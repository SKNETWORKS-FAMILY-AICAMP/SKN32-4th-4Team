"""등록 외부 에이전트 PostgreSQL 통합 테스트. 별도 insurance_agent DB가 필요하다."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest


_SUPER_DSN = "host=127.0.0.1 port=5433 user=postgres dbname=insurance_agent"
_RUNTIME_DSN = (
    "host=127.0.0.1 port=5433 user=insurance_agent_runtime dbname=insurance_agent"
)
_ADMIN_DSN = (
    "host=127.0.0.1 port=5433 user=insurance_agent_admin dbname=insurance_agent"
)


@pytest.fixture
def pg_agent_client():
    import psycopg

    from app.adapters.pg_agent_access import PgAgentAccess
    from app.core.domain.agent_access import generate_api_key

    admin_store = PgAgentAccess(_ADMIN_DSN)
    store = PgAgentAccess(_RUNTIME_DSN)
    client_id = f"test-{uuid.uuid4().hex[:12]}"
    raw_key = generate_api_key(client_id)
    admin_store.create_client(
        client_id=client_id,
        display_name="통합 테스트 클라이언트",
        raw_key=raw_key,
        scopes={"precheck:read", "observations:write"},
        rate_limit_per_minute=3,
    )
    yield store, client_id, raw_key

    # 이 fixture가 만든 정확한 client_id만 역참조 순서대로 정리한다.
    with psycopg.connect(_SUPER_DSN) as conn:
        conn.execute("DELETE FROM ops.agent_api_audit WHERE client_id=%s", (client_id,))
        conn.execute("DELETE FROM ops.agent_rate_event WHERE client_id=%s", (client_id,))
        conn.execute("DELETE FROM ops.agent_idempotency WHERE client_id=%s", (client_id,))
        conn.execute(
            "DELETE FROM ops.agent_client_auth_log WHERE authenticated_client_id=%s "
            "OR claimed_client_id=%s",
            (client_id, client_id),
        )
        conn.execute("DELETE FROM ops.agent_client WHERE client_id=%s", (client_id,))


@pytest.mark.pg
def test_pg_agent_auth_rate_idempotency_and_audit(pg_agent_client):
    import psycopg

    from app.core.domain.agent_access import AgentAuditRecord, sensitive_payload_hash
    from app.core.errors import ConflictErr

    store, client_id, raw_key = pg_agent_client
    secret = "integration-agent-hash-secret-32-characters"
    principal = store.authenticate(raw_key, trace_hash="a" * 64)
    assert principal.client_id == client_id
    assert principal.scopes == frozenset({"precheck:read", "observations:write"})

    subject_hash = sensitive_payload_hash(secret, "opaque-user-1")
    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(
            pool.map(
                lambda _: store.consume_rate_limit(
                    principal, subject_hash=subject_hash, operation="precheck"
                ),
                range(8),
            )
        )
    assert sum(decision.allowed for decision in decisions) == 3
    assert all(decision.retry_after_seconds == 60 for decision in decisions if not decision.allowed)

    request_hash = sensitive_payload_hash(secret, {"kcd_codes": ["F32"]})
    first = store.reserve_idempotency(
        client_id=client_id,
        idempotency_hash=sensitive_payload_hash(secret, "claim-event-0001"),
        request_hash=request_hash,
    )
    assert first.replayed is False
    store.complete_idempotency(
        client_id=client_id,
        idempotency_hash=sensitive_payload_hash(secret, "claim-event-0001"),
        request_hash=request_hash,
        submission_id="claim-event-0001",
        lease_token=first.lease_token,
    )
    retry = store.reserve_idempotency(
        client_id=client_id,
        idempotency_hash=sensitive_payload_hash(secret, "claim-event-0001"),
        request_hash=request_hash,
    )
    assert retry.replayed is True
    with pytest.raises(ConflictErr):
        store.reserve_idempotency(
            client_id=client_id,
            idempotency_hash=sensitive_payload_hash(secret, "claim-event-0001"),
            request_hash=sensitive_payload_hash(secret, {"kcd_codes": ["S72.0"]}),
        )

    store.append_audit(
        AgentAuditRecord(
            client_id=client_id,
            operation="precheck",
            required_scope="precheck:read",
            subject_hash=subject_hash,
            request_hash=request_hash,
            response_hash=sensitive_payload_hash(secret, {"verdict": "needs_expert"}),
            trace_hash="b" * 64,
            source_event_hash=None,
            http_status=200,
            latency_ms=7,
            verdict="needs_expert",
            abstained=True,
            reason_code="no_evidence",
        )
    )

    with psycopg.connect(_SUPER_DSN) as conn:
        client = conn.execute(
            "SELECT api_key_hash, scopes FROM ops.agent_client WHERE client_id=%s",
            (client_id,),
        ).fetchone()
        audit = conn.execute(
            "SELECT subject_hash, request_hash, response_hash, verdict, reason_code "
            "FROM ops.agent_api_audit WHERE client_id=%s",
            (client_id,),
        ).fetchone()
    assert raw_key not in str(client)
    assert "F32" not in str(audit)
    assert "opaque-user-1" not in str(audit)
    assert audit[:3] == (subject_hash, request_hash, sensitive_payload_hash(secret, {"verdict": "needs_expert"}))


@pytest.mark.pg
def test_stale_lease_cannot_overwrite_new_worker_completion(pg_agent_client):
    import psycopg

    from app.core.domain.agent_access import sensitive_payload_hash

    store, client_id, _raw_key = pg_agent_client
    secret = "integration-agent-hash-secret-32-characters"
    idem_hash = sensitive_payload_hash(secret, "lease-case-0001")
    request_hash = sensitive_payload_hash(secret, {"outcome": "paid"})
    worker_a = store.reserve_idempotency(
        client_id=client_id,
        idempotency_hash=idem_hash,
        request_hash=request_hash,
    )
    with psycopg.connect(_SUPER_DSN) as conn:
        conn.execute(
            "UPDATE ops.agent_idempotency SET updated_at=clock_timestamp()-interval '6 minutes' "
            "WHERE client_id=%s AND idempotency_hash=%s",
            (client_id, idem_hash),
        )
    worker_b = store.reserve_idempotency(
        client_id=client_id,
        idempotency_hash=idem_hash,
        request_hash=request_hash,
    )
    assert worker_a.lease_token != worker_b.lease_token
    store.complete_idempotency(
        client_id=client_id,
        idempotency_hash=idem_hash,
        request_hash=request_hash,
        submission_id="submission-from-b",
        lease_token=worker_b.lease_token,
    )
    assert store.fail_idempotency(
        client_id=client_id,
        idempotency_hash=idem_hash,
        request_hash=request_hash,
        lease_token=worker_a.lease_token,
    ) is False
    replay = store.reserve_idempotency(
        client_id=client_id,
        idempotency_hash=idem_hash,
        request_hash=request_hash,
    )
    assert replay.replayed is True
    assert replay.submission_id == "submission-from-b"


@pytest.mark.pg
def test_runtime_cannot_rewrite_client_or_audit_ledger(pg_agent_client):
    import psycopg

    _store, client_id, _raw_key = pg_agent_client
    for statement in (
        "UPDATE ops.agent_client SET status='disabled' WHERE client_id=%s",
        "DELETE FROM ops.agent_api_audit WHERE client_id=%s",
        "TRUNCATE ops.agent_client_auth_log",
    ):
        with psycopg.connect(_RUNTIME_DSN) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement, (client_id,) if "%s" in statement else None)


@pytest.mark.pg
def test_protected_http_path_uses_real_agent_registry(pg_agent_client, monkeypatch):
    import psycopg
    from fastapi.testclient import TestClient

    from app.application.agent_facade import get_agent_facade
    from app.core.config import get_settings

    _store, client_id, raw_key = pg_agent_client
    monkeypatch.setenv("AGENT_PG_DSN", _RUNTIME_DSN)
    monkeypatch.setenv("AGENT_HASH_SECRET", "integration-agent-hash-secret-32-characters")
    get_settings.cache_clear()

    class _Facade:
        def support_manifest(self):
            return {
                "schema_version": "v1",
                "rule_engine_version": "test",
                "require_confirmed_documents": True,
                "identification_mode": {},
                "total_policy_versions": 0,
                "confirmation": {},
                "insurers": {},
                "notes": [],
            }

    try:
        from app.agent_main import create_agent_app

        app = create_agent_app()
        app.dependency_overrides[get_agent_facade] = lambda: _Facade()
        response = TestClient(app).get(
            "/v1/agent/support-manifest",
            headers={
                "Authorization": f"Bearer {raw_key}",
                "X-Agent-Subject": "opaque-http-user-1",
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers["x-trace-id"]
        with psycopg.connect(_SUPER_DSN) as conn:
            row = conn.execute(
                "SELECT http_status, operation, required_scope "
                "FROM ops.agent_api_audit WHERE client_id=%s "
                "ORDER BY created_at DESC LIMIT 1",
                (client_id,),
            ).fetchone()
        assert row == (200, "support_manifest", "precheck:read")
    finally:
        get_settings.cache_clear()


def test_agent_postgres_failure_has_no_fallback():
    from app.adapters.pg_agent_access import PgAgentAccess
    from app.core.errors import InfraError

    store = PgAgentAccess("host=127.0.0.1 port=9 user=none dbname=none connect_timeout=1")
    with pytest.raises(InfraError, match="PostgreSQL"):
        store.authenticate("not-a-key", trace_hash="a" * 64)
