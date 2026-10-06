"""Small persistence layer for deployment records.

The repository uses a JSON file so the app works out of the box in local
development, while keeping the API boundary easy to replace with PostgreSQL.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Protocol
from urllib.parse import unquote, urlparse
from uuid import uuid4

import psycopg2
from psycopg2.extras import Json

from app.schemas import AuditLogEntry, DeploymentRecord


class DeploymentRepository(Protocol):
    """Persistence contract used by deployment orchestration."""

    def list(self) -> list[DeploymentRecord]:
        """Return all known deployments."""

    def get(self, name: str) -> DeploymentRecord | None:
        """Return one deployment record by name."""

    def upsert(self, record: DeploymentRecord) -> DeploymentRecord:
        """Insert or replace a deployment record."""

    def delete(self, name: str) -> DeploymentRecord | None:
        """Delete one deployment record if present."""


class AuditLogRepository(Protocol):
    """Persistence contract used by audit logging."""

    def append(
        self,
        *,
        action: str,
        status: str,
        message: str,
        service: str | None = None,
        actor: str = "system",
        metadata: dict | None = None,
    ) -> AuditLogEntry:
        """Append one immutable audit event."""

    def list(self, limit: int = 100) -> list[AuditLogEntry]:
        """Return newest audit entries first."""


class FileDeploymentRepository:
    """Thread-safe JSON-backed repository for deployment records."""

    def __init__(self, state_file: str) -> None:
        self._path = Path(state_file)
        self._lock = Lock()

    def list(self) -> list[DeploymentRecord]:
        """Return all known deployments sorted by most recent update."""

        with self._lock:
            records = self._read()
        return sorted(records.values(), key=lambda item: item.updated_at, reverse=True)

    def get(self, name: str) -> DeploymentRecord | None:
        """Return one deployment record by name."""

        with self._lock:
            return self._read().get(name)

    def upsert(self, record: DeploymentRecord) -> DeploymentRecord:
        """Insert or replace a deployment record."""

        with self._lock:
            records = self._read()
            records[record.name] = record
            self._write(records)
        return record

    def delete(self, name: str) -> DeploymentRecord | None:
        """Delete one deployment record if present."""

        with self._lock:
            records = self._read()
            removed = records.pop(name, None)
            self._write(records)
        return removed

    def _read(self) -> dict[str, DeploymentRecord]:
        """Read the state file and hydrate Pydantic records."""

        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return {name: DeploymentRecord.model_validate(item) for name, item in raw.items()}

    def _write(self, records: dict[str, DeploymentRecord]) -> None:
        """Persist records with an atomic replace to avoid partial writes."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            name: record.model_dump(mode="json")
            for name, record in records.items()
        }
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self._path)


