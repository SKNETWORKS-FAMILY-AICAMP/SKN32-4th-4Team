from __future__ import annotations

import json
import subprocess

from scripts.ops import verify_release


def test_git_info_does_not_treat_stderr_warning_as_changed_path(monkeypatch):
    def fake_run(command, **kwargs):
        args = tuple(command[1:])
        outputs = {
            ("status", "--porcelain"): " M app/main.py\n",
            ("rev-parse", "HEAD"): "abc123\n",
            ("branch", "--show-current"): "develop\n",
        }
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=outputs[args],
            stderr="warning: could not open directory '.tmp/pytest_verify/'\n",
        )

    monkeypatch.setattr(verify_release.subprocess, "run", fake_run)

    report = verify_release._git_info()

    assert report["available"] is True
    assert report["worktree_dirty"] is True
    assert report["changed_paths"] == ["app/main.py"]


def test_migration_numbering_is_sound():
    """★**개수를 박지 않는다** (2026-08-27 정정).

    앞 판은 `count == 19` 와 `latest == EXPECTED_LATEST` 를 걸어 뒀다.
    마이그레이션을 하나 더할 때마다 시험이 깨진다 — 그러면 사람은
    **시험을 고치는 대신 기능을 되돌리기 쉽다.** 오늘 020 을 더하고 실제로 깨졌다.

    ★재야 할 것은 「몇 번까지인가」가 아니라 **번호가 성한가**다 —
      빠진 번호도 겹친 번호도 없어야 한다. 그건 개수와 무관하게 늘 참이어야 한다.
    """
    report = verify_release._migration_info()

    assert report["count"] > 0
    assert report["latest"], "마이그레이션을 하나도 못 찾았다"
    assert report["numbering_faults"] == [], (
        f"마이그레이션 번호가 성하지 않다: {report['numbering_faults']}"
    )
    assert report["latest_matches"] is True
    assert all(len(item["sha256"]) == 64 for item in report["files"])


def test_release_baseline_runs_without_dsn(monkeypatch, capsys):
    monkeypatch.setattr(verify_release, "_load_defaults", lambda: ("", "", "", ""))

    assert verify_release.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["read_only"] is True
    assert report["ok"] is True
    assert report["insurance_readiness"]["checked"] is False
    assert report["agent_readiness"]["checked"] is False
    assert report["agent_mirror"]["checked"] is False


def test_strict_baseline_rejects_missing_connections(monkeypatch):
    monkeypatch.setattr(verify_release, "_load_defaults", lambda: ("", "", "", ""))

    assert verify_release.main(["--strict", "--json"]) == 1


def test_production_persistence_gate_requires_postgres_and_disabled_sqlite(monkeypatch):
    class FakeSettings:
        APP_ENV = "production"
        AUTH_PERSISTENCE = "postgres"
        OPS_PERSISTENCE = "postgres"
        PRECHECK_PERSISTENCE = "postgres"
        OUTCOME_PERSISTENCE = "postgres"
        DEMO_STORE_BACKEND = "postgres"
        CLAUSE_STORE = "pg"
        VERIFIED_COHORT_STORE = "postgres"
        SQLITE_LEGACY_ENABLED = False
        DATABASE_URL = "postgresql+psycopg://runtime@db.example/insurance_real"

    monkeypatch.setattr("app.core.config.get_settings", lambda: FakeSettings())
    result = verify_release._persistence_config()

    assert result["ready"] is True
    assert result["database_url_is_sqlite"] is False


def test_persistence_gate_rejects_non_production_environment(monkeypatch):
    class FakeSettings:
        APP_ENV = "development"
        AUTH_PERSISTENCE = "postgres"
        OPS_PERSISTENCE = "postgres"
        PRECHECK_PERSISTENCE = "postgres"
        OUTCOME_PERSISTENCE = "postgres"
        DEMO_STORE_BACKEND = "postgres"
        CLAUSE_STORE = "pg"
        VERIFIED_COHORT_STORE = "postgres"
        SQLITE_LEGACY_ENABLED = False
        DATABASE_URL = "postgresql+psycopg://runtime@db.example/insurance_real"

    monkeypatch.setattr("app.core.config.get_settings", lambda: FakeSettings())
    result = verify_release._persistence_config()

    assert result["ready"] is False
    assert result["app_env"] == "development"


def test_production_env_template_is_postgres_only():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "config" / "production.env.example").read_text(encoding="utf-8")
    active = [line.strip() for line in text.splitlines()
              if line.strip() and not line.lstrip().startswith("#")]

    assert "APP_ENV=production" in active
    assert any(line.startswith("DATABASE_URL=postgresql") for line in active)
    assert "SQLITE_LEGACY_ENABLED=false" in active
    assert "CLAUSE_STORE=pg" in active
    assert not any(line.startswith("DATABASE_URL=sqlite") for line in active)
