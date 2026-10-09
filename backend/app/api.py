"""HTTP routes for the InfraWatch REST API.

Routes are intentionally thin and delegate orchestration to services stored on
FastAPI application state.
"""

from fastapi import APIRouter, HTTPException, Query, Request, status
from prometheus_client import Counter, Histogram

from app.schemas import (
    AlertsResponse,
    AuditLogEntry,
    DeploymentRecord,
    DeploymentRequest,
    DeploymentResponse,
    LogsResponse,
    ServiceMetrics,
    WorkloadHealth,
)
from app.services.deployments import DeploymentExecutionError

DEPLOYMENT_COUNTER = Counter("infrawatch_deployments_total", "Deployment actions accepted by InfraWatch")
REQUEST_TIMER = Histogram("infrawatch_api_request_seconds", "InfraWatch API route execution time", ["route"])


def build_router() -> APIRouter:
    """Build the API router with all public InfraWatch endpoints."""

    router = APIRouter()

    @router.get("/healthz", tags=["system"])
    async def healthz(request: Request) -> dict[str, str | bool]:
        """Return health and an honest summary of active platform capabilities."""

        settings = request.app.state.settings
        return {
            "status": "ok",
            "environment": settings.environment,
            "fastapi": True,
            "kubernetes_execution": settings.execute_kubectl,
            "deployment_mode": "kubernetes" if settings.execute_kubectl else "manifest-simulation",
            "observability_mode": "prometheus-loki" if not settings.allow_mock_observability else "fallback-enabled",
            "persistence_mode": "postgresql" if settings.database_url else "json-file",
        }

    @router.post(
        "/deploy",
        response_model=DeploymentResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["deployments"],
    )
    async def deploy(payload: DeploymentRequest, request: Request) -> DeploymentResponse:
        """Trigger or update a service deployment."""

        with REQUEST_TIMER.labels(route="/deploy").time():
            try:
                response = request.app.state.deployment_service.deploy(payload)
                DEPLOYMENT_COUNTER.inc()
                return response
            except DeploymentExecutionError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    @router.get("/deployments", tags=["deployments"])
    async def deployments(request: Request):
        """List all deployments known to InfraWatch."""

        with REQUEST_TIMER.labels(route="/deployments").time():
            return request.app.state.deployment_service.list_deployments()

    @router.get("/audit-logs", response_model=list[AuditLogEntry], tags=["audit"])
    async def audit_logs(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[AuditLogEntry]:
        """List recent operational actions recorded by InfraWatch."""

        with REQUEST_TIMER.labels(route="/audit-logs").time():
            return request.app.state.deployment_service.list_audit_logs(limit=limit)

    @router.get("/metrics/{service}", response_model=ServiceMetrics, tags=["observability"])
    async def metrics(service: str, request: Request) -> ServiceMetrics:
        """Return service-level Prometheus metrics for dashboard charts."""

        with REQUEST_TIMER.labels(route="/metrics/{service}").time():
            try:
                return await request.app.state.prometheus_client.service_metrics(service)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Unable to read metrics from Prometheus",
                ) from exc

    @router.get("/workloads/{service}", response_model=WorkloadHealth, tags=["observability"])
    async def workload(service: str, request: Request) -> WorkloadHealth:
        """Return Kubernetes Deployment and Pod health for dashboard workload cards."""

        with REQUEST_TIMER.labels(route="/workloads/{service}").time():
            try:
                workload_health = request.app.state.deployment_service.workload_health(service)
            except DeploymentExecutionError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
            if workload_health is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workload not found")
            return workload_health

    @router.get("/logs/{service}", response_model=LogsResponse, tags=["observability"])
    async def logs(
        service: str,
        request: Request,
        search: str | None = Query(default=None, max_length=200),
        pod: str | None = Query(default=None, min_length=1, max_length=253),
        container: str | None = Query(default=None, min_length=1, max_length=253),
        minutes: int = Query(default=15, ge=1, le=60),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> LogsResponse:
        """Return recent Loki log lines for a service."""

        with REQUEST_TIMER.labels(route="/logs/{service}").time():
            try:
                return await request.app.state.loki_client.logs(
                    service,
                    limit=limit,
                    minutes=minutes,
                    search=search,
                    pod=pod,
                    container=container,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Unable to read logs from Loki",
                ) from exc

    @router.get("/alerts", response_model=AlertsResponse, tags=["observability"])
    async def alerts(request: Request) -> AlertsResponse:
        """Return normalized Alertmanager alert state for the dashboard."""

        with REQUEST_TIMER.labels(route="/alerts").time():
            try:
                return await request.app.state.alertmanager_client.alerts()
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Unable to read alerts from Alertmanager",
                ) from exc

    @router.delete("/deployment/{name}", status_code=status.HTTP_200_OK, tags=["deployments"])
    async def delete_deployment(name: str, request: Request) -> dict[str, str]:
        """Tear down a deployment and remove its InfraWatch state."""

        with REQUEST_TIMER.labels(route="/deployment/{name}").time():
            try:
                deleted = request.app.state.deployment_service.delete(name)
            except DeploymentExecutionError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
            if deleted is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
            return {"status": "deleted", "name": name}

    @router.post("/deployment/{name}/rollback", response_model=DeploymentRecord, tags=["deployments"])
    async def rollback_deployment(name: str, request: Request) -> DeploymentRecord:
        """Rollback a deployment with Kubernetes rollout undo."""

        with REQUEST_TIMER.labels(route="/deployment/{name}/rollback").time():
            try:
                rolled_back = request.app.state.deployment_service.rollback(name)
            except DeploymentExecutionError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
            if rolled_back is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
            return rolled_back

    return router
