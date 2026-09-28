"""API regression tests for the InfraWatch backend."""

import asyncio

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import MetricPoint
from app.services.observability import PrometheusClient


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

    async def empty_query(_query: str):
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

    async def mostly_live_query(query: str):
        if 'status=~"5.."' in query:
            return []
        return points

    client._query_range = mostly_live_query  # type: ignore[method-assign]

    metrics = asyncio.run(client.service_metrics("infrawatch-backend"))
    assert metrics.source == "prometheus"
    assert [point.value for point in metrics.error_rate] == [0.0, 0.0, 0.0]
    assert [point.timestamp for point in metrics.error_rate] == [100, 101, 102]
