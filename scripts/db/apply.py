# -*- coding: utf-8 -*-
"""번호 붙은 SQL 을 순서대로 적용한다. **forward-only.**

    python -m scripts.db.apply --dsn postgresql://... [--dry-run]

★왜 alembic 이 아닌가
    저장소에 alembic 이 없고 되돌릴 이력도 없다. 도입 자체가 일이다.
    번호 붙은 SQL + 얇은 적용기가 지금 규모에 맞는다. 되돌리기가 필요해지면
    롤백 파일을 억지로 만들지 말고 **보정 마이그레이션을 앞으로 더한다.**

★이 적용기가 지키는 것 (코덱스 2라운드 권고)
    1. 적용 이력·checksum 을 DB 에 남긴다 — 같은 번호가 다른 내용으로 재적용되면 멈춘다
    2. advisory lock — 두 사람이 동시에 돌려도 하나만 진행한다
    3. ★DDL 과 이력 INSERT 가 **한 트랜잭션**이다 — 그래서 .sql 안에 BEGIN/COMMIT 을
       두지 않는다. 파일이 스스로 커밋하면 "적용됐는데 이력이 없는" 상태가 생긴다
    4. 오류 즉시 중단 — 다음 파일로 넘어가지 않는다
    5. --dry-run 은 DB 를 건드리지 않는다 — 이력 테이블도 만들지 않는다
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parents[2] / "db" / "migrations" / "postgres"
TRACKS = {
    "core": (HERE, 0x5F3A_1C02),
    "demo": (HERE / "demo", 0x5F3A_1C03),
    "agent": (HERE / "agent", 0x5F3A_1C04),
}


def _ledger_name(track: str, path: pathlib.Path) -> str:
    """기존 core 이력 키는 바꾸지 않는다. 바꾸면 적용된 DDL을 재실행한다."""
    return path.name if track == "core" else f"{track}/{path.name}"

#: ★이력은 `public` 에 둔다. `ops` 는 001 이 만드는 것이라 여기서 먼저 만들면 충돌한다.
LEDGER = """
CREATE TABLE IF NOT EXISTS public.schema_migration (
    filename    text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    applied_by  text NOT NULL DEFAULT current_user
);
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()   # ★전체를 남긴다


def _apply_one(cur, sql_text: str, ledger_name: str, digest: str) -> None:
    """DDL과 migration ledger 기록을 호출자의 한 트랜잭션에서 실행한다."""
    cur.execute(sql_text)
    cur.execute(
        "INSERT INTO public.schema_migration (filename, checksum) VALUES (%s, %s)",
        (ledger_name, digest),
    )


