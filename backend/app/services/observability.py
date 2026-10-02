"""Prometheus and Loki client helpers.

The clients call real observability backends when available and provide
deterministic demo data for local development without a cluster.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import redis.asyncio as redis
from prometheus_client import Counter

from app.config import Settings
from app.schemas import LogLine, LogsResponse, MetricPoint, ServiceMetrics

PROMETHEUS_CACHE_EVENTS = Counter(
    "infrawatch_prometheus_cache_events_total",
    "Prometheus metrics cache events in the InfraWatch backend.",
    ["event"],
)

PROMETHEUS_RANGE_SECONDS = 15 * 60
PROMETHEUS_STEP_SECONDS = 30


class PrometheusClient:
    """Fetch service metrics from Prometheus."""

    def __init__(self, settings: Settings, redis_client: Any | None = None) -> None:
        self._settings = settings
        self._redis = redis_client
        if self._redis is None and settings.redis_url:
            try:
                self._redis = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
            except ValueError:
                PROMETHEUS_CACHE_EVENTS.labels(event="configuration_error").inc()
                self._redis = None

    async def service_metrics(self, service: str) -> ServiceMetrics:
        """Return CPU, memory, traffic, and error-rate data for a service."""

        query_end = int(time.time())
        cache_key = self._metrics_cache_key(service)
        if self._redis is not None:
            cached_metrics = await self._get_cached_metrics(cache_key)
            if cached_metrics is not None:
                PROMETHEUS_CACHE_EVENTS.labels(event="hit").inc()
                return cached_metrics
            PROMETHEUS_CACHE_EVENTS.labels(event="miss").inc()

        metrics = await self._service_metrics_uncached(service, query_end)
        if metrics.source == "prometheus":
            await self._set_cached_metrics(cache_key, metrics)
        return metrics

    async def _service_metrics_uncached(self, service: str, query_end: int) -> ServiceMetrics:
        """Read service metrics directly from Prometheus with the existing fallback behavior."""

        queries = self._metric_queries(service)
        try:
            cpu = await self._query_range(queries["cpu"], end=query_end)
            memory = await self._query_range(queries["memory"], end=query_end)
            request_rate = await self._query_range(queries["request_rate"], end=query_end)
            error_rate = await self._query_range(queries["error_rate"], end=query_end)
            if not error_rate:
                error_rate = self._zero_series_like(request_rate, cpu, memory)
            if self._settings.allow_mock_observability and not all([cpu, memory, request_rate]):
                return self._mock_metrics(service)
            return ServiceMetrics(
                service=service,
                cpu_cores=cpu,
                memory_megabytes=memory,
                request_rate=request_rate,
                error_rate=error_rate,
                source="prometheus",
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            if not self._settings.allow_mock_observability:
                raise
            return self._mock_metrics(service)

    async def _query_range(self, query: str, end: int | None = None) -> list[MetricPoint]:
        """Execute a Prometheus query_range call for the last 15 minutes."""

        query_end = end or int(time.time())
        start = query_end - PROMETHEUS_RANGE_SECONDS
        params = {"query": query, "start": start, "end": query_end, "step": PROMETHEUS_STEP_SECONDS}
        async with httpx.AsyncClient(timeout=self._settings.observability_timeout_seconds) as client:
            response = await client.get(f"{self._settings.prometheus_url}/api/v1/query_range", params=params)
            response.raise_for_status()

        payload = response.json()
        result = payload["data"]["result"]
        if not result:
            return []
        return [
            MetricPoint(timestamp=int(point[0]), value=float(point[1]))
            for point in result[0]["values"]
        ]

    def _metric_queries(self, service: str) -> dict[str, str]:
        """Build the PromQL queries used for one service metrics response."""

        namespace = self._settings.kubectl_namespace
        memory_query = (
            "sum("
            f'container_memory_working_set_bytes{{namespace="{namespace}",pod=~"{service}.*"}}'
            ") / 1024 / 1024"
        )
        return {
            "cpu": f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",pod=~"{service}.*"}}[2m]))',
            "memory": memory_query,
            "request_rate": f'sum(rate(http_requests_total{{service="{service}"}}[2m]))',
            "error_rate": f'sum(rate(http_requests_total{{service="{service}",status=~"5.."}}[2m]))',
        }

    def _metrics_cache_key(self, service: str) -> str:
        """Return a deterministic key for the full service metrics response."""

        payload = {
            "version": 1,
            "service": service,
            "namespace": self._settings.kubectl_namespace,
            "prometheus_url": self._settings.prometheus_url,
            "range_seconds": PROMETHEUS_RANGE_SECONDS,
            "step_seconds": PROMETHEUS_STEP_SECONDS,
            "queries": self._metric_queries(service),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        return f"infrawatch:metrics:{digest}"

    async def _get_cached_metrics(self, cache_key: str) -> ServiceMetrics | None:
        """Read metrics from Redis when available; failures behave like cache misses."""

        if self._redis is None:
            return None

        try:
            raw = await self._redis.get(cache_key)
        except Exception:
            PROMETHEUS_CACHE_EVENTS.labels(event="unavailable").inc()
            return None

        if raw is None:
            return None

        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return ServiceMetrics.model_validate_json(raw)
        except (TypeError, ValueError):
            PROMETHEUS_CACHE_EVENTS.labels(event="invalid").inc()
            return None

    async def _set_cached_metrics(self, cache_key: str, metrics: ServiceMetrics) -> None:
        """Store real Prometheus metrics in Redis without making Redis mandatory."""

        if self._redis is None:
            return

        try:
            await self._redis.setex(cache_key, self._settings.redis_cache_ttl, metrics.model_dump_json())
            PROMETHEUS_CACHE_EVENTS.labels(event="stored").inc()
        except Exception:
            PROMETHEUS_CACHE_EVENTS.labels(event="store_error").inc()

    def _zero_series_like(self, *series_options: list[MetricPoint]) -> list[MetricPoint]:
        """Return a zero-valued series using timestamps from the first available real series."""

        for series in series_options:
            if series:
                return [MetricPoint(timestamp=point.timestamp, value=0.0) for point in series]
        return []

    def _mock_metrics(self, service: str) -> ServiceMetrics:
        """Produce stable, service-specific time series for demos and tests."""

        now = int(time.time())
        seed = sum((index + 1) * ord(char) for index, char in enumerate(service))
        timestamps = [now - (14 - index) * 60 for index in range(15)]

        def series(base: float, amplitude: float, *, trend: float = 0, floor: float = 0) -> list[MetricPoint]:
            return [
                MetricPoint(
                    timestamp=stamp,
                    value=round(
                        max(
                            floor,
                            base
                            + math.sin((index + seed % 7) / 2.2) * amplitude
                            + math.cos((index + seed % 11) / 4.1) * amplitude * 0.35
                            + trend * index,
                        ),
                        3,
                    ),
                )
                for index, stamp in enumerate(timestamps)
            ]

        cpu_base = 0.16 + (seed % 13) / 100
        memory_base = 210 + seed % 90
        request_base = 22 + seed % 28
        error_base = 0.015 + (seed % 5) / 100

        return ServiceMetrics(
            service=service,
            cpu_cores=series(cpu_base, 0.065, trend=0.001),
            memory_megabytes=series(memory_base, 24, trend=0.7),
            request_rate=series(request_base, 7.5, trend=0.12),
            error_rate=series(error_base, 0.018),
            source="mock",
        )


class LokiClient:
    """Fetch recent service logs from Loki."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def logs(self, service: str, limit: int = 100) -> LogsResponse:
        """Return recent log lines for a service."""

        try:
            params = {
                "query": f'{{app="{service}"}}',
                "limit": limit,
                "direction": "backward",
            }
            async with httpx.AsyncClient(timeout=self._settings.observability_timeout_seconds) as client:
                response = await client.get(f"{self._settings.loki_url}/loki/api/v1/query_range", params=params)
                response.raise_for_status()
            payload = response.json()
            lines = self._parse_loki_streams(payload)
            if self._settings.allow_mock_observability and not lines:
                return self._mock_logs(service, limit)
            return LogsResponse(service=service, lines=lines[:limit], source="loki")
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            if not self._settings.allow_mock_observability:
                raise
            return self._mock_logs(service, limit)

    def _parse_loki_streams(self, payload: dict) -> list[LogLine]:
        """Convert Loki stream values into API log lines."""

        parsed: list[LogLine] = []
        for stream in payload["data"]["result"]:
            for raw_timestamp, line in stream["values"]:
                timestamp = datetime.fromtimestamp(int(raw_timestamp) / 1_000_000_000, UTC).isoformat()
                parsed.append(LogLine(timestamp=timestamp, line=line))
        return parsed

    def _mock_logs(self, service: str, limit: int) -> LogsResponse:
        """Return varied operational logs when Loki is not reachable."""

        now = int(time.time())
        seed = sum(ord(char) for char in service)
        routes = ("/healthz", "/api/orders", "/api/catalog", "/metrics")
        messages = (
            lambda index: (
                f"[info] service={service} request completed method=GET path={routes[(index + seed) % len(routes)]} "
                f"status=200 duration_ms={18 + (seed + index * 7) % 83}"
            ),
            lambda index: (
                f"[info] service={service} cache refresh completed entries={140 + (seed + index * 13) % 760}"
            ),
            lambda index: (
                f"[info] service={service} deployment revision={1 + seed % 8} ready_replicas={1 + seed % 3}"
            ),
            lambda index: (
                f"[warn] service={service} upstream latency elevated duration_ms={190 + (seed + index) % 90} retry=1"
            ),
            lambda index: (
                f"[info] service={service} trace_id={seed:04x}{index:04x} span=database.query status=ok"
            ),
        )
        entries = [
            LogLine(
                timestamp=datetime.fromtimestamp(now - index * 17, UTC).isoformat(),
                line=messages[(index + seed) % len(messages)](index),
            )
            for index in range(min(limit, 20))
        ]
        return LogsResponse(service=service, lines=entries, source="mock")
