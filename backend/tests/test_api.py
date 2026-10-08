"""API regression tests for the InfraWatch backend."""

import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.repository import connect_postgres
from app.schemas import MetricPoint, ServiceMetrics
from app.services.observability import LokiClient, PrometheusClient


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


class FakeHttpResponse:
    """Minimal httpx-like response for Loki tests."""

    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        """Raise an HTTP error for non-success status codes."""

        if self.status_code >= 400:
            raise httpx.HTTPStatusError("loki error", request=httpx.Request("GET", "http://loki"), response=None)

    def json(self) -> dict:
        """Return the fake JSON payload."""

        return self.payload


class FakeAsyncClient:
    """Small async client fake used by Loki tests."""

    payload: dict = {}
    status_code = 200
    raised: Exception | None = None
    calls: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def get(self, url: str, params: dict):
        """Capture the request and return a fake response."""

        self.calls.append({"url": url, "params": params})
        if self.raised:
            raise self.raised
        return FakeHttpResponse(self.payload, status_code=self.status_code)


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


def test_workload_health_uses_stored_state_when_kubectl_disabled(tmp_path) -> None:
    """The workload endpoint should expose replica health without requiring kubectl in demo mode."""

    client = build_client(tmp_path)
    payload = {
        "name": "catalog-api",
        "image": "docker.io/example/catalog-api:latest",
        "replicas": 2,
        "port": 8080,
    }

    assert client.post("/deploy", json=payload).status_code == 202
    response = client.get("/workloads/catalog-api")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "catalog-api"
    assert body["desired_replicas"] == 2
    assert body["ready_replicas"] == 2
    assert body["available_replicas"] == 2
    assert body["unavailable_replicas"] == 0
    assert body["pods"] == []
    assert body["source"] == "stored"


def test_unknown_workload_returns_404(tmp_path) -> None:
    """A missing workload should return a clear 404."""

    client = build_client(tmp_path)
    response = client.get("/workloads/missing-api")

    assert response.status_code == 404


def test_workload_health_reads_live_kubernetes_payload(monkeypatch, tmp_path) -> None:
    """The workload endpoint should expose live Deployment and Pod health when kubectl is enabled."""

    settings = Settings(
        environment="test",
        state_file=str(tmp_path / "deployments.json"),
        audit_file=str(tmp_path / "audit-log.json"),
        execute_kubectl=True,
        allow_mock_observability=True,
    )

    def fake_kubectl(_self, args: list[str], stdin: str | None = None, timeout: int = 45) -> str:
        if "deployment/catalog-api" in args:
            return json.dumps(
                {
                    "spec": {"replicas": 2},
                    "status": {
                        "updatedReplicas": 2,
                        "readyReplicas": 1,
                        "availableReplicas": 1,
                        "unavailableReplicas": 1,
                        "observedGeneration": 7,
                    },
                }
            )
        if "pods" in args:
            return json.dumps(
                {
                    "items": [
                        {
                            "metadata": {"name": "catalog-api-healthy"},
                            "status": {
                                "phase": "Running",
                                "containerStatuses": [{"ready": True, "restartCount": 0}],
                            },
                        },
                        {
                            "metadata": {"name": "catalog-api-waiting"},
                            "status": {
                                "phase": "Pending",
                                "containerStatuses": [
                                    {
                                        "ready": False,
                                        "restartCount": 2,
                                        "state": {
                                            "waiting": {
                                                "reason": "ImagePullBackOff",
                                                "message": "image pull failed",
                                            }
                                        },
                                    }
                                ],
                            },
                        },
                    ]
                }
            )
        raise AssertionError(f"unexpected kubectl args: {args}")

    monkeypatch.setattr("app.services.deployments.DeploymentService._run_kubectl", fake_kubectl)
    client = TestClient(create_app(settings))

    response = client.get("/workloads/catalog-api")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "kubernetes"
    assert body["desired_replicas"] == 2
    assert body["ready_replicas"] == 1
    assert body["available_replicas"] == 1
    assert body["unavailable_replicas"] == 1
    assert body["observed_generation"] == 7
    assert body["pods"][0]["ready"] is True
    assert body["pods"][1]["reason"] == "ImagePullBackOff"


