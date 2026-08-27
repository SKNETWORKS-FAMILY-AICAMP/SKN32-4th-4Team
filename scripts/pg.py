"""userspace PostgreSQL+pgvector 라이프사이클 (Phase 3, Docker 없이).

conda env `pgv`(postgresql 16.14 + pgvector 0.8.3)의 바이너리를 **최소 clean 환경**으로 기동한다.
Windows에서 큰 PATH로 띄우면 쿼리 백엔드가 0xC0000142(DLL init 실패)로 죽으므로, env를 최소화한다.

사용:
    python -m scripts.pg start    # 클러스터 initdb(최초) + 서버 기동(포트 5433)
    python -m scripts.pg stop
    python -m scripts.pg status
    python -m scripts.pg init      # DB + 확장만 (보험 인덱스용 부트스트랩)
    python -m scripts.pg init-demo # 별도 insurance_demo DB + 합성 스키마
    python -m scripts.pg init-agent # 별도 insurance_agent DB + 외부 에이전트 보안 스키마
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_PGDATA = _PROJECT / "data" / "pgdata"
_ENV = Path(os.environ.get("PGV_ENV", r"C:/Users/playdata2/anaconda3/envs/pgv"))
_BIN = _ENV / "Library" / "bin"
_PORT = "5433"
_DBNAME = "mall_vec"
_DEMO_DBNAME = "insurance_demo"
_AGENT_DBNAME = "insurance_agent"


def _clean_env() -> dict:
    """0xC0000142 회피: 최소 PATH만 담은 환경(자식 백엔드 DLL init 정상화)."""
    system32 = r"C:\Windows\System32"
    windows = r"C:\Windows"
    return {
        "SYSTEMROOT": windows,
        "PATH": f"{_BIN};{system32};{windows}",
    }


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, env=_clean_env(), capture_output=True, text=True)


def cmd_start() -> None:
    if not (_PGDATA / "postgresql.conf").exists():
        _PGDATA.mkdir(parents=True, exist_ok=True)
        r = _run([str(_BIN / "initdb.exe"), "-D", str(_PGDATA), "-U", "postgres",
                  "--auth-local=trust", "--auth-host=trust", "--encoding=UTF8"])
        print("[initdb]", "OK" if r.returncode == 0 else r.stderr[-300:])
    # postgres를 창 없이 백그라운드로 기동한다. 로그는 server.log에 남긴다.
    logf = open(_PGDATA / "server.log", "ab")
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [str(_BIN / "postgres.exe"), "-D", str(_PGDATA), "-p", _PORT],
        env=_clean_env(), stdout=logf, stderr=logf,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    print(f"[start] postgres 기동 시도(port {_PORT}). status로 확인하세요.")


def cmd_stop() -> None:
    r = _run([str(_BIN / "pg_ctl.exe"), "-D", str(_PGDATA), "stop", "-m", "fast"])
    print("[stop]", "OK" if r.returncode == 0 else r.stderr[-300:])


def cmd_status() -> None:
    import psycopg

    from app.core.config import get_settings

    try:
        psycopg.connect(get_settings().PGVECTOR_DSN, connect_timeout=3).close()
        print("[status] 연결 OK: PG 기동 중")
    except Exception as exc:  # noqa: BLE001
        print(f"[status] 연결 실패(PG 미기동?): {exc}")
        sys.exit(1)


def _ensure_database(dbname: str = _DBNAME) -> None:
    """`mall_vec` 데이터베이스를 만든다(멱등).

    ★없었다 — `start` 는 클러스터만 만들고 `setup` 은 `mall_vec` 에 바로 붙었다.
      새 기계에서는 `FATAL: database "mall_vec" does not exist` 로 막힌다.
      "먼저 PG를 기동하세요"라는 메시지가 나오지만 **PG 는 이미 떠 있어서**
      사실을 잘못 전한다(CLAUDE.md §3).
    """
    import psycopg

    admin = f"host=127.0.0.1 port={_PORT} user=postgres dbname=postgres"
    with psycopg.connect(admin, connect_timeout=5, autocommit=True) as conn:
        try:
            #: CREATE DATABASE에는 IF NOT EXISTS가 없다. 바로 시도하고 동시 생성도
            #: DuplicateDatabase로 같은 멱등 성공으로 취급한다.
            conn.execute(f'CREATE DATABASE "{dbname}"')
        except psycopg.errors.DuplicateDatabase:
            print(f"[setup] 데이터베이스 {dbname} 이미 있음")
            return
        print(f"[setup] 데이터베이스 {dbname} 생성")


def _ensure_extensions() -> None:
    """`vector`·`pg_trgm` 확장을 만든다(멱등).

    ★`get_conn()` 이 `register_vector()` 를 호출하는데, 확장이 없으면
      `vector type not found in the database` 로 죽는다. 즉 **확장은 연결보다 먼저**여야 한다.
      여기서는 확장 등록 없이 맨 psycopg 로 붙어서 만든다.
    """
    import psycopg

    dsn = f"host=127.0.0.1 port={_PORT} user=postgres dbname={_DBNAME}"
    with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    print("[setup] 확장 vector·pg_trgm 준비")


def cmd_init() -> None:
    """데이터베이스 + 확장만 만든다. **코퍼스 적재는 하지 않는다.**

    ★`setup` 은 `ingest_corpus()` 까지 하는데, 그 코퍼스는 `data/docs/` —
      **커머스 실습 문서**다. 보험 인덱스를 만들려고 부트스트랩했다가
      레거시 문서를 적재하게 되면 안 된다. 그래서 갈라 둔다.
    """
    _ensure_database()
    _ensure_extensions()
    print("[init] 완료. 인덱스 A 적재: python -m scripts.index.build_clause_index")


def cmd_init_demo() -> None:
    """별도 합성 DB 생성 + demo 마이그레이션 적용."""
    _ensure_database(_DEMO_DBNAME)
    dsn = f"postgresql://postgres@127.0.0.1:{_PORT}/{_DEMO_DBNAME}"
    r = subprocess.run(
        [sys.executable, "-m", "scripts.db.apply", "--dsn", dsn, "--track", "demo"],
        cwd=_PROJECT,
    )
    if r.returncode:
        raise SystemExit(r.returncode)
    print("[init-demo] 합성 PostgreSQL 준비 완료")


def cmd_init_agent() -> None:
    """별도 등록 에이전트 DB 생성 + 인증·감사 마이그레이션 적용."""
    _ensure_database(_AGENT_DBNAME)
    dsn = f"postgresql://postgres@127.0.0.1:{_PORT}/{_AGENT_DBNAME}"
    r = subprocess.run(
        [sys.executable, "-m", "scripts.db.apply", "--dsn", dsn, "--track", "agent"],
        cwd=_PROJECT,
    )
    if r.returncode:
        raise SystemExit(r.returncode)
    print("[init-agent] 외부 에이전트 PostgreSQL 준비 완료")


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(description="userspace pgvector 관리")
    p.add_argument(
        "command",
        choices=["start", "stop", "status", "init", "init-demo", "init-agent"],
    )
    args = p.parse_args(argv)
    {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "init": cmd_init,
        "init-demo": cmd_init_demo,
        "init-agent": cmd_init_agent,
    }[args.command]()


if __name__ == "__main__":
    main()
