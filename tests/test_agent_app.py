"""등록 외부 에이전트 앱의 인증·scope·격리·감사 계약."""

from __future__ import annotations

import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.application.agent_facade import get_agent_facade
from app.auth.agent_client import get_agent_principal, get_agent_registry
from app.core.domain.agent_access import (
    AgentPrincipal,
    IdempotencyReservation,
    RateLimitDecision,
    generate_api_key,
    hash_api_key,
    parse_api_key,
)
from app.core.errors import AuthErr, InfraError
from app.schemas.precheck import PrecheckResult


_SUBJECT = "opaque-user-0001"


class _Registry:
    def __init__(self, *, allowed: bool = True, auth_error: Exception | None = None):
        self.allowed = allowed
        self.auth_error = auth_error
        self.auth_events = []
        self.audits = []
        self.reservations = {}

    def record_auth_attempt(self, **kwargs):
        self.auth_events.append(kwargs)

    def authenticate(self, _raw_key, *, trace_hash):
        if self.auth_error:
            raise self.auth_error
        return _principal({"precheck:read"})

    def consume_rate_limit(self, *_args, **_kwargs):
        return RateLimitDecision(self.allowed, 17 if not self.allowed else 0)

    def append_audit(self, record):
        self.audits.append(record)

    def reserve_idempotency(self, *, client_id, idempotency_hash, request_hash):
        key = (client_id, idempotency_hash)
        old = self.reservations.get(key)
        if old:
            if old[0] != request_hash:
                from app.core.errors import ConflictErr

                raise ConflictErr("same key, different payload")
            return IdempotencyReservation(replayed=True, submission_id=old[1])
        self.reservations[key] = (request_hash, "pending")
        return IdempotencyReservation(replayed=False, lease_token="f" * 32)

    def complete_idempotency(
        self, *, client_id, idempotency_hash, request_hash, submission_id, lease_token
    ):
        self.reservations[(client_id, idempotency_hash)] = (request_hash, submission_id)

    def fail_idempotency(self, **_kwargs):
        return None


class _Facade:
    def __init__(self):
        self.calls = []

    def support_manifest(self):
        self.calls.append(("support", {}))
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

    def precheck(self, body, *, client_id):
        self.calls.append(("precheck", {"body": body, "client_id": client_id}))
        return PrecheckResult(
            verdict="needs_expert",
            abstained=True,
            reason_code="no_evidence",
            trace_id="trace-test",
        )

    def explain_term(self, body):
        self.calls.append(("terms", {"body": body}))
        return {
            "schema_version": "v1",
            "intent": "term",
            "message": "설명",
            "next_action": "none",
            "term": "통원",
            "found": True,
            "quotes": [],
            "total_passages": 0,
            "insurers": [],
            "warnings": [],
            "llm": {"used": False, "provider": None, "model": None},
        }

    def cohort(self, **kwargs):
        self.calls.append(("cohort", kwargs))
        return {
            "schema_version": "v1",
            "data_source": "verified_real",
            "n": 0,
            "approved_n": 0,
            "denied_n": 0,
            "min_sample": 30,
            "min_sample_met": False,
            "approval_rate": None,
            "approval_ci": None,
            "headline": "검증된 사례가 없습니다.",
            "by_verification": {},
            "warnings": [],
        }

    def submit_observation(self, body, *, client_id, idempotency_key):
        self.calls.append(
            (
                "observation",
                {"body": body, "client_id": client_id, "idempotency_key": idempotency_key},
            )
        )
        return SimpleNamespace(
            stored=True,
            duplicate=False,
            idempotency_key=idempotency_key,
            submission_id=f"submission-{idempotency_key}",
        )


def _principal(scopes) -> AgentPrincipal:
    return AgentPrincipal(
        client_id="agent-a",
        display_name="Agent A",
        scopes=frozenset(scopes),
        rate_limit_per_minute=60,
        key_fingerprint="a" * 16,
    )


def _client(scopes, *, registry=None, facade=None):
    from app.agent_main import create_agent_app

    app = create_agent_app()
    registry = registry or _Registry()
    facade = facade or _Facade()
    app.dependency_overrides[get_agent_principal] = lambda: _principal(scopes)
    app.dependency_overrides[get_agent_registry] = lambda: registry
    app.dependency_overrides[get_agent_facade] = lambda: facade
    return TestClient(app), registry, facade


