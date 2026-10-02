"""API regression tests for the InfraWatch backend."""

import asyncio

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import MetricPoint, ServiceMetrics
from app.services.observability import PrometheusClient


class FakeRedis:
    """Small async Redis fake used by cache tests."""

    def __init__(self, initial: str | None = None, *, fail_get: bool = False, fail_set: bool = False) -> None:
        self.initial = initial
        self.fail_get = fail_get
        self.fail_set = fail_set
        self.values: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []

    async def get(self, key: str) -> str | None:
        """Return a cached value or simulate Redis being unavailable."""

        if self.fail_get:
            raise ConnectionError("redis unavailable")
        return self.values.get(key, self.initial)

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        """Store a value with the requested TTL or simulate a write failure."""

        if self.fail_set:
            raise TimeoutError("redis write timeout")
        self.values[key] = value
        self.setex_calls.append((key, ttl, value))
        return True


def build_client(tmp_path) -> TestClient:
    """Create a test client with isolated JSON state."""

    settings = Settings(
        environment="test",
        state_file=str(tmp_path / "deployments.json"),
        audit_file=str(tmp_path / "audit-log.json"),
        execute_kubectl=False,
        allow_mock_observability=True,
    )
    return TestClient(create_app(settings))


def test_deployment_lifecycle(tmp_path) -> None:
    """A service can be deployed, listed, observed, and deleted."""

    client = build_client(tmp_path)
    payload = {
        "name": "catalog-api",
        "image": "docker.io/example/catalog-api:latest",
        "replicas": 2,
        "port": 8080,
        "environment": {"ENVIRONMENT": "test"},
    }

    deploy_response = client.post("/deploy", json=payload)
    assert deploy_response.status_code == 202
    assert deploy_response.json()["deployment"]["status"] == "Running"

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["fastapi"] is True
    assert health.json()["deployment_mode"] == "manifest-simulation"

    deployments = client.get("/deployments")
    assert deployments.status_code == 200
    assert deployments.json()[0]["name"] == "catalog-api"

    metrics = client.get("/metrics/catalog-api")
    assert metrics.status_code == 200
    assert metrics.json()["source"] == "mock"
    assert len(metrics.json()["cpu_cores"]) == 15
    assert all(point["value"] >= 0 for point in metrics.json()["error_rate"])

    logs = client.get("/logs/catalog-api")
    assert logs.status_code == 200
    assert logs.json()["lines"]
    assert len({entry["line"] for entry in logs.json()["lines"]}) >= 5
    assert any("[warn]" in entry["line"] for entry in logs.json()["lines"])

    deleted = client.delete("/deployment/catalog-api")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"

    audit_logs = client.get("/audit-logs")
    assert audit_logs.status_code == 200
    events = audit_logs.json()
    assert events[0]["action"] == "deployment.deleted"
    assert events[0]["service"] == "catalog-api"
    assert events[1]["action"] == "deployment.simulated"


def test_unknown_deployment_delete_returns_404(tmp_path) -> None:
    """Deleting a missing deployment returns a correct HTTP error."""

    client = build_client(tmp_path)
    response = client.delete("/deployment/missing-api")
    assert response.status_code == 404

    audit_logs = client.get("/audit-logs")
    assert audit_logs.status_code == 200
    assert audit_logs.json()[0]["action"] == "deployment.delete_missing"


def test_demo_rollback_is_audited_without_kubectl(tmp_path) -> None:
    """Rollback is safe in demo mode and records that no cluster action happened."""

    client = build_client(tmp_path)
    payload = {
        "name": "rollback-api",
        "image": "docker.io/example/rollback-api:latest",
        "replicas": 1,
        "port": 8080,
    }

    assert client.post("/deploy", json=payload).status_code == 202
    rollback = client.post("/deployment/rollback-api/rollback")
    assert rollback.status_code == 200
    assert rollback.json()["name"] == "rollback-api"
    assert "Demo Mode" in rollback.json()["message"]

    audit_logs = client.get("/audit-logs")
    assert audit_logs.status_code == 200
    assert audit_logs.json()[0]["action"] == "deployment.rollback_simulated"


def test_demo_runtime_can_seed_sample_services(tmp_path) -> None:
    """Demo runtimes start with useful sample data when explicitly enabled."""

    settings = Settings(
        environment="demo",
        state_file=str(tmp_path / "deployments.json"),
        audit_file=str(tmp_path / "audit-log.json"),
        execute_kubectl=False,
        seed_demo_data=True,
        allow_mock_observability=True,
    )
    client = TestClient(create_app(settings))

    deployments = client.get("/deployments")
    assert deployments.status_code == 200
    assert {item["name"] for item in deployments.json()} == {
        "catalog-api",
        "checkout-api",
        "payments-worker",
    }


def test_empty_prometheus_series_falls_back_to_mock_metrics() -> None:
    """A reachable but empty Prometheus should not leave demo dashboards blank."""

    settings = Settings(environment="test", allow_mock_observability=True)
    client = PrometheusClient(settings)

    async def empty_query(_query: str, end: int | None = None):
        return []

    client._query_range = empty_query  # type: ignore[method-assign]

    metrics = asyncio.run(client.service_metrics("catalog-api"))
    assert metrics.source == "mock"
    assert len(metrics.cpu_cores) == 15