def test_postgres_url_is_parsed_into_connection_kwargs(monkeypatch) -> None:
    """PostgreSQL URLs should be passed to psycopg2 as explicit connection fields."""

    captured: dict[str, object] = {}

    def fake_connect(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("app.repository.psycopg2.connect", fake_connect)

    connect_postgres("postgresql://infrawatch:abc-123@infrawatch-postgres:5432/infrawatch")

    assert captured["args"] == ()
    assert captured["kwargs"] == {
        "dbname": "infrawatch",
        "user": "infrawatch",
        "password": "abc-123",
        "host": "infrawatch-postgres",
        "port": 5432,
    }


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


def loki_payload(*streams: dict) -> dict:
    """Build a Loki query_range payload for tests."""

    return {"status": "success", "data": {"result": list(streams)}}


def test_loki_logs_are_normalized_from_real_stream_labels(monkeypatch) -> None:
    """Loki streams should become frontend-friendly log entries with pod metadata."""

    FakeAsyncClient.payload = loki_payload(
        {
            "stream": {
                "namespace": "infrawatch",
                "app": "infrawatch-backend",
                "pod": "infrawatch-backend-abc",
                "container": "backend",
            },
            "values": [["1700000000000000000", "GET /healthz 200"]],
        }
    )
    FakeAsyncClient.raised = None
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = LokiClient(Settings(environment="test", allow_mock_observability=False))
    logs = asyncio.run(client.logs("infrawatch-backend", limit=25, search="healthz", pod="infrawatch-backend-abc"))

    assert logs.source == "loki"
    assert logs.lines[0].line == "GET /healthz 200"
    assert logs.lines[0].namespace == "infrawatch"
    assert logs.lines[0].pod == "infrawatch-backend-abc"
    assert logs.lines[0].container == "backend"
    assert logs.lines[0].labels["app"] == "infrawatch-backend"
    params = FakeAsyncClient.calls[0]["params"]
    assert params["limit"] == 25
    assert 'app="infrawatch-backend"' in params["query"]
    assert 'pod="infrawatch-backend-abc"' in params["query"]
    assert '|= "healthz"' in params["query"]
    assert params["end"] > params["start"]


def test_loki_empty_response_stays_empty(monkeypatch) -> None:
    """An empty Loki response should not be replaced with fake logs."""

    FakeAsyncClient.payload = loki_payload()
    FakeAsyncClient.raised = None
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = LokiClient(Settings(environment="test", allow_mock_observability=True))
    logs = asyncio.run(client.logs("infrawatch-backend"))

    assert logs.source == "loki"
    assert logs.lines == []


def test_loki_http_errors_surface_when_not_in_demo(monkeypatch) -> None:
    """Loki failures should be reported instead of silently becoming fake logs."""

    FakeAsyncClient.payload = {}
    FakeAsyncClient.raised = httpx.ConnectTimeout("timeout")
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = LokiClient(Settings(environment="development", allow_mock_observability=True))

    try:
        asyncio.run(client.logs("infrawatch-backend"))
    except httpx.ConnectTimeout:
        pass
    else:
        raise AssertionError("Loki timeout should be surfaced outside explicit demo/test contexts")


def test_loki_malformed_response_surfaces_when_not_in_demo(monkeypatch) -> None:
    """Malformed Loki payloads should not be hidden as real logs."""

    FakeAsyncClient.payload = {"unexpected": "shape"}
    FakeAsyncClient.raised = None
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = LokiClient(Settings(environment="development", allow_mock_observability=True))

    try:
        asyncio.run(client.logs("infrawatch-backend"))
    except KeyError:
        pass
    else:
        raise AssertionError("Malformed Loki responses should be surfaced outside demo/test contexts")


def test_loki_demo_fallback_is_explicitly_marked_mock(monkeypatch) -> None:
    """Demo fallback logs must never claim to come from Loki."""

    FakeAsyncClient.payload = {}
    FakeAsyncClient.raised = httpx.ConnectError("loki unavailable")
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = LokiClient(Settings(environment="demo", allow_mock_observability=True))
    logs = asyncio.run(client.logs("catalog-api"))

    assert logs.source == "mock"
    assert logs.lines


def test_loki_rejects_unsafe_label_filters() -> None:
    """Unsafe service/pod labels should be rejected before a LogQL query is built."""

    client = LokiClient(Settings(environment="test", allow_mock_observability=False))

    try:
        asyncio.run(client.logs("bad/service"))
    except ValueError:
        pass
    else:
        raise AssertionError("Unsafe Loki label values must be rejected")


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
