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
#: ★★**이름을 박지 않는다** (2026-08-27 정정).
#:
#:   `EXPECTED_LATEST = "019_..."` 로 굳어 있었다. 020 을 더하자 `latest_matches` 가
#:   거짓이 되고 릴리스 점검이 **실패**했다 — 스키마가 더 최신인데 「어긋났다」고 답한 것이다.
#:   ★같은 결함을 오늘 `PgInsuranceRepository.readiness()` 에서도 고쳤다
#:     (거기도 `latest == "016_..."` 였다). **마이그레이션을 더할 때마다 소스를 고쳐야 하는
#:     구조는 언젠가 「시험을 고치는 대신 기능을 되돌리게」 만든다.**
#:
#:   진짜로 재야 하는 것은 「몇 번까지인가」가 아니라 **번호가 성한가**다 —
#:   빠진 번호도 겹친 번호도 없어야 한다. 그건 여기서 계산할 수 있다.
#:   적용 여부는 적용기가 checksum·advisory lock 으로 이미 지킨다.


def _numbering_faults(names: list[str]) -> list[str]:
    """번호가 빠졌거나 겹쳤나. **이게 진짜 불변식이다.**"""
    seen: dict[int, list[str]] = {}
    for name in names:
        head = name.split("_", 1)[0]
        if not head.isdigit():
            continue
        seen.setdefault(int(head), []).append(name)
    faults = [f"번호 겹침 {n:03d}: {', '.join(v)}" for n, v in sorted(seen.items()) if len(v) > 1]
    if seen:
        missing = sorted(set(range(min(seen), max(seen) + 1)) - set(seen))
        if missing:
            faults.append("빠진 번호: " + ", ".join(f"{n:03d}" for n in missing))
    return faults

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
    faults = _numbering_faults([e["filename"] for e in entries])
    return {
        "directory": str(MIGRATION_DIR),
        "count": len(entries),
        "latest": latest,
        #: ★번호가 성한가. 빠지거나 겹치면 **여기서 걸린다** — 그건 진짜 결함이다.
        #:   「몇 번까지인가」는 재지 않는다(위 주석).
        "numbering_faults": faults,
        "latest_matches": not faults,
        "files": entries,
    }


def _skip(reason: str) -> dict[str, Any]:
    return {"configured": False, "checked": False, "ready": None, "reason": reason}


def _postgres_pool() -> dict[str, Any]:
    try:
        from db.postgres.pool import pool_status

        return {"checked": True, **pool_status()}
    except Exception as exc:  # noqa: BLE001 - keep the release report machine-readable
        return {"checked": True, "pool_dependency": False, "reason": str(exc)[:200]}


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
            "clause_store": getattr(settings, "CLAUSE_STORE", "file"),
            "verified_cohort_store": getattr(settings, "VERIFIED_COHORT_STORE", "file"),
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
            and values["clause_store"] == "pg"
            and values["verified_cohort_store"] == "postgres"
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
        from db.postgres.pg_insurance_repository import PgInsuranceRepository

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
        from db.postgres.pg_agent_access import PgAgentAccess

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
        from db.postgres.pg_agent_access import PgAgentAccess
        from db.postgres.pg_insurance_repository import PgInsuranceAdminRepository
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
    if strict and not report["postgres_pool"].get("pool_dependency"):
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
        "postgres_pool": _postgres_pool(),
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
