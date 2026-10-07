"""Deployment orchestration service.

This service translates InfraWatch deployment requests into Kubernetes
Deployment and Service manifests, then optionally applies them with kubectl.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import yaml

from app.config import Settings
from app.repository import AuditLogRepository, DeploymentRepository, utc_now
from app.schemas import (
    DeploymentRecord,
    DeploymentRequest,
    DeploymentResponse,
    DeploymentStatus,
    PodHealth,
    WorkloadHealth,
)


class DeploymentExecutionError(RuntimeError):
    """Raised when kubectl fails to apply or delete a workload."""


class DeploymentService:
    """Coordinates deployment persistence and Kubernetes operations."""

    def __init__(
        self,
        settings: Settings,
        repository: DeploymentRepository,
        audit_repository: AuditLogRepository,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._audit_repository = audit_repository

    def list_deployments(self) -> list[DeploymentRecord]:
        """List deployments known by InfraWatch."""

        return self._repository.list()

    def list_audit_logs(self, limit: int = 100):
        """List newest audit log entries."""

        return self._audit_repository.list(limit=limit)

    def workload_health(self, name: str) -> WorkloadHealth | None:
        """Return read-only Kubernetes Deployment and Pod health for one known service."""

        existing = self._repository.get(name)
        namespace = existing.namespace if existing else self._settings.kubectl_namespace
        if not self._settings.execute_kubectl:
            if existing is None:
                return None
            return WorkloadHealth(
                service=existing.name,
                namespace=existing.namespace,
                desired_replicas=existing.replicas,
                updated_replicas=existing.ready_replicas or existing.available_replicas,
                ready_replicas=existing.ready_replicas,
                available_replicas=existing.available_replicas,
                unavailable_replicas=max(existing.replicas - existing.available_replicas, 0),
                observed_generation=existing.observed_generation,
                pods=[],
                source="stored",
            )

        try:
            deployment = self._read_deployment_payload(name, namespace)
        except DeploymentExecutionError:
            if existing is None:
                return None
            raise
        spec = deployment.get("spec", {})
        status = deployment.get("status", {})
        desired = int(spec.get("replicas", 0) or 0)
        updated = int(status.get("updatedReplicas", 0) or 0)
        ready = int(status.get("readyReplicas", 0) or 0)
        available = int(status.get("availableReplicas", 0) or 0)
        unavailable = int(status.get("unavailableReplicas", max(desired - available, 0)) or 0)
        return WorkloadHealth(
            service=name,
            namespace=namespace,
            desired_replicas=desired,
            updated_replicas=updated,
            ready_replicas=ready,
            available_replicas=available,
            unavailable_replicas=unavailable,
            observed_generation=status.get("observedGeneration"),
            pods=self._read_pod_health(name, namespace),
            source="kubernetes",
        )

    def deploy(self, request: DeploymentRequest) -> DeploymentResponse:
        """Create or update Kubernetes resources for a service."""

        namespace = request.namespace or self._settings.kubectl_namespace
        now = utc_now()
        existing = self._repository.get(request.name)
        manifest = self._build_manifest(request, namespace)

        status = DeploymentStatus.deploying if self._settings.execute_kubectl else DeploymentStatus.running
        message = "Deployment accepted; Kubernetes rollout verification is starting."
        if not self._settings.execute_kubectl:
            message = "Kubernetes manifest generated; kubectl execution is disabled in Demo Mode."

        record = DeploymentRecord(
            name=request.name,
            image=request.image,
            namespace=namespace,
            replicas=request.replicas,
            port=request.port,
            status=status,
            url=f"http://{request.name}.{namespace}.svc.cluster.local:{request.port}",
            commit_sha=request.commit_sha,
            ready_replicas=request.replicas if not self._settings.execute_kubectl else 0,
            available_replicas=request.replicas if not self._settings.execute_kubectl else 0,
            observed_generation=None,
            last_failure=None,
            message=message,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._repository.upsert(record)
        self._audit_repository.append(
            action="deployment.simulated" if not self._settings.execute_kubectl else "deployment.requested",
            service=record.name,
            status="success",
            message=record.message,
            metadata={
                "image": record.image,
                "namespace": record.namespace,
                "replicas": record.replicas,
                "port": record.port,
                "mode": "kubernetes" if self._settings.execute_kubectl else "manifest-simulation",
            },
        )

        if self._settings.execute_kubectl:
            record = self._apply_and_verify_rollout(record, manifest)

        return DeploymentResponse(deployment=record, kubernetes_manifest=manifest)

    def delete(self, name: str) -> DeploymentRecord | None:
        """Remove a service from Kubernetes and the local state store."""

        existing = self._repository.get(name)
        if existing is None:
            self._audit_repository.append(
                action="deployment.delete_missing",
                service=name,
                status="not_found",
                message="Delete requested for a deployment that does not exist.",
            )
            return None

        existing.status = DeploymentStatus.deleting
        existing.message = "Deployment deletion requested."
        existing.updated_at = utc_now()
        self._repository.upsert(existing)

        if self._settings.execute_kubectl:
            try:
                self._kubectl_delete(existing.name, existing.namespace)
            except DeploymentExecutionError:
                self._audit_repository.append(
                    action="deployment.delete_failed",
                    service=existing.name,
                    status="failure",
                    message="Kubernetes delete failed.",
                    metadata={"namespace": existing.namespace},
                )
                raise

        deleted = self._repository.delete(name)
        self._audit_repository.append(
            action="deployment.deleted",
            service=existing.name,
            status="success",
            message="Deployment removed from InfraWatch state.",
            metadata={"namespace": existing.namespace, "image": existing.image},
        )
        return deleted

    def rollback(self, name: str) -> DeploymentRecord | None:
        """Rollback a Kubernetes deployment to the previous ReplicaSet revision."""

        existing = self._repository.get(name)
        if existing is None:
            self._audit_repository.append(
                action="deployment.rollback_missing",
                service=name,
                status="not_found",
                message="Rollback requested for a deployment that does not exist.",
            )
            return None

        if not self._settings.execute_kubectl:
            self._audit_repository.append(
                action="deployment.rollback_simulated",
                service=existing.name,
                status="skipped",
                message="Rollback requires real Kubernetes execution; demo mode left deployment unchanged.",
                metadata={"namespace": existing.namespace},
            )
            existing.message = "Rollback skipped because Kubernetes execution is disabled in Demo Mode."
            existing.updated_at = utc_now()
            return self._repository.upsert(existing)

        existing.status = DeploymentStatus.deploying
        existing.message = "Rollback requested; waiting for Kubernetes rollout undo."
        existing.last_failure = None
        existing.updated_at = utc_now()
        self._repository.upsert(existing)

        try:
            self._kubectl_rollout_undo(existing.name, existing.namespace)
            self._kubectl_rollout_status(existing.name, existing.namespace)
            rollout_state = self._read_deployment_state(existing.name, existing.namespace)
            existing.status = DeploymentStatus.running
            existing.ready_replicas = rollout_state["ready_replicas"]
            existing.available_replicas = rollout_state["available_replicas"]
            existing.observed_generation = rollout_state["observed_generation"]
            existing.message = (
                "Rollback completed and rollout verified: "
                f"{existing.ready_replicas}/{existing.replicas} replicas are Ready."
            )
            existing.updated_at = utc_now()
            self._repository.upsert(existing)
            self._audit_repository.append(
                action="deployment.rollback_verified",
                service=existing.name,
                status="success",
                message=existing.message,
                metadata={"namespace": existing.namespace, "image": existing.image},
            )
        except DeploymentExecutionError as exc:
            detail = str(exc)
            try:
                pod_detail = self._pod_failure_summary(existing.name, existing.namespace)
                if pod_detail:
                    detail = f"{detail}; pod detail: {pod_detail}"
            except DeploymentExecutionError:
                pass
            existing.status = DeploymentStatus.failed
            existing.last_failure = detail
            existing.message = f"Rollback failed: {detail}"
            existing.updated_at = utc_now()
            self._repository.upsert(existing)
            self._audit_repository.append(
                action="deployment.rollback_failed",
                service=existing.name,
                status="failure",
                message=existing.message,
                metadata={"namespace": existing.namespace, "image": existing.image},
            )

        return existing

    def _apply_and_verify_rollout(self, record: DeploymentRecord, manifest: dict[str, Any]) -> DeploymentRecord:
        """Apply Kubernetes resources and mark success only after rollout verification."""

        try:
            self._kubectl_apply(manifest)
            self._audit_repository.append(
                action="deployment.applied",
                service=record.name,
                status="success",
                message="Kubernetes manifest applied; waiting for rollout.",
                metadata={"namespace": record.namespace, "image": record.image},
            )

            self._kubectl_rollout_status(record.name, record.namespace)
            rollout_state = self._read_deployment_state(record.name, record.namespace)
            ready_replicas = rollout_state["ready_replicas"]
            available_replicas = rollout_state["available_replicas"]

            if ready_replicas < record.replicas or available_replicas < record.replicas:
                pod_detail = self._pod_failure_summary(record.name, record.namespace)
                raise DeploymentExecutionError(
                    pod_detail
                    or (
                        "Rollout finished but readiness verification failed: "
                        f"{ready_replicas}/{record.replicas} ready, "
                        f"{available_replicas}/{record.replicas} available."
                    )
                )

            record.status = DeploymentStatus.running
            record.ready_replicas = ready_replicas
            record.available_replicas = available_replicas
            record.observed_generation = rollout_state["observed_generation"]
            record.last_failure = None
            record.message = (
                "Kubernetes rollout verified: "
                f"{ready_replicas}/{record.replicas} replicas are Ready and available."
            )
            record.updated_at = utc_now()
            self._repository.upsert(record)
            self._audit_repository.append(
                action="deployment.rollout_verified",
                service=record.name,
                status="success",
                message=record.message,
                metadata={
                    "namespace": record.namespace,
                    "image": record.image,
                    "ready_replicas": ready_replicas,
                    "available_replicas": available_replicas,
                },
            )
            return record
        except DeploymentExecutionError as exc:
            failure_detail = str(exc)
            try:
                pod_detail = self._pod_failure_summary(record.name, record.namespace)
                if pod_detail:
                    failure_detail = f"{failure_detail}; pod detail: {pod_detail}"
            except DeploymentExecutionError:
                pass
            record.status = DeploymentStatus.failed
            record.ready_replicas = 0
            record.available_replicas = 0
            record.last_failure = failure_detail
            record.message = f"Kubernetes rollout failed: {failure_detail}"
            record.updated_at = utc_now()
            try:
                rollout_state = self._read_deployment_state(record.name, record.namespace)
                record.ready_replicas = rollout_state["ready_replicas"]
                record.available_replicas = rollout_state["available_replicas"]
                record.observed_generation = rollout_state["observed_generation"]
            except DeploymentExecutionError:
                pass
            self._repository.upsert(record)
            self._audit_repository.append(
                action="deployment.rollout_failed",
                service=record.name,
                status="failure",
                message=record.message,
                metadata={
                    "namespace": record.namespace,
                    "image": record.image,
                    "ready_replicas": record.ready_replicas,
                    "available_replicas": record.available_replicas,
                },
            )
            return record

    def _build_manifest(self, request: DeploymentRequest, namespace: str) -> dict[str, Any]:
        """Build a Kubernetes List manifest for the service workload."""

        labels = {
            "app.kubernetes.io/name": request.name,
            "app.kubernetes.io/part-of": "infrawatch-managed",
            **request.labels,
        }
        environment = {"INFRAWATCH_SERVICE_NAME": request.name, **request.environment}
        env = [{"name": key, "value": value} for key, value in sorted(environment.items())]

        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": request.name, "namespace": namespace, "labels": labels},
            "spec": {
                "replicas": request.replicas,
                "progressDeadlineSeconds": self._settings.rollout_timeout_seconds,
                "selector": {"matchLabels": {"app.kubernetes.io/name": request.name}},
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0},
                },
                "template": {
                    "metadata": {
                        "labels": labels,
                        "annotations": {
                            "prometheus.io/scrape": "true",
                            "prometheus.io/path": "/metrics",
                            "prometheus.io/port": str(request.port),
                        },
                    },
                    "spec": {
                        "containers": [
                            {
                                "name": request.name,
                                "image": request.image,
                                "imagePullPolicy": "IfNotPresent",
                                "ports": [{"containerPort": request.port, "name": "http"}],
                                "env": env,
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "128Mi"},
                                    "limits": {"cpu": "500m", "memory": "512Mi"},
                                },
                                "readinessProbe": {
                                    "httpGet": {"path": "/healthz", "port": "http"},
                                    "initialDelaySeconds": 10,
                                    "periodSeconds": 10,
                                },
                                "livenessProbe": {
                                    "httpGet": {"path": "/healthz", "port": "http"},
                                    "initialDelaySeconds": 20,
                                    "periodSeconds": 20,
                                },
                            }
                        ]
                    },
                },
            },
        }

        service = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": request.name, "namespace": namespace, "labels": labels},
            "spec": {
                "selector": {"app.kubernetes.io/name": request.name},
                "ports": [{"port": request.port, "targetPort": "http", "name": "http"}],
            },
        }

        return {"apiVersion": "v1", "kind": "List", "items": [deployment, service]}

    def _kubectl_apply(self, manifest: dict[str, Any]) -> None:
        """Apply a manifest by streaming YAML to kubectl."""

        self._run_kubectl(["apply", "-f", "-"], stdin=yaml.safe_dump(manifest))

    def _kubectl_rollout_status(self, name: str, namespace: str) -> None:
        """Wait for Kubernetes Deployment rollout completion."""

        self._run_kubectl(
            [
                "rollout",
                "status",
                f"deployment/{name}",
                "--namespace",
                namespace,
                f"--timeout={self._settings.rollout_timeout_seconds}s",
            ],
            timeout=self._settings.rollout_timeout_seconds + 15,
        )

    def _kubectl_rollout_undo(self, name: str, namespace: str) -> None:
        """Rollback a Kubernetes Deployment to its previous revision."""

        self._run_kubectl(
            [
                "rollout",
                "undo",
                f"deployment/{name}",
                "--namespace",
                namespace,
            ]
        )

    def _read_deployment_state(self, name: str, namespace: str) -> dict[str, int | None]:
        """Read Kubernetes Deployment status fields used by the dashboard."""

        payload = self._read_deployment_payload(name, namespace)
        status = payload.get("status", {})
        return {
            "ready_replicas": int(status.get("readyReplicas", 0)),
            "available_replicas": int(status.get("availableReplicas", 0)),
            "observed_generation": status.get("observedGeneration"),
        }

    def _read_deployment_payload(self, name: str, namespace: str) -> dict[str, Any]:
        """Read the raw Kubernetes Deployment payload for a service."""

        output = self._run_kubectl(
            [
                "get",
                f"deployment/{name}",
                "--namespace",
                namespace,
                "-o",
                "json",
            ]
        )
        return json.loads(output)

    def _read_pod_health(self, name: str, namespace: str) -> list[PodHealth]:
        """Read pod phase/readiness/restart details for one service."""

        output = self._run_kubectl(
            [
                "get",
                "pods",
                "--namespace",
                namespace,
                "-l",
                f"app.kubernetes.io/name={name}",
                "-o",
                "json",
            ]
        )
        payload = json.loads(output)
        pods: list[PodHealth] = []
        for item in payload.get("items", []):
            metadata = item.get("metadata", {})
            status = item.get("status", {})
            container_statuses = status.get("containerStatuses", [])
            ready = bool(container_statuses) and all(bool(container.get("ready")) for container in container_statuses)
            restart_count = sum(int(container.get("restartCount", 0) or 0) for container in container_statuses)
            reason = status.get("reason")
            message = status.get("message")
            for container in container_statuses:
                state = container.get("state", {})
                waiting = state.get("waiting")
                terminated = state.get("terminated")
                if waiting:
                    reason = waiting.get("reason", reason)
                    message = waiting.get("message", message)
                elif terminated:
                    reason = terminated.get("reason", reason)
                    message = terminated.get("message", message)
            pods.append(
                PodHealth(
                    name=metadata.get("name", "unknown-pod"),
                    phase=status.get("phase", "Unknown"),
                    ready=ready,
                    restart_count=restart_count,
                    reason=reason,
                    message=message,
                )
            )
        return pods

    def _pod_failure_summary(self, name: str, namespace: str) -> str:
        """Inspect pods for common rollout failure reasons."""

        output = self._run_kubectl(
            [
                "get",
                "pods",
                "--namespace",
                namespace,
                "-l",
                f"app.kubernetes.io/name={name}",
                "-o",
                "json",
            ]
        )
        payload = json.loads(output)
        reasons: list[str] = []
        for item in payload.get("items", []):
            pod_name = item.get("metadata", {}).get("name", "unknown-pod")
            for container in item.get("status", {}).get("containerStatuses", []):
                state = container.get("state", {})
                waiting = state.get("waiting")
                terminated = state.get("terminated")
                if waiting:
                    reason = waiting.get("reason", "Waiting")
                    message = waiting.get("message", "")
                    reasons.append(f"{pod_name}: {reason} {message}".strip())
                elif terminated:
                    reason = terminated.get("reason", "Terminated")
                    reasons.append(f"{pod_name}: {reason}")
        return "; ".join(reasons)

    def _kubectl_delete(self, name: str, namespace: str) -> None:
        """Delete workload resources for one service."""

        self._run_kubectl(
            [
                "delete",
                f"deployment/{name}",
                f"service/{name}",
                "--namespace",
                namespace,
                "--ignore-not-found=true",
            ]
        )

    def _run_kubectl(self, args: list[str], stdin: str | None = None, timeout: int = 45) -> str:
        """Execute kubectl and surface a clean domain-specific error."""

        command = [self._settings.kubectl_binary, *args]
        result = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "kubectl failed"
            raise DeploymentExecutionError(detail)
        return result.stdout
