"""합성 트랙 PostgreSQL 통합 테스트. 별도 insurance_demo DB가 필요하다."""

from __future__ import annotations

import uuid

import pytest


def test_postgres_장애를_파일_결과로_폴백하지_않는다(monkeypatch, tmp_path):
    from app.adapters import demo_submission_store as store
    from app.core.config import get_settings
    from app.core.errors import InfraError

    monkeypatch.setenv("DEMO_STORE_BACKEND", "postgres")
    monkeypatch.setenv(
        "DEMO_PG_DSN", "host=127.0.0.1 port=9 user=none dbname=none connect_timeout=1"
    )
    monkeypatch.setattr(store, "_SUBMISSIONS", tmp_path / "must-not-be-used")
    get_settings.cache_clear()
    try:
        with pytest.raises(InfraError, match="PostgreSQL"):
            store.counts()
        assert not (tmp_path / "must-not-be-used").exists()
    finally:
        get_settings.cache_clear()


@pytest.fixture
def pg_demo(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("DEMO_STORE_BACKEND", "postgres")
    monkeypatch.setenv(
        "DEMO_PG_DSN", "host=127.0.0.1 port=5433 user=postgres dbname=insurance_demo"
    )
    get_settings.cache_clear()
    created: list[str] = []
    yield created

    import psycopg

    with psycopg.connect(get_settings().DEMO_PG_DSN) as conn:
        if created:
            conn.execute(
                "DELETE FROM demo.verification_event WHERE submission_id = ANY(%s)",
                (created,),
            )
            conn.execute(
                "DELETE FROM demo.submission WHERE submission_id = ANY(%s)",
                (created,),
            )
        conn.commit()
    get_settings.cache_clear()


def _payload(*, outcome="paid", code="S72.0"):
    run_id = uuid.uuid4().hex[:12]
    return {
        "client_ref": "sim-agent-001",
        "insurer": "삼성화재",
        "enrolled_on": "20260804",
        "kcd_codes": [code],
        "product_id": "",
        "age_band": "30대",
        "outcome": outcome,
        "outcome_reason": "시뮬레이션 생성",
        "idempotency_key": f"sim-{run_id}-001-001",
        "simulation_run_id": run_id,
        "simulation_case_no": 1,
        "auto_validate": True,
    }


@pytest.mark.pg
def test_pg_자동정합성_승격과_멱등성이_원자적으로_동작한다(pg_demo):
    from app.adapters import demo_submission_store as store
    from app.adapters.pg_demo_submission_store import fetch_cohort
    from app.core.domain.insurance import KcdCode
    from app.core.domain.synthetic_validation import RULE_VERSION

    payload = _payload()
    first = store.store(payload, auto_validate=True)
    pg_demo.append(first.submission_id)
    assert first.stored is True
    assert first.promoted is True
    assert first.verification == "synthetic_consistency"
    assert first.rule_version == RULE_VERSION

    retry = store.store(payload, auto_validate=True)
    assert retry.duplicate is True
    assert retry.submission_id == first.submission_id
    assert retry.promoted is True

    stats = fetch_cohort(
        kcd_code=KcdCode(version_label="", code="S72.0", name_ko=""),
        product_id="",
        age_band="30대",
    )
    assert stats.n >= 1
    assert dict(stats.by_verification)["synthetic_consistency"] >= 1


@pytest.mark.pg
def test_pg_같은_멱등키의_다른_payload는_409_충돌이다(pg_demo):
    from app.adapters import demo_submission_store as store
    from app.core.errors import ConflictErr

    payload = _payload()
    first = store.store(payload)
    pg_demo.append(first.submission_id)

    changed = {**payload, "outcome": "denied"}
    with pytest.raises(ConflictErr, match="다른 합성 payload"):
        store.store(changed)


@pytest.mark.pg
def test_pg_게이트_실패는_기록하되_승격하지_않는다(pg_demo):
    import psycopg

    from app.adapters import demo_submission_store as store
    from app.core.config import get_settings
    from app.core.domain.synthetic_validation import RULE_VERSION

    payload = _payload(code="C30~C39")
    result = store.store(payload, auto_validate=True)
    pg_demo.append(result.submission_id)
    assert result.stored is True
    assert result.promoted is False
    assert result.verification == "rejected"
    assert "single_kcd_code_valid" in result.reason_codes

    with psycopg.connect(get_settings().DEMO_PG_DSN) as conn:
        row = conn.execute(
            "SELECT decision, rule_version, reason_codes "
            "FROM demo.verification_event WHERE submission_id=%s",
            (result.submission_id,),
        ).fetchone()
    assert row[0] == "rejected"
    assert row[1] == RULE_VERSION
    assert "single_kcd_code_valid" in row[2]


@pytest.mark.pg
def test_합성_DB에는_실제_사례_스키마가_없다(pg_demo):
    import psycopg

    from app.core.config import get_settings

    with psycopg.connect(get_settings().DEMO_PG_DSN) as conn:
        schemas = {
            r[0] for r in conn.execute(
                "SELECT schema_name FROM information_schema.schemata"
            ).fetchall()
        }
        tables = conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema='demo' AND table_type='BASE TABLE' ORDER BY table_name"
        ).fetchall()
    assert {"app", "core", "ops"}.isdisjoint(schemas)
    assert {r[1] for r in tables} == {"submission", "verification_event"}
