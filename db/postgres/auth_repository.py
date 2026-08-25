"""PostgreSQL account, face credential, and runtime-event adapter."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.core.config import get_settings
from app.core.errors import InfraError
from db.postgres.pg_insurance_repository import _postgres_error
from db.postgres.pool import connection


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

    @contextmanager
    def _connect(self):
        """연결 획득뿐 아니라 **`with` 블록 안 쿼리 실패까지** 번역한다.

        ★코덱스 3차 리뷰 지적 — 앞선 수정은 연결·SET 문만 감쌌다. `get_user` 등
          각 메서드의 `conn.execute(...)`는 이 함수가 반환한 *뒤에* 호출자 쪽
          `with` 블록에서 실행되므로, 거기서 난 `psycopg.Error`는 하나도 못
          잡고 그대로 FastAPI까지 새어나가 분류 안 된 500이 됐다.
          `@contextmanager`로 바꿔 호출자의 `with` 블록 전체를 이 함수 안에서
          감싸면, `yield` 지점에서 그 예외가 다시 던져져 여기서 잡힌다.
        """
        try:
            lease = connection(self.dsn)
            lease.execute("SET statement_timeout = '10s'")
            lease.execute("SET lock_timeout = '3s'")
        except psycopg.Error as exc:
            raise _postgres_error(exc) from exc
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"인증 PostgreSQL에 연결할 수 없습니다: {exc}") from exc

        try:
            with lease as conn:
                yield conn
        except psycopg.Error as exc:
            raise _postgres_error(exc) from exc

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