class FileAuditLogRepository:
    """Thread-safe JSON-backed append-only audit log repository."""

    def __init__(self, audit_file: str, max_entries: int = 500) -> None:
        self._path = Path(audit_file)
        self._max_entries = max_entries
        self._lock = Lock()

    def append(
        self,
        *,
        action: str,
        status: str,
        message: str,
        service: str | None = None,
        actor: str = "system",
        metadata: dict | None = None,
    ) -> AuditLogEntry:
        """Append one immutable event and keep the newest records."""

        entry = AuditLogEntry(
            id=str(uuid4()),
            action=action,
            service=service,
            actor=actor,
            status=status,
            message=message,
            metadata=metadata or {},
            created_at=utc_now(),
        )
        with self._lock:
            entries = self._read()
            entries.append(entry)
            entries = entries[-self._max_entries :]
            self._write(entries)
        return entry

    def list(self, limit: int = 100) -> list[AuditLogEntry]:
        """Return newest audit entries first."""

        with self._lock:
            entries = self._read()
        return list(reversed(entries))[:limit]

    def _read(self) -> list[AuditLogEntry]:
        """Read and hydrate persisted audit log entries."""

        if not self._path.exists():
            return []
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return [AuditLogEntry.model_validate(item) for item in raw]

    def _write(self, entries: list[AuditLogEntry]) -> None:
        """Persist audit entries with an atomic replace."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [entry.model_dump(mode="json") for entry in entries]
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self._path)


def utc_now() -> datetime:
    """Return timezone-aware UTC timestamps for consistent API output."""

    return datetime.now(UTC)


def connect_postgres(database_url: str):
    """Open PostgreSQL connections from URL settings with explicit libpq parameters."""

    parsed = urlparse(database_url)
    if parsed.scheme in {"postgresql", "postgres"}:
        return psycopg2.connect(
            dbname=parsed.path.lstrip("/") or None,
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            host=parsed.hostname,
            port=parsed.port or 5432,
        )
    return psycopg2.connect(database_url)


class PostgresDeploymentRepository:
    """PostgreSQL-backed deployment repository for real local Kubernetes mode."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._ensure_schema()

    def list(self) -> list[DeploymentRecord]:
        """Return all known deployments sorted by most recent update."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload
                FROM infrawatch_deployments
                ORDER BY updated_at DESC
                """
            )
            return [DeploymentRecord.model_validate(row[0]) for row in cursor.fetchall()]

    def get(self, name: str) -> DeploymentRecord | None:
        """Return one deployment record by name."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload
                FROM infrawatch_deployments
                WHERE name = %s
                """,
                (name,),
            )
            row = cursor.fetchone()
            return DeploymentRecord.model_validate(row[0]) if row else None

    def upsert(self, record: DeploymentRecord) -> DeploymentRecord:
        """Insert or replace a deployment record."""

        payload = record.model_dump(mode="json")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO infrawatch_deployments (name, payload, created_at, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name)
                DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
                """,
                (record.name, Json(payload), record.created_at, record.updated_at),
            )
        return record

    def delete(self, name: str) -> DeploymentRecord | None:
        """Delete one deployment record if present."""

        existing = self.get(name)
        if existing is None:
            return None
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM infrawatch_deployments WHERE name = %s", (name,))
        return existing

    def _ensure_schema(self) -> None:
        """Create the small persistence schema used by InfraWatch."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS infrawatch_deployments (
                    name TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS infrawatch_deployments_updated_at_idx
                ON infrawatch_deployments (updated_at DESC)
                """
            )

    def _connect(self):
        """Open a short-lived database connection."""

        return connect_postgres(self._database_url)


class PostgresAuditLogRepository:
    """PostgreSQL-backed append-only audit log repository."""

    def __init__(self, database_url: str, max_entries: int = 1000) -> None:
        self._database_url = database_url
        self._max_entries = max_entries
        self._ensure_schema()

    def append(
        self,
        *,
        action: str,
        status: str,
        message: str,
        service: str | None = None,
        actor: str = "system",
        metadata: dict | None = None,
    ) -> AuditLogEntry:
        """Append one immutable event and keep the newest records."""

        entry = AuditLogEntry(
            id=str(uuid4()),
            action=action,
            service=service,
            actor=actor,
            status=status,
            message=message,
            metadata=metadata or {},
            created_at=utc_now(),
        )
        payload = entry.model_dump(mode="json")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO infrawatch_audit_logs (id, payload, created_at)
                VALUES (%s, %s, %s)
                """,
                (entry.id, Json(payload), entry.created_at),
            )
            cursor.execute(
                """
                DELETE FROM infrawatch_audit_logs
                WHERE id IN (
                    SELECT id
                    FROM infrawatch_audit_logs
                    ORDER BY created_at DESC
                    OFFSET %s
                )
                """,
                (self._max_entries,),
            )
        return entry

    def list(self, limit: int = 100) -> list[AuditLogEntry]:
        """Return newest audit entries first."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload
                FROM infrawatch_audit_logs
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [AuditLogEntry.model_validate(row[0]) for row in cursor.fetchall()]

    def _ensure_schema(self) -> None:
        """Create the small persistence schema used by audit logging."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS infrawatch_audit_logs (
                    id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS infrawatch_audit_logs_created_at_idx
                ON infrawatch_audit_logs (created_at DESC)
                """
            )

    def _connect(self):
        """Open a short-lived database connection."""

        return connect_postgres(self._database_url)