def test_empty_prometheus_error_series_means_zero_errors_not_mock_metrics() -> None:
    """No 5xx samples is a healthy zero-error state, not missing telemetry."""

    settings = Settings(environment="test", allow_mock_observability=True)
    client = PrometheusClient(settings)
    points = [MetricPoint(timestamp=100 + index, value=float(index + 1)) for index in range(3)]

    async def mostly_live_query(query: str, end: int | None = None):
        if 'status=~"5.."' in query:
            return []
        return points

    client._query_range = mostly_live_query  # type: ignore[method-assign]

    metrics = asyncio.run(client.service_metrics("infrawatch-backend"))
    assert metrics.source == "prometheus"
    assert [point.value for point in metrics.error_rate] == [0.0, 0.0, 0.0]
    assert [point.timestamp for point in metrics.error_rate] == [100, 101, 102]


def test_prometheus_metrics_cache_miss_queries_prometheus_and_stores_response() -> None:
    """A cache miss should query Prometheus and store only the real Prometheus response."""

    settings = Settings(environment="test", allow_mock_observability=True, redis_url="redis://test", redis_cache_ttl=5)
    redis = FakeRedis()
    client = PrometheusClient(settings, redis_client=redis)
    points = [MetricPoint(timestamp=100 + index, value=float(index + 1)) for index in range(3)]
    calls: list[str] = []

    async def live_query(query: str, end: int | None = None):
        calls.append(query)
        return [] if 'status=~"5.."' in query else points

    client._query_range = live_query  # type: ignore[method-assign]

    metrics = asyncio.run(client.service_metrics("catalog-api"))

    assert metrics.source == "prometheus"
    assert len(calls) == 4
    assert len(redis.setex_calls) == 1
    assert redis.setex_calls[0][1] == 5
    assert ServiceMetrics.model_validate_json(redis.setex_calls[0][2]).source == "prometheus"


def test_prometheus_metrics_cache_hit_skips_prometheus() -> None:
    """A cache hit should return the cached response without calling Prometheus."""

    cached = ServiceMetrics(
        service="catalog-api",
        cpu_cores=[MetricPoint(timestamp=100, value=0.2)],
        memory_megabytes=[MetricPoint(timestamp=100, value=128)],
        request_rate=[MetricPoint(timestamp=100, value=12)],
        error_rate=[MetricPoint(timestamp=100, value=0)],
        source="prometheus",
    )
    settings = Settings(environment="test", allow_mock_observability=True, redis_url="redis://test", redis_cache_ttl=5)
    client = PrometheusClient(settings, redis_client=FakeRedis(initial=cached.model_dump_json()))

    async def should_not_query(_query: str, end: int | None = None):
        raise AssertionError("Prometheus should not be queried on cache hit")

    client._query_range = should_not_query  # type: ignore[method-assign]

    metrics = asyncio.run(client.service_metrics("catalog-api"))

    assert metrics == cached


def test_prometheus_metrics_cache_uses_configured_ttl() -> None:
    """Cached metrics should use the short configured TTL."""

    settings = Settings(environment="test", allow_mock_observability=True, redis_url="redis://test", redis_cache_ttl=9)
    redis = FakeRedis()
    client = PrometheusClient(settings, redis_client=redis)
    points = [MetricPoint(timestamp=100 + index, value=float(index + 1)) for index in range(3)]

    async def live_query(query: str, end: int | None = None):
        return [] if 'status=~"5.."' in query else points

    client._query_range = live_query  # type: ignore[method-assign]

    asyncio.run(client.service_metrics("payments-api"))

    assert redis.setex_calls
    assert redis.setex_calls[0][1] == 9


def test_prometheus_metrics_continue_when_redis_is_unavailable() -> None:
    """Redis errors are bypassed so Prometheus remains available as the source of truth."""

    settings = Settings(environment="test", allow_mock_observability=True, redis_url="redis://test", redis_cache_ttl=5)
    client = PrometheusClient(settings, redis_client=FakeRedis(fail_get=True, fail_set=True))
    points = [MetricPoint(timestamp=100 + index, value=float(index + 1)) for index in range(3)]
    calls: list[str] = []

    async def live_query(query: str, end: int | None = None):
        calls.append(query)
        return [] if 'status=~"5.."' in query else points

    client._query_range = live_query  # type: ignore[method-assign]

    metrics = asyncio.run(client.service_metrics("checkout-api"))

    assert metrics.source == "prometheus"
    assert len(calls) == 4


def test_mock_metrics_are_not_cached() -> None:
    """Mock fallback data should not be cached as if it were Prometheus truth."""

    settings = Settings(environment="test", allow_mock_observability=True, redis_url="redis://test", redis_cache_ttl=5)
    redis = FakeRedis()
    client = PrometheusClient(settings, redis_client=redis)

    async def empty_query(_query: str, end: int | None = None):
        return []

    client._query_range = empty_query  # type: ignore[method-assign]

    metrics = asyncio.run(client.service_metrics("catalog-api"))

    assert metrics.source == "mock"
    assert redis.setex_calls == []
