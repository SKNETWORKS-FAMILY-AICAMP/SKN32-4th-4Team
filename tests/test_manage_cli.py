"""운영 CLI가 격리된 커머스 시드 데이터에 다시 의존하지 않는지 검사한다."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _run_manage(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env is not None:
        run_env.update(env)
    run_env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "scripts.manage", *args],
        cwd=ROOT,
        env=run_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def test_manage_help_does_not_offer_commerce_seed() -> None:
    result = _run_manage("--help")

    assert result.returncode == 0, result.stderr
    assert "seed" not in result.stdout


def test_removed_seed_command_is_rejected_before_file_access() -> None:
    result = _run_manage("seed")
    output = result.stdout + result.stderr

    assert result.returncode == 2
    assert "seed" in output
    assert "products.csv" not in output
    assert "시딩 CSV가 없습니다" not in output


def test_manage_help_does_not_offer_legacy_faiss_ingest() -> None:
    result = _run_manage("--help")

    assert result.returncode == 0, result.stderr
    assert "ingest" not in result.stdout


def test_removed_ingest_command_is_rejected_before_rag_import() -> None:
    result = _run_manage("ingest")
    output = result.stdout + result.stderr

    assert result.returncode == 2
    assert "ingest" in output
    assert "app.rag.build_index" not in output


def test_migrate_succeeds_without_commerce_csvs(tmp_path: Path) -> None:
    assert not (ROOT / "data" / "products.csv").exists()
    assert not (ROOT / "data" / "inventory.csv").exists()

    db_path = tmp_path / "insurance.sqlite3"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    env["PYTHONIOENCODING"] = "utf-8"

    result = _run_manage("migrate", env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert db_path.is_file()
    assert db_path.stat().st_size > 0


def test_legacy_migrate_is_rejected_in_postgres_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "should-not-be-created.sqlite3"
    result = _run_manage(
        "migrate",
        env={
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "AUTH_PERSISTENCE": "postgres",
            "OPS_PERSISTENCE": "postgres",
            "SQLITE_LEGACY_ENABLED": "false",
        },
    )

    assert result.returncode != 0
    assert not db_path.exists()
    assert "PostgreSQL 운영 모드" in (result.stdout + result.stderr)


def test_active_docs_do_not_instruct_commerce_seed() -> None:
    #: ★목록을 손으로 관리했더니 `docs/submission/` 이 빠져 있었다(2026-08-04).
    #:   제출 문서 2개가 없어진 `manage seed` 를 설치 절차에 적어 두고 있었고,
    #:   심사자가 그대로 따라 하면 거기서 멈춘다. 목록 대신 **폴더를 훑는다.**
    targets = [ROOT / "README.md", ROOT / "docs" / "architecture.md"]
    targets += sorted((ROOT / "docs" / "submission").glob("*.md"))

    offenders = [
        p.relative_to(ROOT).as_posix()
        for p in targets
        if p.exists() and "python -m scripts.manage seed" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"제거된 커머스 시드 명령을 안내하는 문서: {offenders}"


def test_active_code_and_docs_do_not_instruct_legacy_faiss_ingest() -> None:
    targets = (
        ROOT / "README.md",
        ROOT / "docs" / "architecture.md",
        ROOT / "scripts" / "manage.py",
        ROOT / "app" / "main.py",
        ROOT / "app" / "obs" / "readiness.py",
    )
    offenders = []
    for path in targets:
        source = path.read_text(encoding="utf-8")
        if "scripts.manage ingest" in source or "cmd_ingest" in source:
            offenders.append(path.relative_to(ROOT).as_posix())

    assert not offenders, f"구형 FAISS ingest 현행 참조: {offenders}"
