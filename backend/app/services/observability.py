"""Prometheus and Loki client helpers.

The clients call real observability backends when available and provide
deterministic demo data for local development without a cluster.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime

import httpx

from app.config import Settings
from app.schemas import LogLine, LogsResponse, MetricPoint, ServiceMetrics


class PrometheusClient:
    """Fetch service metrics from Prometheus."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def service_metrics(self, service: str) -> ServiceMetrics:
        """Return CPU, memory, traffic, and error-rate data for a service."""

        try:
            cpu = await self._query_range(
                f'sum(rate(container_cpu_usage_seconds_total{{namespace="{self._settings.kubectl_namespace}",pod=~"{service}.*"}}[2m]))'
            )
            memory_query = (
                "sum("
                f'container_memory_working_set_bytes{{namespace="{self._settings.kubectl_namespace}",pod=~"{service}.*"}}'
                ") / 1024 / 1024"
            )
            memory = await self._query_range(
                memory_query
            )
            request_rate = await self._query_range(
                f'sum(rate(http_requests_total{{service="{service}"}}[2m]))'
            )
            error_rate = await self._query_range(
                f'sum(rate(http_requests_total{{service="{service}",status=~"5.."}}[2m]))'
            )
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

    async def _query_range(self, query: str) -> list[MetricPoint]:
        """Execute a Prometheus query_range call for the last 15 minutes."""

        end = int(time.time())
        start = end - 15 * 60
        params = {"query": query, "start": start, "end": end, "step": 30}
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