def _assert_core_preflight(cur) -> None:
    """core migration이 중간 적용 후에야 권한 부족으로 멈추는 일을 예방한다."""
    cur.execute(
        "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("현재 PostgreSQL role 정보를 확인할 수 없다")
    is_superuser = bool(row[0])

    cur.execute(
        "SELECT rolname FROM pg_roles "
        "WHERE rolname IN ('insurance_owner','insurance_app')"
    )
    existing_roles = {role for (role,) in cur.fetchall()}
    missing_roles = {"insurance_owner", "insurance_app"} - existing_roles
    if missing_roles and not is_superuser:
        names = ", ".join(sorted(missing_roles))
        raise RuntimeError(
            "core migration 사전검사 실패: 비-superuser 실행 전에 관리자 계정으로 "
            f"role을 먼저 생성해야 한다 ({names})"
        )

    if "insurance_owner" in existing_roles and not is_superuser:
        cur.execute(
            "SELECT pg_has_role(current_user, 'insurance_owner', 'MEMBER')"
        )
        if not cur.fetchone()[0]:
            raise RuntimeError(
                "core migration 사전검사 실패: 현재 로그인 role은 "
                "insurance_owner의 MEMBER여야 한다"
            )

    cur.execute(
        "SELECT count(*) FROM pg_namespace "
        "WHERE nspname IN ('core','app','ops')"
    )
    if cur.fetchone()[0] < 3:
        cur.execute(
            "SELECT has_database_privilege(current_user, current_database(), 'CREATE')"
        )
        if not cur.fetchone()[0]:
            raise RuntimeError(
                "core migration 사전검사 실패: 초기 schema 생성을 위한 "
                "database CREATE 권한이 없다"
            )

    cur.execute(
        """
        WITH required(name) AS (
            VALUES ('vector'), ('pg_trgm'), ('pgcrypto')
        ), availability AS (
            SELECT name, bool_or(trusted) AS trusted
            FROM pg_available_extension_versions
            GROUP BY name
        )
        SELECT r.name,
               (a.name IS NOT NULL) AS available,
               coalesce(a.trusted, false) AS trusted
        FROM required r
        LEFT JOIN pg_extension e ON e.extname = r.name
        LEFT JOIN availability a ON a.name = r.name
        WHERE e.extname IS NULL
        ORDER BY r.name
        """
    )
    missing_extensions = cur.fetchall()
    unavailable = [name for name, available, _trusted in missing_extensions if not available]
    if unavailable:
        raise RuntimeError(
            "core migration 사전검사 실패: PostgreSQL 서버에 extension이 없다 "
            f"({', '.join(unavailable)})"
        )
    privileged = [
        name
        for name, _available, trusted in missing_extensions
        if not trusted and not is_superuser
    ]
    if privileged:
        raise RuntimeError(
            "core migration 사전검사 실패: 비-superuser 적용 전에 관리자 계정으로 "
            f"extension을 먼저 설치해야 한다 ({', '.join(privileged)})"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN", ""))
    ap.add_argument("--track", choices=sorted(TRACKS), default="core")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        print("DSN 이 없다. --dsn 또는 PG_DSN 환경변수를 주라.", file=sys.stderr)
        return 2

    migration_dir, lock_key = TRACKS[args.track]
    files = sorted(p for p in migration_dir.glob("*.sql"))
    if not files:
        print("적용할 .sql 이 없다.", file=sys.stderr)
        return 2

    import psycopg

    #: ★dry-run 은 읽기만 한다. 이력 테이블도 만들지 않는다.
    if args.dry_run:
        with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.schema_migration')")
            done = {}
            if cur.fetchone()[0] is not None:
                cur.execute("SELECT filename, checksum FROM public.schema_migration")
                done = dict(cur.fetchall())
        checksum_conflict = False
        for path in files:
            sql = path.read_text(encoding="utf-8")
            digest = _sha(sql)
            ledger_name = _ledger_name(args.track, path)
            prev = done.get(ledger_name)
            mark = "skip " if prev == digest else ("STOP " if prev else "would")
            checksum_conflict = checksum_conflict or bool(prev and prev != digest)
            print(f"  {mark}  {ledger_name}  ({len(sql):,}자, {digest[:16]}…)")
        return 1 if checksum_conflict else 0

    #: ★락과 DDL 을 같은 세션에서 돌린다. 세션이 끊기면 락도 풀린다.
    with psycopg.connect(args.dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
        if not cur.fetchone()[0]:
            print("다른 적용이 진행 중이다(advisory lock). 중단한다.", file=sys.stderr)
            return 1
        conn.commit()
        try:
            if args.track == "core":
                _assert_core_preflight(cur)

            #: 최초 DB에서도 ledger bootstrap부터 track lock 안에서 수행한다.
            cur.execute(LEDGER)
            conn.commit()
            cur.execute("SELECT filename, checksum FROM public.schema_migration")
            done = dict(cur.fetchall())

            for path in files:
                sql = path.read_text(encoding="utf-8")
                digest = _sha(sql)
                ledger_name = _ledger_name(args.track, path)
                prev = done.get(ledger_name)

                if prev == digest:
                    print(f"  skip    {ledger_name}")
                    continue
                if prev is not None:
                    print(
                        f"  STOP    {ledger_name}  이미 적용됐는데 내용이 바뀌었다\n"
                        f"          적용본 {prev[:16]}… / 현재 {digest[:16]}…\n"
                        f"          되돌리지 말고 새 번호의 보정 마이그레이션을 만들라.",
                        file=sys.stderr,
                    )
                    return 1

                #: ★DDL + 이력이 한 트랜잭션. 중간에 죽으면 둘 다 롤백된다.
                _apply_one(cur, sql, ledger_name, digest)
                conn.commit()
                print(f"  applied {ledger_name}  ({digest[:16]}…)")
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
            conn.commit()

    print("완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
