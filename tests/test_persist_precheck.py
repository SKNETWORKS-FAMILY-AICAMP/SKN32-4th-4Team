"""사전판정 영속화 유스케이스의 fail-closed 입력 계약."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.domain.insurance import Verdict
from app.core.domain.precheck_result import PrecheckOutcome
from app.core.errors import ConfigError, InfraError, TransientInfraError, ValidationErr
from app.core.usecases.persist_precheck import (
    PersistPrecheckCommand,
    _occurrence,
    _request_key_hash,
)


def _command(*, key="12345678", secret="x" * 32):
    return PersistPrecheckCommand(
        outcome=PrecheckOutcome(verdict=Verdict.NEEDS_EXPERT),
        enrolled_on=date(2024, 1, 1),
        incident_on=date(2025, 1, 1),
        kcd_codes=("S72.0",),
        channel="test",
        idempotency_key=key,
        idempotency_secret=secret,
        request_snapshot={"a": 1},
        response_snapshot={"b": 2},
    )


def test_idempotency_key는_scope를_포함한_hmac이다():
    public = _request_key_hash(_command())
    agent = _request_key_hash(
        PersistPrecheckCommand(**{**_command().__dict__, "agent_client_id": "agent-a"})
    )
    assert len(public) == 64
    assert public != agent
    assert "12345678" not in public


@pytest.mark.parametrize(
    "command",
    [_command(key="short"), _command(secret="too-short")],
)
def test_약한_idempotency_입력은_거절한다(command):
    with pytest.raises(ValidationErr):
        _request_key_hash(command)


def test_occurrence는_release에_colon이_있어도_오른쪽에서_해석한다():
    sha = "a" * 64
    assert _occurrence(f"release:part:{sha}:annex:12") == (sha, "annex", 12)


@pytest.mark.parametrize("value", ["bad", f"r:{'a' * 64}:other:0", f"r:{'a' * 64}:clause:-1"])
def test_불완전한_occurrence를_추정하지_않는다(value):
    with pytest.raises(ValidationErr):
        _occurrence(value)


def test_precheck의_일시적_db오류는_retry_after와_error_code를_보존한다(monkeypatch):
    from app.main import create_app
    from app.routers import precheck as precheck_router

    class FailingGraph:
        def invoke(self, _input):
            raise TransientInfraError("temporary postgres failure", retry_after_seconds=3)

    monkeypatch.setattr(precheck_router, "_GRAPH", FailingGraph())
    response = TestClient(create_app()).post(
        "/v1/prechecks",
        json={
            "insurer": "Repo Test",
            "enrolled_on": "20240101",
            "kcd_codes": ["S72.0"],
        },
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "3"
    assert response.json()["error_code"] == "transient_infra_error"


def test_precheck_persistence_failure_is_structured_503(monkeypatch):
    from app.core.config import get_settings
    from app.main import create_app
    from app.routers import precheck as precheck_router
    from db.postgres.pg_insurance_repository import PgInsuranceRepository

    class StaticGraph:
        def invoke(self, _input):
            return PrecheckOutcome(
                verdict=Verdict.NEEDS_EXPERT,
                abstained=True,
                reason_code="no_evidence",
                rule_engine_version="test",
            ), {}

    def fail_from_settings(_cls):
        raise InfraError("보험 PostgreSQL에 연결할 수 없습니다.")

    monkeypatch.setenv("PRECHECK_PERSISTENCE", "postgres")
    monkeypatch.setenv("INSURANCE_PG_DSN", "postgresql://unavailable/insurance")
    monkeypatch.setenv("INSURANCE_IDEMPOTENCY_SECRET", "x" * 32)
    get_settings.cache_clear()
    monkeypatch.setattr(precheck_router, "_GRAPH", StaticGraph())
    monkeypatch.setattr(
        PgInsuranceRepository, "from_settings", classmethod(fail_from_settings)
    )
    try:
        response = TestClient(create_app(), raise_server_exceptions=False).post(
            "/v1/prechecks",
            json={
                "insurer": "Repo Test",
                "enrolled_on": "20240101",
                "incident_on": "20250101",
                "kcd_codes": ["S72.0"],
            },
            headers={"Idempotency-Key": "precheck-failure-1"},
        )
        assert response.status_code == 503
        assert response.json()["ok"] is False
        assert response.json()["error_code"] == "infra_error"
    finally:
        get_settings.cache_clear()


def test_registered_agent_postgres저장은_client원장_동기화전까지_거절한다(
    monkeypatch,
):
    from app.core.config import get_settings
    from app.routers import precheck as precheck_router
    from app.schemas.precheck import PrecheckRequest

    class StaticGraph:
        def invoke(self, _input):
            return PrecheckOutcome(
                verdict=Verdict.NEEDS_EXPERT,
                abstained=True,
                rule_engine_version="test",
            ), {}

    monkeypatch.setenv("PRECHECK_PERSISTENCE", "postgres")
    monkeypatch.setenv("INSURANCE_PG_DSN", "postgresql://must-not-connect/blocked")
    monkeypatch.setenv("INSURANCE_IDEMPOTENCY_SECRET", "x" * 32)
    get_settings.cache_clear()
    monkeypatch.setattr(precheck_router, "_GRAPH", StaticGraph())
    try:
        with pytest.raises(ConfigError, match="원장 동기화"):
            precheck_router.create_precheck_for_registered_agent(
                PrecheckRequest(
                    insurer="Repo Test",
                    enrolled_on="20240101",
                    incident_on="20250101",
                    kcd_codes=["S72.0"],
                    client_ref="agent-a",
                ),
                idempotency_key="agent-key-0001",
            )
    finally:
        get_settings.cache_clear()