def test_agent_public_readiness_does_not_expose_database_details(monkeypatch):
    from app import agent_main
    from db.postgres import pg_agent_access

    settings = SimpleNamespace(
        BRAND_NAME="test",
        AGENT_API_ENABLED=True,
        AGENT_PG_DSN="postgresql://runtime:top-secret@db.internal/agent",
    )

    class FakeAccess:
        def __init__(self, _dsn):
            pass

        def readiness(self):
            return {
                "ready": False,
                "database": "internal_agent",
                "reason": "top-secret at db.internal",
            }

    monkeypatch.setattr(agent_main, "get_settings", lambda: settings)
    monkeypatch.setattr(pg_agent_access, "PgAgentAccess", FakeAccess)

    response = TestClient(agent_main.create_agent_app()).get("/api/health/ready")

    assert response.status_code == 200
    assert response.json() == {"enabled": True, "ready": False}
    assert "top-secret" not in response.text
    assert "db.internal" not in response.text


def _request(client: TestClient, endpoint: str):
    headers = {"X-Agent-Subject": _SUBJECT}
    if endpoint == "support":
        return client.get("/v1/agent/support-manifest", headers=headers)
    if endpoint == "precheck":
        return client.post(
            "/v1/agent/prechecks",
            headers=headers,
            json={"insurer": "가보험", "enrolled_on": "20200101", "kcd_codes": ["F32"]},
        )
    if endpoint == "terms":
        return client.post(
            "/v1/agent/terms/explain", headers=headers, json={"message": "통원 뜻"}
        )
    if endpoint == "cohort":
        return client.get("/v1/agent/cohorts?code=F32", headers=headers)
    return client.post(
        "/v1/agent/observations",
        headers={**headers, "Idempotency-Key": "case-key-0001"},
        json={"insurer": "가보험", "kcd_codes": ["F32"], "outcome": "pending"},
    )


def test_api_key_roundtrip_and_no_raw_storage_value():
    raw = generate_api_key("agent-a")
    assert parse_api_key(raw) == "agent-a"
    assert raw not in hash_api_key(raw)
    assert len(hash_api_key(raw)) == 64


def test_agent_router_is_fail_closed_and_surface_isolated():
    from app.agent_main import create_agent_app
    from app.main import customer_app
    from app.routers.agent import router

    assert any(getattr(dep, "dependency", None) is get_agent_principal for dep in router.dependencies)
    assert all(route.path.startswith("/v1/agent/") for route in router.routes)

    app = create_agent_app()
    registry = _Registry()
    app.dependency_overrides[get_agent_registry] = lambda: registry
    agent_client = TestClient(app)
    assert agent_client.post("/v1/agent/prechecks", json={}).status_code == 401
    for path in ("/v1/prechecks", "/v1/demo/observations", "/api/admin/agents", "/static/insurance.html"):
        assert agent_client.get(path).status_code == 404

    customer_paths = {route.path for route in customer_app.routes}
    assert "/v1/prechecks" in customer_paths
    assert "/v1/cohorts" in customer_paths
    assert "/v1/observations" in customer_paths
    assert not any(path.startswith("/v1/agent/") for path in customer_paths)


@pytest.mark.parametrize("endpoint", ["support", "precheck", "terms", "cohort", "observation"])
def test_every_agent_endpoint_requires_bearer(endpoint):
    from app.agent_main import create_agent_app

    app = create_agent_app()
    registry = _Registry()
    app.dependency_overrides[get_agent_registry] = lambda: registry
    response = _request(TestClient(app), endpoint)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert registry.auth_events[-1]["result"] == "missing"


@pytest.mark.parametrize(
    ("endpoint", "scope", "expected_status"),
    [
        ("support", "precheck:read", 200),
        ("precheck", "precheck:read", 200),
        ("terms", "terms:read", 200),
        ("cohort", "cohort:read", 200),
        ("observation", "observations:write", 202),
    ],
)
def test_agent_scope_matrix(endpoint, scope, expected_status):
    client, registry, facade = _client({scope})
    response = _request(client, endpoint)
    assert response.status_code == expected_status, response.text
    assert facade.calls
    assert registry.audits

    denied_client, denied_registry, denied_facade = _client({"terms:read" if scope != "terms:read" else "cohort:read"})
    denied = _request(denied_client, endpoint)
    assert denied.status_code == 403
    assert not denied_facade.calls
    assert denied_registry.audits[-1].http_status == 403


def test_observation_identity_header_and_replay_contract():
    client, _registry, facade = _client({"observations:write"})
    base = {"insurer": "가보험", "kcd_codes": ["F32"], "outcome": "paid"}
    headers = {"X-Agent-Subject": _SUBJECT}

    assert client.post("/v1/agent/observations", headers=headers, json=base).status_code == 422
    spoofed = client.post(
        "/v1/agent/observations",
        headers={**headers, "Idempotency-Key": "case-key-0002"},
        json={**base, "client_ref": "agent-b"},
    )
    assert spoofed.status_code == 422
    assert not facade.calls

    valid_headers = {**headers, "Idempotency-Key": "case-key-0002"}
    first = client.post("/v1/agent/observations", headers=valid_headers, json=base)
    second = client.post("/v1/agent/observations", headers=valid_headers, json=base)
    assert first.status_code == second.status_code == 202
    assert first.json()["stored"] is True
    assert second.json()["replayed"] is True
    assert len(facade.calls) == 1
    assert facade.calls[0][1]["client_id"] == "agent-a"
    assert facade.calls[0][1]["idempotency_key"] == "case-key-0002"
    changed = client.post(
        "/v1/agent/observations",
        headers=valid_headers,
        json={**base, "outcome": "denied"},
    )
    assert changed.status_code == 409


