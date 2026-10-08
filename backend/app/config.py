"""Runtime configuration for the InfraWatch API.

All operational settings are read from environment variables so secrets and
cluster-specific values never need to be hardcoded in source control.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings."""

    app_name: str = "InfraWatch API"
    environment: str = "development"
    log_level: str = "INFO"
    service_name: str = "infrawatch-backend"

    cors_origins: Annotated[
        list[str],
        Field(
            default_factory=lambda: [
                "http://localhost:3000",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ]
        ),
    ]

    state_file: str = "/tmp/infrawatch/deployments.json"
    audit_file: str = "/tmp/infrawatch/audit-log.json"
    database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("INFRAWATCH_DATABASE_URL", "DATABASE_URL"),
    )
    execute_kubectl: bool = False
    seed_demo_data: bool = False
    kubectl_binary: str = "kubectl"
    kubectl_namespace: str = "infrawatch"
    rollout_timeout_seconds: int = Field(default=180, ge=30, le=900)

    prometheus_url: str = "http://prometheus:9090"
    loki_url: str = "http://loki:3100"
    observability_timeout_seconds: float = 10.0
    allow_mock_observability: bool = True
    redis_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("INFRAWATCH_REDIS_URL", "REDIS_URL"),
    )
    redis_cache_ttl: int = Field(
        default=5,
        ge=1,
        le=60,
        validation_alias=AliasChoices("INFRAWATCH_REDIS_CACHE_TTL", "REDIS_CACHE_TTL"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="INFRAWATCH_",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings object for dependency injection."""

    return Settings()
