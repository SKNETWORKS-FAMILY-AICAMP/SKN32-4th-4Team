# -*- coding: utf-8 -*-
"""`vec` 스키마 되돌리기 — 스키마와 **원장 기록을 함께** 지운다.

    python -m scripts.db.rollback_vec_schema            # 조회만(기본)
    python -m scripts.db.rollback_vec_schema --apply    # 실제로 되돌린다

★★**`DROP SCHEMA vec CASCADE` 만으로는 안 된다** (2026-08-26, 코덱스 사전감사 P0-⑤).

    `public.schema_migration` 에 `017`·`018` 이 기록돼 있다. 스키마만 지우면
    적용기가 그 둘을 `skip` 해서 **테이블을 다시 만들지 않는다.**
    「되돌렸다」고 믿는데 다음 적용이 아무 일도 안 하는 상태가 된다.

★이 스크립트는 **옛 DB(`mall_vec`)를 안 건드린다.** 거기 데이터가 그대로 있는 것이
  이 되돌리기가 성립하는 전제다. 그 전제를 먼저 확인하고 시작한다.

★기본은 조회만. 지우려면 `--apply` 를 명시해야 한다.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DST = "host=127.0.0.1 port=5433 user=postgres dbname=insurance_real"
SRC = "host=127.0.0.1 port=5433 user=postgres dbname=mall_vec"
LEDGER_ROWS = ("017_vec_clause_index.sql", "018_vec_ownership.sql",
               "019_drop_dead_generation_default.sql")
TABLES = ("policy_clause_content", "policy_clause_chunk", "policy_clause_occurrence")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 되돌린다(기본은 조회만)")
    a = ap.parse_args()

    import psycopg

    #: ★★**옛 DB 에 데이터가 있는지 먼저 본다.** 없으면 이건 되돌리기가 아니라 파괴다.
    with psycopg.connect(SRC, connect_timeout=30) as src, src.cursor() as cur:
        src_counts = {}
        for t in TABLES:
            cur.execute(f"select count(*) from public.{t}")
            src_counts[t] = cur.fetchone()[0]
    print("  ── 옛 DB (mall_vec.public) — 되돌아갈 곳")
    for t, n in src_counts.items():
        print(f"    {t:30} {n:>10,}")
    if not all(src_counts.values()):
        print("  ★옛 DB 가 비어 있다. 지우면 되돌릴 곳이 없다 — 멈춘다.")
        return 1

    with psycopg.connect(DST, connect_timeout=30) as dst:
        with dst.cursor() as cur:
            cur.execute("select count(*) from information_schema.schemata where schema_name='vec'")
            has_schema = bool(cur.fetchone()[0])
            cur.execute("select filename from public.schema_migration where filename = any(%s) order by 1",
                        (list(LEDGER_ROWS),))
            in_ledger = [r[0] for r in cur.fetchall()]
        print("  ── 새 DB (insurance_real)")
        print(f"    vec 스키마      {'있음' if has_schema else '없음'}")
        print(f"    원장 기록       {in_ledger or '없음'}")

        if not a.apply:
            print("  (조회만 했다. 되돌리려면 --apply)")
            return 0

        with dst.cursor() as cur:
            if has_schema:
                cur.execute("drop schema vec cascade")
                print("    drop schema vec cascade")
            if in_ledger:
                #: ★여기가 핵심이다. 이걸 안 지우면 다음 적용이 skip 한다.
                cur.execute("delete from public.schema_migration where filename = any(%s)",
                            (list(LEDGER_ROWS),))
                print(f"    원장에서 {cur.rowcount}건 삭제 — 다음 적용이 다시 만든다")
        dst.commit()
    print("  되돌렸다. 다시 세우려면: python -m scripts.db.apply --dsn ... --track core")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
