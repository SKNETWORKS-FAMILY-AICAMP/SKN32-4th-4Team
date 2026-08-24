"""PostgreSQL account, face credential, and runtime-event adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.core.config import get_settings


@dataclass(frozen=True)
class PgUser:
    id: UUID
    legacy_user_id: int | None
    username: str
    hashed_password: str
    role: str
    created_at: datetime


@dataclass(frozen=True)
class PgFaceCredential:
    user_id: UUID
    embedding: bytes
    created_at: datetime


class PgAuthStore:
    """Small adapter used by auth/face/observability while SQLite is retired."""

    def __init__(self, dsn: str) -> None:
        if not dsn.strip():
            raise RuntimeError("INSURANCE_PG_DSN이 설정되지 않았습니다.")
        self.dsn = dsn

    @classmethod
    def from_settings(cls) -> "PgAuthStore":
        return cls(get_settings().INSURANCE_PG_DSN)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn)

    def get_user(self, username: str) -> PgUser | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, legacy_user_id, username, password_hash, role, created_at "
                "FROM app.user_account WHERE username=%s",
                (username,),
            ).fetchone()
        return PgUser(*row) if row else None

    def create_user(self, username: str, password_hash: str) -> PgUser:
        with self._connect() as conn:
            row = conn.execute(
                "INSERT INTO app.user_account (username, password_hash) "
                "VALUES (%s,%s) RETURNING id, legacy_user_id, username, password_hash, role, created_at",
                (username, password_hash),
            ).fetchone()
        return PgUser(*row)

    def list_users(self) -> list[PgUser]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, legacy_user_id, username, password_hash, role, created_at "
                "FROM app.user_account ORDER BY id"
            ).fetchall()
        return [PgUser(*row) for row in rows]

    def change_role(self, username: str, role: str, *, actor: str) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT id, legacy_user_id, username, password_hash, role, created_at "
                    "FROM app.user_account WHERE username=%s FOR UPDATE",
                    (username,),
                ).fetchone()
                if row is None:
                    return {"changed": False, "missing": True, "username": username}
                user = PgUser(*row)
                if user.role == role:
                    return {
                        "changed": False,
                        "username": username,
                        "role": role,
                        "message": f"변경 없음(이미 {role}): {username}",
                    }
                if user.role == "ADMIN" and role != "ADMIN":
                    remaining = conn.execute(
                        "SELECT count(*) FROM app.user_account "
                        "WHERE role='ADMIN' AND id<>%s",
                        (user.id,),
                    ).fetchone()[0]
                    if remaining == 0:
                        raise ValueError("마지막 관리자는 강등할 수 없습니다(잠금 방지).")
                conn.execute(
                    "UPDATE app.user_account SET role=%s WHERE id=%s",
                    (role, user.id),
                )
                conn.execute(
                    "INSERT INTO ops.run_event (trace_id, kind, detail) VALUES (%s,%s,%s)",
                    ("no-trace", "role_change", Jsonb({
                        "username": username, "from": user.role, "to": role, "by": actor
                    })),
                )
        return {
            "changed": True,
            "username": username,
            "role": role,
            "from": user.role,
            "message": f"역할 변경: {username} {user.role} → {role}",
        }

    def authenticate(self, username: str, password_hash: str) -> PgUser | None:
        user = self.get_user(username)
        if user is None or user.hashed_password != password_hash:
            return None
        return user

    def get_face(self, user_id: UUID) -> PgFaceCredential | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, embedding, created_at "
                "FROM app.face_credential WHERE user_id=%s",
                (user_id,),
            ).fetchone()
        return PgFaceCredential(*row) if row else None

    def upsert_face(self, user_id: UUID, embedding: bytes) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO app.face_credential (user_id, embedding) VALUES (%s,%s) "
                "ON CONFLICT (user_id) DO UPDATE SET embedding=EXCLUDED.embedding",
                (user_id, embedding),
            )

    def delete_face(self, user_id: UUID) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM app.face_credential WHERE user_id=%s", (user_id,))

    def record_event(self, trace_id: str, kind: str, detail: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ops.run_event (id, trace_id, kind, detail) "
                "VALUES (nextval('ops.run_event_id_seq'), %s, %s, %s)",
                (trace_id, kind, Jsonb(json.loads(json.dumps(detail or {}, default=str)))),
            )
