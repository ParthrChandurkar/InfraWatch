"""Pydantic schemas shared by API routes and service classes.

The models define InfraWatch's public REST contract for deployments, metrics,
and log responses.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class DeploymentStatus(str, Enum):
    """Supported lifecycle states for a deployment."""

    running = "Running"
    failed = "Failed"
    pending = "Pending"
    deploying = "Deploying"
    deleting = "Deleting"
    deleted = "Deleted"


class DeploymentRequest(BaseModel):
    """Payload used to deploy a service through InfraWatch."""

    name: str = Field(..., min_length=2, max_length=63, pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    image: str = Field(..., min_length=3, max_length=255)
    namespace: str | None = Field(default=None, min_length=2, max_length=63)
    replicas: int = Field(default=1, ge=1, le=10)
    port: int = Field(default=8000, ge=1, le=65535)
    environment: dict[str, str] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    commit_sha: str | None = Field(default=None, max_length=64)

    @field_validator("environment", "labels")
    @classmethod
    def validate_flat_string_map(cls, value: dict[str, str]) -> dict[str, str]:
        """Keep Kubernetes metadata and environment values simple and safe."""

        for key, item in value.items():
            if not key or len(key) > 128:
                raise ValueError("map keys must be between 1 and 128 characters")
            if not isinstance(item, str):
                raise ValueError("map values must be strings")
        return value


class DeploymentRecord(BaseModel):
    """Deployment state returned by the API and persisted locally."""

    name: str
    image: str
    namespace: str
    replicas: int
    port: int
    status: DeploymentStatus
    url: str | None = None
    commit_sha: str | None = None
    ready_replicas: int = 0
    available_replicas: int = 0
    observed_generation: int | None = None
    last_failure: str | None = None
    message: str
    created_at: datetime
    updated_at: datetime


class DeploymentResponse(BaseModel):
    """Response returned after a deployment action is accepted."""

    deployment: DeploymentRecord
    kubernetes_manifest: dict[str, Any]


class AuditLogEntry(BaseModel):
    """Immutable operational event recorded by InfraWatch."""

    id: str
    action: str
    service: str | None = None
    actor: str = "system"
    status: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MetricPoint(BaseModel):
    """Single time-series point returned to the React dashboard."""

    timestamp: int
    value: float


class ServiceMetrics(BaseModel):
    """Prometheus-derived metrics for one service."""

    service: str
    cpu_cores: list[MetricPoint]
    memory_megabytes: list[MetricPoint]
    request_rate: list[MetricPoint]
    error_rate: list[MetricPoint]
    source: str


class PodHealth(BaseModel):
    """One Kubernetes pod health summary for a service workload."""

    name: str
    phase: str
    ready: bool
    restart_count: int = 0
    reason: str | None = None
    message: str | None = None


class WorkloadHealth(BaseModel):
    """Kubernetes Deployment and Pod health for one service."""

    service: str
    namespace: str
    desired_replicas: int
    updated_replicas: int
    ready_replicas: int
    available_replicas: int
    unavailable_replicas: int
    observed_generation: int | None = None
    pods: list[PodHealth] = Field(default_factory=list)
    source: str


class LogLine(BaseModel):
    """Single log event returned from Loki or demo fallback data."""

    timestamp: str
    line: str
    namespace: str | None = None
    pod: str | None = None
    container: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class LogsResponse(BaseModel):
    """Log response for a selected service."""

    service: str
    lines: list[LogLine]
    source: str