def test_limiter_and_registry_fail_closed():
    limited = _Registry(allowed=False)
    client, _, facade = _client({"precheck:read"}, registry=limited)
    response = _request(client, "precheck")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "17"
    assert not facade.calls

    from app.agent_main import create_agent_app

    app = create_agent_app()
    broken = _Registry(auth_error=InfraError("registry down"))
    app.dependency_overrides[get_agent_registry] = lambda: broken
    response = TestClient(app).get(
        "/v1/agent/support-manifest",
        headers={
            "Authorization": f"Bearer {generate_api_key('agent-a')}",
            "X-Agent-Subject": _SUBJECT,
        },
    )
    assert response.status_code == 503


def test_disabled_flag_blocks_direct_asgi_start(monkeypatch):
    from app.agent_main import create_agent_app
    from app.core.config import get_settings

    monkeypatch.setenv("AGENT_API_ENABLED", "false")
    get_settings.cache_clear()
    try:
        app = create_agent_app()
        registry = _Registry()
        app.dependency_overrides[get_agent_registry] = lambda: registry
        response = TestClient(app).get(
            "/v1/agent/support-manifest",
            headers={
                "Authorization": f"Bearer {generate_api_key('agent-a')}",
                "X-Agent-Subject": _SUBJECT,
            },
        )
        assert response.status_code == 503
        assert response.json()["error_code"] == "config_error"
    finally:
        get_settings.cache_clear()


def test_audit_contains_hashes_not_medical_or_subject_plaintext():
    client, registry, _facade = _client({"precheck:read"})
    response = _request(client, "precheck")
    assert response.status_code == 200
    serialized = json.dumps(asdict(registry.audits[-1]), ensure_ascii=False)
    assert "F32" not in serialized
    assert _SUBJECT not in serialized
    assert len(registry.audits[-1].request_hash) == 64
    assert len(registry.audits[-1].subject_hash) == 64


def test_audit_failure_is_fail_closed():
    class _BrokenAudit(_Registry):
        def append_audit(self, _record):
            raise InfraError("audit down")

    client, _registry, _facade = _client({"precheck:read"}, registry=_BrokenAudit())
    response = _request(client, "support")
    assert response.status_code == 503
    assert response.json()["error_code"] == "agent_audit_unavailable"


def test_registered_precheck_does_not_copy_kcd_to_knowledge_gap(monkeypatch):
    from app.application.agent_facade import AgentFacade
    from app.core.domain.precheck_result import PrecheckOutcome
    from app.obs import agent_stream
    from app.obs import knowledge_gaps
    from app.routers import precheck as public_router
    from app.schemas.agent import AgentPrecheckRequest

    outcome = PrecheckOutcome(
        verdict="needs_expert",
        abstained=True,
        reason_code="no_evidence",
        message="",
        applied_policy=None,
        per_code=(),
        citations=(),
        candidates=(),
        rule_engine_version="test",
        extractor="test",
        trace_id="trace-test",
        warnings=(),
    )

    class _Graph:
        def invoke(self, _body):
            return outcome, {}

    copied = []
    monkeypatch.setattr(public_router, "_graph", lambda: _Graph())
    monkeypatch.setattr(knowledge_gaps, "record_gap_safe", copied.append)
    monkeypatch.setattr(agent_stream, "publish", lambda *_args, **_kwargs: None)
    result = AgentFacade().precheck(
        AgentPrecheckRequest(
            insurer="가보험",
            enrolled_on="20200101",
            kcd_codes=["F32"],
        ),
        client_id="agent-a",
    )
    assert result.reason_code == "no_evidence"
    assert copied == []


def test_agent_openapi_has_bearer_and_typed_success_responses():
    from app.agent_main import create_agent_app

    schema = TestClient(create_agent_app()).get("/openapi.json").json()
    bearer = schema["components"]["securitySchemes"]["AgentBearer"]
    assert bearer["type"] == "http" and bearer["scheme"] == "bearer"
    for path in ("/v1/agent/support-manifest", "/v1/agent/terms/explain"):
        operation = next(iter(schema["paths"][path].values()))
        response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert "$ref" in response_schema
