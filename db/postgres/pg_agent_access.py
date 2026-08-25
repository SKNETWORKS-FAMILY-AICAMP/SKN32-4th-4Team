"""등록 외부 에이전트용 PostgreSQL 인증·한도·멱등·감사 어댑터.

별도 ``insurance_agent`` DB의 ``ops`` 스키마만 사용한다. 연결 실패 시 공개 API나
파일 저장소로 폴백하지 않는다. 원문 API 키·subject·요청/응답은 DB에 쓰지 않는다.
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import asdict

from app.core.domain.agent_access import (
    AgentAuditRecord,
    AgentPrincipal,
    IdempotencyReservation,
    RateLimitDecision,
    hash_api_key,
    key_fingerprint,
    parse_api_key,
    validate_client_id,
    validate_scopes,
)
from app.core.errors import AuthErr, ConflictErr, InfraError, ValidationErr
from db.postgres.pool import connection


class PgAgentAccess:
    """한 요청 안에서 재사용 가능한 stateless PostgreSQL gateway."""

    def __init__(self, dsn: str):
        self._dsn = (dsn or "").strip()
        if not self._dsn:
            raise InfraError("AGENT_PG_DSN이 설정되지 않았습니다.")

    def _connect(self):
        import psycopg

        try:
            conn = connection(self._dsn)
            conn.execute("SET statement_timeout = '10s'")
            conn.execute("SET lock_timeout = '3s'")
            return conn
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"외부 에이전트 PostgreSQL에 연결할 수 없습니다: {exc}") from exc

    def readiness(self) -> dict:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT to_regclass('ops.agent_client'), "
                    "to_regclass('ops.agent_client_auth_log'), "
                    "to_regclass('ops.agent_rate_event'), "
                    "to_regclass('ops.agent_idempotency'), "
                    "to_regclass('ops.agent_api_audit')"
                ).fetchone()
            ready = bool(row and all(row))
            return {"backend": "postgres", "database": "insurance_agent", "ready": ready}
        except InfraError as exc:
            return {
                "backend": "postgres",
                "database": "insurance_agent",
                "ready": False,
                "reason": str(exc)[:200],
            }

    def record_auth_attempt(
        self,
        *,
        result: str,
        trace_hash: str | None,
        claimed_client_id: str | None = None,
        authenticated_client_id: str | None = None,
        fingerprint: str = "",
    ) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO ops.agent_client_auth_log (
                        auth_event_id, claimed_client_id, authenticated_client_id,
                        key_fingerprint, result, trace_hash
                    ) VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        uuid.uuid4().hex,
                        claimed_client_id,
                        authenticated_client_id,
                        fingerprint[:16],
                        result,
                        trace_hash,
                    ),
                )
        except InfraError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"에이전트 인증 감사를 기록하지 못했습니다: {exc}") from exc

    def authenticate(self, raw_key: str, *, trace_hash: str) -> AgentPrincipal:
        """고엔트로피 키를 해시 대조하고 성공·실패를 같은 트랜잭션에 기록한다."""

        try:
            claimed = parse_api_key(raw_key)
        except ValueError:
            self.record_auth_attempt(
                result="malformed",
                trace_hash=trace_hash,
                fingerprint=key_fingerprint(raw_key),
            )
            raise AuthErr("등록되지 않았거나 유효하지 않은 에이전트 키입니다.") from None

        fingerprint = key_fingerprint(raw_key)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT client_id, display_name, api_key_hash, scopes,
                           rate_limit_per_minute, status
                    FROM ops.agent_client
                    WHERE client_id=%s
                    """,
                    (claimed,),
                )
                row = cur.fetchone()
                if row is None:
                    result = "unknown"
                    authenticated = None
                elif row[5] != "active":
                    result = "disabled"
                    authenticated = row[0]
                elif not hmac.compare_digest(str(row[2]), hash_api_key(raw_key)):
                    result = "invalid"
                    authenticated = row[0]
                else:
                    result = "success"
                    authenticated = row[0]

                cur.execute(
                    """
                    INSERT INTO ops.agent_client_auth_log (
                        auth_event_id, claimed_client_id, authenticated_client_id,
                        key_fingerprint, result, trace_hash
                    ) VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        uuid.uuid4().hex,
                        claimed,
                        authenticated,
                        fingerprint,
                        result,
                        trace_hash,
                    ),
                )
                if result != "success":
                    conn.commit()
                    raise AuthErr("등록되지 않았거나 유효하지 않은 에이전트 키입니다.")

                principal = AgentPrincipal(
                    client_id=row[0],
                    display_name=row[1],
                    scopes=frozenset(row[3] or ()),
                    rate_limit_per_minute=int(row[4]),
                    key_fingerprint=fingerprint,
                )
                conn.commit()
                return principal
        except (AuthErr, InfraError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"에이전트 키를 검증하지 못했습니다: {exc}") from exc

    def consume_rate_limit(
        self,
        principal: AgentPrincipal,
        *,
        subject_hash: str,
        operation: str,
    ) -> RateLimitDecision:
        """client+subject+operation 버킷을 DB 행 잠금으로 원자적으로 소비한다."""

        try:
            with self._connect() as conn, conn.cursor() as cur:
                # 정확한 client+operation+subject 버킷만 직렬화한다. 서로 다른 사용자의
                # 정상 요청을 client 전체 행 잠금으로 막지 않는다.
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{principal.client_id}\x1f{operation}\x1f{subject_hash}",),
                )
                cur.execute(
                    "SELECT rate_limit_per_minute, status "
                    "FROM ops.agent_client WHERE client_id=%s",
                    (principal.client_id,),
                )
                client = cur.fetchone()
                if client is None or client[1] != "active":
                    raise AuthErr("비활성화된 에이전트 클라이언트입니다.")
                limit = int(client[0])
                cur.execute(
                    """
                    SELECT count(*)
                    FROM ops.agent_rate_event
                    WHERE client_id=%s AND operation=%s AND subject_hash=%s
                      AND allowed=true
                      AND occurred_at >= clock_timestamp() - interval '60 seconds'
                    """,
                    (principal.client_id, operation, subject_hash),
                )
                allowed = int(cur.fetchone()[0]) < limit
                cur.execute(
                    """
                    INSERT INTO ops.agent_rate_event (
                        rate_event_id, client_id, operation, subject_hash,
                        allowed, limit_per_minute
                    ) VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        uuid.uuid4().hex,
                        principal.client_id,
                        operation,
                        subject_hash,
                        allowed,
                        limit,
                    ),
                )
                conn.commit()
                return RateLimitDecision(allowed=allowed, retry_after_seconds=0 if allowed else 60)
        except (AuthErr, InfraError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"에이전트 요청 한도를 판정하지 못했습니다: {exc}") from exc

    def reserve_idempotency(
        self,
        *,
        client_id: str,
        idempotency_hash: str,
        request_hash: str,
    ) -> IdempotencyReservation:
        """쓰기 요청을 예약한다. 동일 키·다른 payload는 무조건 409다."""

        if len(idempotency_hash) != 64:
            raise ValidationErr("idempotency_hash 형식이 올바르지 않습니다.")
        lease_token = uuid.uuid4().hex
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ops.agent_idempotency (
                        client_id, idempotency_hash, request_hash, lease_token
                    ) VALUES (%s,%s,%s,%s)
                    ON CONFLICT (client_id, idempotency_hash) DO NOTHING
                    RETURNING client_id
                    """,
                    (client_id, idempotency_hash, request_hash, lease_token),
                )
                if cur.fetchone() is not None:
                    conn.commit()
                    return IdempotencyReservation(replayed=False, lease_token=lease_token)

                cur.execute(
                    """
                    SELECT request_hash, state, submission_id,
                           updated_at < clock_timestamp() - interval '5 minutes' AS stale
                    FROM ops.agent_idempotency
                    WHERE client_id=%s AND idempotency_hash=%s
                    FOR UPDATE
                    """,
                    (client_id, idempotency_hash),
                )
                row = cur.fetchone()
                if row is None:
                    raise InfraError("멱등 충돌 후 기존 예약을 찾지 못했습니다.")
                if not hmac.compare_digest(str(row[0]), request_hash):
                    raise ConflictErr("같은 Idempotency-Key에 다른 payload가 제출됐습니다.")
                if row[1] == "completed":
                    conn.commit()
                    return IdempotencyReservation(replayed=True, submission_id=row[2])
                if row[1] == "failed" or bool(row[3]):
                    cur.execute(
                        """
                        UPDATE ops.agent_idempotency
                        SET state='processing', lease_token=%s,
                            lease_generation=lease_generation+1,
                            updated_at=clock_timestamp()
                        WHERE client_id=%s AND idempotency_hash=%s
                        """,
                        (lease_token, client_id, idempotency_hash),
                    )
                    conn.commit()
                    return IdempotencyReservation(replayed=False, lease_token=lease_token)
                raise ConflictErr(
                    "같은 Idempotency-Key 요청이 이미 처리 중입니다. 잠시 뒤 재시도하세요."
                )
        except (ConflictErr, InfraError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"에이전트 멱등 요청을 예약하지 못했습니다: {exc}") from exc

    def complete_idempotency(
        self,
        *,
        client_id: str,
        idempotency_hash: str,
        request_hash: str,
        submission_id: str,
        lease_token: str,
    ) -> None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    UPDATE ops.agent_idempotency
                    SET state='completed', submission_id=%s,
                        updated_at=clock_timestamp(),
                        retention_until=clock_timestamp() + interval '90 days'
                    WHERE client_id=%s AND idempotency_hash=%s AND request_hash=%s
                      AND state='processing' AND lease_token=%s
                    RETURNING client_id
                    """,
                    (
                        submission_id,
                        client_id,
                        idempotency_hash,
                        request_hash,
                        lease_token,
                    ),
                ).fetchone()
                if row is None:
                    raise ConflictErr("멱등 처리 lease가 만료되거나 다른 worker로 이전됐습니다.")
        except (ConflictErr, InfraError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"에이전트 멱등 완료를 기록하지 못했습니다: {exc}") from exc

    def fail_idempotency(
        self,
        *,
        client_id: str,
        idempotency_hash: str,
        request_hash: str,
        lease_token: str,
    ) -> bool:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    UPDATE ops.agent_idempotency
                    SET state='failed', updated_at=clock_timestamp()
                    WHERE client_id=%s AND idempotency_hash=%s AND request_hash=%s
                      AND state='processing' AND lease_token=%s
                    RETURNING client_id
                    """,
                    (client_id, idempotency_hash, request_hash, lease_token),
                ).fetchone()
                return row is not None
        except InfraError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"에이전트 멱등 실패 상태를 기록하지 못했습니다: {exc}") from exc

    def prune_history(self) -> list[dict]:
        """보존기간이 지난 비식별 운영 이력을 admin 권한으로 파기한다."""

        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT relation_name, deleted_rows FROM ops.prune_agent_history()"
                ).fetchall()
            return [{"relation": row[0], "deleted": int(row[1])} for row in rows]
        except InfraError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"에이전트 보존기간 만료 이력을 파기하지 못했습니다: {exc}") from exc

    def append_audit(self, record: AgentAuditRecord) -> None:
        """자유형 원문 없이 고정 필드만 append한다."""

        values = asdict(record)
        refs = [str(ref)[:160] for ref in values.pop("citation_refs")[:50]]
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO ops.agent_api_audit (
                        audit_event_id, client_id, operation, required_scope,
                        subject_hash, request_hash, response_hash, trace_hash,
                        source_event_hash, http_status, latency_ms, verdict,
                        abstained, reason_code, rule_engine_version, model_profile,
                        policy_version_ref, citation_refs
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    )
                    """,
                    (
                        uuid.uuid4().hex,
                        values["client_id"],
                        values["operation"],
                        values["required_scope"],
                        values["subject_hash"],
                        values["request_hash"] or None,
                        values["response_hash"],
                        values["trace_hash"],
                        values["source_event_hash"],
                        values["http_status"],
                        values["latency_ms"],
                        (values["verdict"] or None),
                        values["abstained"],
                        (values["reason_code"] or None),
                        (values["rule_engine_version"] or None),
                        (values["model_profile"] or None),
                        (values["policy_version_ref"] or None),
                        refs,
                    ),
                )
        except InfraError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"에이전트 API 감사를 기록하지 못했습니다: {exc}") from exc

    def create_client(
        self,
        *,
        client_id: str,
        display_name: str,
        raw_key: str,
        scopes: set[str] | frozenset[str] | list[str],
        rate_limit_per_minute: int,
    ) -> None:
        client = validate_client_id(client_id)
        if parse_api_key(raw_key) != client:
            raise ValidationErr("API 키의 client_id와 등록 client_id가 다릅니다.")
        normalized_scopes = validate_scopes(scopes)
        if not (1 <= rate_limit_per_minute <= 60000):
            raise ValidationErr("rate-limit은 분당 1~60000 범위여야 합니다.")
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO ops.agent_client (
                        client_id, display_name, api_key_hash, key_fingerprint,
                        scopes, rate_limit_per_minute
                    ) VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        client,
                        display_name.strip() or client,
                        hash_api_key(raw_key),
                        key_fingerprint(raw_key),
                        sorted(normalized_scopes),
                        rate_limit_per_minute,
                    ),
                )
        except (InfraError, ValidationErr):
            raise
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "sqlstate", None) == "23505":
                raise ConflictErr("이미 존재하는 client_id 또는 키입니다.") from exc
            raise InfraError(f"에이전트 클라이언트를 등록하지 못했습니다: {exc}") from exc

    def rotate_client_key(self, *, client_id: str, raw_key: str) -> None:
        client = validate_client_id(client_id)
        if parse_api_key(raw_key) != client:
            raise ValidationErr("API 키의 client_id와 등록 client_id가 다릅니다.")
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    UPDATE ops.agent_client
                    SET api_key_hash=%s, key_fingerprint=%s,
                        key_rotated_at=clock_timestamp(), updated_at=clock_timestamp()
                    WHERE client_id=%s
                    RETURNING client_id
                    """,
                    (hash_api_key(raw_key), key_fingerprint(raw_key), client),
                ).fetchone()
                if row is None:
                    raise ValidationErr(f"에이전트 클라이언트를 찾을 수 없습니다: {client}")
        except (InfraError, ValidationErr):
            raise
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "sqlstate", None) == "23505":
                raise ConflictErr("다른 클라이언트가 이미 쓰는 키입니다.") from exc
            raise InfraError(f"에이전트 키를 교체하지 못했습니다: {exc}") from exc

    def disable_client(self, *, client_id: str) -> None:
        client = validate_client_id(client_id)
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    UPDATE ops.agent_client
                    SET status='disabled', updated_at=clock_timestamp()
                    WHERE client_id=%s RETURNING client_id
                    """,
                    (client,),
                ).fetchone()
                if row is None:
                    raise ValidationErr(f"에이전트 클라이언트를 찾을 수 없습니다: {client}")
        except (InfraError, ValidationErr):
            raise
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"에이전트 클라이언트를 비활성화하지 못했습니다: {exc}") from exc

    def list_clients(self) -> list[dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT client_id, display_name, key_fingerprint, scopes,
                           rate_limit_per_minute, status, created_at,
                           key_rotated_at
                    FROM ops.agent_client ORDER BY client_id
                    """
                ).fetchall()
            return [
                {
                    "client_id": row[0],
                    "display_name": row[1],
                    "key_fingerprint": row[2],
                    "scopes": list(row[3]),
                    "rate_limit_per_minute": row[4],
                    "status": row[5],
                    "created_at": row[6].isoformat(),
                    "key_rotated_at": row[7].isoformat() if row[7] else None,
                }
                for row in rows
            ]
        except InfraError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"에이전트 클라이언트 목록을 읽지 못했습니다: {exc}") from exc

    def list_client_mirror_snapshots(self) -> list[dict]:
        """보험 원장 미러 비교용 snapshot을 반환한다(raw API key는 반환하지 않는다)."""

        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT client_id, display_name, api_key_hash,
                           rate_limit_per_minute, status
                    FROM ops.agent_client ORDER BY client_id
                    """
                ).fetchall()
            return [
                {
                    "agent_client_id": row[0],
                    "name": row[1],
                    "api_key_hash": row[2],
                    "rate_limit_rpm": int(row[3]),
                    "status": row[4],
                }
                for row in rows
            ]
        except InfraError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"에이전트 미러 snapshot을 읽지 못했습니다: {exc}") from exc


__all__ = ["PgAgentAccess"]
