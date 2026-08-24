"""PostgreSQL adapter for runtime observability data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.core.config import get_settings


@dataclass(frozen=True)
class PgRunEvent:
    id: int
    trace_id: str
    kind: str
    detail: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class PgKnowledgeGap:
    id: int
    question: str
    trace_id: str
    resolved: bool
    created_at: datetime


class PgOpsStore:
    """Append/query adapter for ``ops.run_event`` and ``ops.knowledge_gap``."""

    def __init__(self, dsn: str) -> None:
        if not dsn.strip():
            raise RuntimeError("INSURANCE_PG_DSN is not configured")
        self.dsn = dsn

    @classmethod
    def from_settings(cls) -> "PgOpsStore":
        return cls(get_settings().INSURANCE_PG_DSN)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn)

    def record_event(
        self, trace_id: str, kind: str, detail: dict[str, Any] | None = None
    ) -> PgRunEvent:
        payload = json.loads(json.dumps(detail or {}, default=str))
        with self._connect() as conn:
            row = conn.execute(
                "INSERT INTO ops.run_event (trace_id, kind, detail) "
                "VALUES (%s, %s, %s) "
                "RETURNING id, trace_id, kind, detail, created_at",
                (trace_id, kind, Jsonb(payload)),
            ).fetchone()
        return PgRunEvent(row[0], row[1], row[2], row[3], row[4])

    def record_knowledge_gap(self, question: str, trace_id: str) -> PgKnowledgeGap:
        with self._connect() as conn:
            row = conn.execute(
                "INSERT INTO ops.knowledge_gap (question, trace_id) "
                "VALUES (%s, %s) "
                "RETURNING id, question, trace_id, resolved, created_at",
                (question, trace_id),
            ).fetchone()
        return PgKnowledgeGap(*row)

    def list_events(
        self, *, trace_id: str | None = None, kind: str | None = None, limit: int = 50
    ) -> list[PgRunEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if trace_id:
            clauses.append("trace_id = %s")
            params.append(trace_id)
        if kind:
            clauses.append("kind = %s")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, trace_id, kind, detail, created_at FROM ops.run_event "
                f"{where} ORDER BY id DESC LIMIT %s",
                params,
            ).fetchall()
        return [PgRunEvent(row[0], row[1], row[2], row[3], row[4]) for row in rows]

    def list_knowledge_gaps(
        self, *, resolved: bool | None = None, limit: int = 50
    ) -> list[PgKnowledgeGap]:
        params: list[Any] = []
        clauses = ["deleted_at IS NULL"]
        if resolved is not None:
            clauses.append("resolved = %s")
            params.append(resolved)
        where = f"WHERE {' AND '.join(clauses)}"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, question, trace_id, resolved, created_at "
                "FROM ops.knowledge_gap "
                f"{where} ORDER BY id DESC LIMIT %s",
                params,
            ).fetchall()
        return [PgKnowledgeGap(*row) for row in rows]

    def summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            event_total = conn.execute("SELECT count(*) FROM ops.run_event").fetchone()[0]
            event_by_kind = conn.execute(
                "SELECT kind, count(*) FROM ops.run_event "
                "GROUP BY kind ORDER BY count(*) DESC LIMIT 8"
            ).fetchall()
            gap_total = conn.execute(
                "SELECT count(*) FROM ops.knowledge_gap WHERE deleted_at IS NULL"
            ).fetchone()[0]
            gap_unresolved = conn.execute(
                "SELECT count(*) FROM ops.knowledge_gap "
                "WHERE resolved = false AND deleted_at IS NULL"
            ).fetchone()[0]
        return {
            "event_total": int(event_total),
            "event_by_kind": [(str(kind), int(count)) for kind, count in event_by_kind],
            "gap_total": int(gap_total),
            "gap_unresolved": int(gap_unresolved),
        }

    def purge_knowledge_gaps(self, before: datetime) -> int:
        """Soft-delete expired knowledge-gap rows and return the affected count."""
        with self._connect() as conn:
            row = conn.execute(
                "UPDATE ops.knowledge_gap SET deleted_at = now() "
                "WHERE deleted_at IS NULL AND created_at < %s RETURNING id",
                (before,),
            ).fetchall()
        return len(row)

    def readiness(self) -> dict[str, Any]:
        """Return a non-throwing readiness report for the runtime tables."""
        required = (
            "ops.run_event",
            "ops.knowledge_gap",
            "ops.run_event_id_seq",
            "ops.knowledge_gap_id_seq",
        )
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT name, to_regclass(name) FROM unnest(%s::text[]) AS t(name)",
                    (list(required),),
                ).fetchall()
                database = conn.execute("SELECT current_database()").fetchone()[0]
            missing = [name for name, regclass in rows if regclass is None]
            return {
                "backend": "postgres",
                "database": database,
                "ready": not missing,
                "missing": missing,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "backend": "postgres",
                "database": None,
                "ready": False,
                "reason": f"{type(exc).__name__}: {exc}"[:200],
            }
