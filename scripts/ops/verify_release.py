"""Read-only release verification for the PostgreSQL insurance rollout.

The command is deliberately safe to run before a deployment.  It never applies
migrations or mirrors agent clients with ``apply=True``.  Connection checks are
only attempted when a DSN is supplied (explicitly or through settings).

Examples::

    python -m scripts.ops.verify_release --json
    python -m scripts.ops.verify_release --strict \
        --insurance-dsn "$INSURANCE_PG_DSN" \
        --insurance-admin-dsn "$INSURANCE_ADMIN_PG_DSN"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION_DIR = ROOT / "db" / "migrations" / "postgres"
EXPECTED_LATEST = "016_runtime_ownership_and_grants.sql"

# Support both documented module execution and direct script execution:
# ``python -m scripts.ops.verify_release`` and
# ``python scripts/ops/verify_release.py``.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _git_info() -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        completed.check_returncode()
        # porcelain의 첫 두 칸은 상태 열이다. 선행 공백을 지우면 경로 첫 글자가 잘린다.
        return completed.stdout.rstrip()

    try:
        status = run("status", "--porcelain")
        return {
            "available": True,
            "revision": run("rev-parse", "HEAD"),
            "branch": run("branch", "--show-current"),
            "worktree_dirty": bool(status),
            "changed_paths": [line[3:] for line in status.splitlines() if len(line) >= 4],
        }
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"available": False, "reason": str(exc)[:200]}


def _migration_info() -> dict[str, Any]:
    migrations = sorted(
        path for path in MIGRATION_DIR.glob("*.sql") if path.is_file()
    )
    entries = []
    for path in migrations:
        digest = hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        entries.append({"filename": path.name, "sha256": digest})
    latest = entries[-1]["filename"] if entries else None
    return {
        "directory": str(MIGRATION_DIR),
        "count": len(entries),
        "latest": latest,
        "expected_latest": EXPECTED_LATEST,
        "latest_matches": latest == EXPECTED_LATEST,
        "files": entries,
    }


def _skip(reason: str) -> dict[str, Any]:
    return {"configured": False, "checked": False, "ready": None, "reason": reason}


def _persistence_config() -> dict[str, Any]:
    """Check the non-secret production cutover switches without opening a DB."""
    try:
        from app.core.config import get_settings

        settings = get_settings()
        values = {
            "app_env": getattr(settings, "APP_ENV", "development"),
            "auth": settings.AUTH_PERSISTENCE,
            "ops": settings.OPS_PERSISTENCE,
            "precheck": settings.PRECHECK_PERSISTENCE,
            "outcome": settings.OUTCOME_PERSISTENCE,
            "demo_store_backend": getattr(settings, "DEMO_STORE_BACKEND", "file"),
            "sqlite_legacy_enabled": bool(settings.SQLITE_LEGACY_ENABLED),
            "database_url_is_sqlite": settings.DATABASE_URL.lower().startswith("sqlite"),
        }
        ready = (
            values["app_env"] == "production"
            and
            values["auth"] == "postgres"
            and values["ops"] == "postgres"
            and values["precheck"] == "postgres"
            and values["outcome"] == "postgres"
            and values["demo_store_backend"] == "postgres"
            and not values["sqlite_legacy_enabled"]
            and not values["database_url_is_sqlite"]
        )
        return {"configured": True, "checked": True, "ready": ready, **values}
    except Exception as exc:  # noqa: BLE001 - report config failure
        return {
            "configured": False,
            "checked": True,
            "ready": False,
            "reason": str(exc)[:200],
        }


def _insurance_readiness(dsn: str | None) -> dict[str, Any]:
    if not (dsn or "").strip():
        return _skip("INSURANCE_PG_DSN not supplied")
    try:
        from app.adapters.pg_insurance_repository import PgInsuranceRepository

        result = PgInsuranceRepository(dsn).readiness()
        return {"configured": True, "checked": True, **result}
    except Exception as exc:  # noqa: BLE001 - report the check, do not mask it
        return {
            "configured": True,
            "checked": True,
            "backend": "postgres",
            "ready": False,
            "reason": str(exc)[:200],
        }


def _agent_readiness(dsn: str | None) -> dict[str, Any]:
    if not (dsn or "").strip():
        return _skip("AGENT_PG_DSN not supplied")
    try:
        from app.adapters.pg_agent_access import PgAgentAccess

        result = PgAgentAccess(dsn).readiness()
        return {"configured": True, "checked": True, **result}
    except Exception as exc:  # noqa: BLE001 - report the check, do not mask it
        return {
            "configured": True,
            "checked": True,
            "backend": "postgres",
            "ready": False,
            "reason": str(exc)[:200],
        }


def _demo_readiness() -> dict[str, Any]:
    try:
        from app.core.config import get_settings

        settings = get_settings()
        if settings.DEMO_STORE_BACKEND != "postgres":
            return _skip("DEMO_STORE_BACKEND is not postgres")
        if not (settings.DEMO_PG_DSN or "").strip():
            return _skip("DEMO_PG_DSN not supplied")
        from db.postgres.pg_demo_submission_store import readiness

        return {"configured": True, "checked": True, **readiness()}
    except Exception as exc:  # noqa: BLE001 - report the check
        return {
            "configured": True,
            "checked": True,
            "backend": "postgres",
            "ready": False,
            "reason": str(exc)[:200],
        }


def _agent_mirror(
    source_dsn: str | None, insurance_admin_dsn: str | None
) -> dict[str, Any]:
    if not (source_dsn or "").strip():
        return _skip("agent source/admin DSN not supplied")
    if not (insurance_admin_dsn or "").strip():
        return _skip("INSURANCE_ADMIN_PG_DSN not supplied")
    try:
        from app.adapters.pg_agent_access import PgAgentAccess
        from app.adapters.pg_insurance_repository import PgInsuranceAdminRepository
        from app.core.usecases.sync_agent_clients import SyncAgentClients

        report = SyncAgentClients(
            PgAgentAccess(source_dsn), PgInsuranceAdminRepository(insurance_admin_dsn)
        ).run(apply=False)
        return {"configured": True, "checked": True, **report.as_dict()}
    except Exception as exc:  # noqa: BLE001 - report the check, do not mask it
        return {
            "configured": True,
            "checked": True,
            "in_sync": False,
            "reason": str(exc)[:200],
        }


def _load_defaults() -> tuple[str, str, str, str]:
    from app.core.config import get_settings

    settings = get_settings()
    return (
        settings.INSURANCE_PG_DSN,
        settings.INSURANCE_ADMIN_PG_DSN,
        settings.AGENT_ADMIN_PG_DSN,
        settings.AGENT_PG_DSN,
    )


def _is_failure(report: dict[str, Any], *, strict: bool) -> bool:
    migration = report["migrations"]
    if not migration["latest_matches"]:
        return True
    persistence = report["persistence_config"]
    if strict and not persistence.get("ready"):
        return True
    for key in ("insurance_readiness", "agent_readiness", "agent_mirror", "demo_readiness"):
        check = report[key]
        if check.get("checked") and not check.get("ready", check.get("in_sync", False)):
            return True
        if strict and not check.get("configured"):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    # Windows OpenSSH sessions may expose cp1252/stdout even when the repo and
    # JSON contain Korean text.  Keep remote release collection machine-readable.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="read-only PostgreSQL release verification")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--strict", action="store_true", help="fail when configured checks are missing or fail")
    parser.add_argument("--insurance-dsn", default=None)
    parser.add_argument("--insurance-admin-dsn", default=None)
    parser.add_argument("--agent-source-dsn", default=None)
    args = parser.parse_args(argv)

    try:
        defaults = _load_defaults()
    except Exception as exc:  # noqa: BLE001 - settings failures belong in the report
        defaults = ("", "", "", "")
        settings_error = str(exc)[:200]
    else:
        settings_error = None

    insurance_dsn = args.insurance_dsn if args.insurance_dsn is not None else defaults[0]
    insurance_admin_dsn = (
        args.insurance_admin_dsn
        if args.insurance_admin_dsn is not None
        else defaults[1]
    )
    agent_source_dsn = (
        args.agent_source_dsn if args.agent_source_dsn is not None else defaults[2]
    )
    agent_dsn = defaults[3]
    report: dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "git": _git_info(),
        "migrations": _migration_info(),
        "persistence_config": _persistence_config(),
        "insurance_readiness": _insurance_readiness(insurance_dsn),
        "agent_readiness": _agent_readiness(agent_dsn),
        "agent_mirror": _agent_mirror(agent_source_dsn, insurance_admin_dsn),
        "demo_readiness": _demo_readiness(),
    }
    if settings_error:
        report["settings_error"] = settings_error
    report["ok"] = not _is_failure(report, strict=args.strict)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"release verification: {'PASS' if report['ok'] else 'FAIL'}")
        print(f"revision: {report['git'].get('revision', 'unknown')}")
        print(f"migration latest: {report['migrations']['latest']}")
        print(f"persistence config: {'PASS' if report['persistence_config'].get('ready') else 'FAIL'}")
        print(f"insurance readiness: {report['insurance_readiness'].get('ready', 'skipped')}")
        print(f"agent mirror: {report['agent_mirror'].get('in_sync', 'skipped')}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
