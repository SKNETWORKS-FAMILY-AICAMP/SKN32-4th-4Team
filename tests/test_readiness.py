"""TEST-OPS-READY-001 — 기동/데이터 분리 readiness (REQ-OPS-01)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.obs import readiness


@pytest.fixture(autouse=True)
def _disable_external_agent_probe(monkeypatch):
    """Readiness unit tests must not depend on a live external-agent DB."""
    settings = get_settings().model_copy(update={"AGENT_API_ENABLED": False})
    monkeypatch.setattr(readiness, "get_settings", lambda: settings)


def test_readiness_reports_expected_fields():
    s = readiness.check_readiness()
    for key in (
        "ready",
        "db_tables_ready",
        "missing_tables",
        "clause_index_ready",
        "accepted_release",
        "clause_index",
        "candidate_fact_sources",
        "hint",
    ):
        assert key in s


def _isolate_ready_insurance_components(monkeypatch, tmp_path) -> None:
    """한 readiness 단위 시험이 로컬 DB·승인 산출물·외부 DB 상태에 기대지 않게 한다."""
    from app import composition
    from app.adapters import demo_submission_store
    from app.core import candidate_fact_registry

    fake_settings = SimpleNamespace(
        DATABASE_URL=f"sqlite:///{tmp_path / 'unused.sqlite3'}",
        SQLITE_LEGACY_ENABLED=True,
        AUTH_PERSISTENCE="sqlite",
        OPS_PERSISTENCE="sqlite",
        PRECHECK_PERSISTENCE="off",
        OUTCOME_PERSISTENCE="file",
        AGENT_API_ENABLED=False,
        INSURANCE_PG_DSN="",
    )
    monkeypatch.setattr(readiness, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(readiness, "_required_sqlite_tables", lambda _settings: ())
    monkeypatch.setattr(readiness, "_existing_sqlite_tables", lambda _settings, _required: set())
    monkeypatch.setattr(composition, "_clause_store_kind", lambda: "file")
    monkeypatch.setattr(
        readiness,
        "_accepted_release_state",
        lambda _store: {"backend": "file", "required": True, "ready": True},
    )
    monkeypatch.setattr(
        candidate_fact_registry,
        "check_candidate_fact_sources",
        lambda: {"configured": True, "required": True, "ready": True},
    )
    monkeypatch.setattr(demo_submission_store, "backend_name", lambda: "file")


def test_insurance_readiness_reports_the_active_clause_index(monkeypatch, tmp_path):
    _isolate_ready_insurance_components(monkeypatch, tmp_path)

    s = readiness.check_readiness()

    assert s["clause_index_ready"] is True
    assert s["ready"] is True
    assert s["hint"] is None


def test_broken_accepted_release_fails_closed(monkeypatch, tmp_path):
    _isolate_ready_insurance_components(monkeypatch, tmp_path)
    monkeypatch.setattr(
        readiness,
        "_accepted_release_state",
        lambda _store: {
            "backend": "file",
            "required": True,
            "ready": False,
            "reason": "test mutation",
        },
    )

    s = readiness.check_readiness()

    assert s["clause_index_ready"] is False
    assert s["accepted_release"]["ready"] is False
    assert s["clause_index"]["ready"] is False
    assert s["ready"] is False
    assert "승인" in s["hint"]


def test_readiness_endpoint(client):
    r = client.get("/api/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert "ready" in body
    assert "components" in body
    for sensitive in (
        "missing_tables",
        "reason",
        "database",
        "schema",
        "hint",
        "details",
    ):
        assert sensitive not in r.text


def test_public_readiness_keeps_only_boolean_health(monkeypatch):
    monkeypatch.setattr(
        readiness,
        "check_readiness",
        lambda: {
            "ready": False,
            "db_tables_ready": False,
            "missing_tables": ["secret_table"],
            "clause_index_ready": False,
            "hint": "postgresql://runtime:secret@db.internal/insurance",
            "accepted_release": {"required": True, "ready": False, "reason": "secret"},
            "clause_index": {"required": True, "ready": False, "reason": "secret"},
            "candidate_fact_sources": {"ready": True, "details": ["private"]},
            "demo_store": {"ready": False, "reason": "db.internal"},
            "insurance_postgres": {"required": False},
            "agent_postgres": {"required": False},
            "ops_postgres": {"required": False},
        },
    )

    public = readiness.public_readiness()

    assert public == {
        "ready": False,
        "db_tables_ready": False,
        "clause_index_ready": False,
        "components": {
            "accepted_release": False,
            "clause_index": False,
            "candidate_fact_sources": True,
            "demo_store": False,
            "insurance_postgres": None,
            "agent_postgres": None,
            "ops_postgres": None,
        },
    }
    rendered = str(public)
    assert "secret" not in rendered
    assert "db.internal" not in rendered


def test_pg_readiness_failure_is_structured():
    result = readiness._pg_readiness_failure(
        "postgres", required=True, configured=False,
        exc=RuntimeError("postgresql://runtime:top-secret@db.internal/insurance"),
    )

    assert result["backend"] == "postgres"
    assert result["required"] is True
    assert result["configured"] is False
    assert result["ready"] is False
    assert "top-secret" not in result["reason"]
    assert "db.internal" not in result["reason"]


def test_missing_sqlite_is_not_created(tmp_path):
    path = tmp_path / "missing.sqlite3"
    settings = SimpleNamespace(
        DATABASE_URL=f"sqlite:///{path.as_posix()}",
        SQLITE_LEGACY_ENABLED=True,
    )

    existing = readiness._existing_sqlite_tables(settings, ("users",))

    assert existing == set()
    assert not path.exists()


def test_sqlite_readiness_uses_read_only_connection(monkeypatch, tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with readiness.sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE users (id integer primary key)")
    real_connect = readiness.sqlite3.connect
    captured: dict[str, object] = {}

    def checked_connect(database, *args, **kwargs):
        captured.update(database=database, **kwargs)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(readiness.sqlite3, "connect", checked_connect)
    settings = SimpleNamespace(
        DATABASE_URL=f"sqlite:///{path.as_posix()}",
        SQLITE_LEGACY_ENABLED=True,
    )

    existing = readiness._existing_sqlite_tables(settings, ("users",))

    assert existing == {"users"}
    assert captured["uri"] is True
    assert "mode=ro" in str(captured["database"])


def test_file_clause_store_skips_postgres_probe(monkeypatch):
    from app import composition

    monkeypatch.setattr(composition, "_clause_store_kind", lambda: "file")
    monkeypatch.setattr(
        readiness,
        "_accepted_release_state",
        lambda _store: {"backend": "file", "required": True, "ready": True},
    )
    monkeypatch.setattr(
        readiness,
        "_clause_index_state",
        lambda: pytest.fail("file clause store must not probe PostgreSQL"),
    )

    result = readiness.check_readiness()

    assert result["clause_index"] == {
        "backend": "file",
        "checked": True,
        "required": True,
        "ready": True,
    }
