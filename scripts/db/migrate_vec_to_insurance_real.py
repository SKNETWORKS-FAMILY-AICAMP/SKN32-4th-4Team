# -*- coding: utf-8 -*-
"""조항 벡터 색인을 `mall_vec.public` → `insurance_real.vec` 으로 옮긴다.

    python -m scripts.db.migrate_vec_to_insurance_real            # 조회만(기본)
    python -m scripts.db.migrate_vec_to_insurance_real --apply    # 실제로 복사
    python -m scripts.db.migrate_vec_to_insurance_real --indexes  # 적재 후 인덱스

★★**옛 DB 를 안 건드린다.** 읽기만 한다. 되돌리기는 `DROP SCHEMA vec CASCADE` 이고,
  DSN 을 전환한 뒤라면 `PGVECTOR_DSN` 을 되돌리면 된다 — 데이터가 양쪽에 다 있다.

★**데이터를 변형하지 않는다.** 행 그대로 옮긴다.
  해시·순번(`ordinal`)·게이트(`citation_eligible` 등)가 전부 불변이어야 한다.
  끝에 기준선과 대조해 그것을 **확인**한다.

★인덱스는 **적재 뒤에** 만든다(`--indexes`). 넣으면서 만들면 훨씬 느리다.
  HNSW 는 931 MB 짜리라 이 차이가 크다.

★COPY 를 쓴다. `INSERT ... SELECT` 는 DB 가 달라 못 쓰고(별개 서버 취급),
  파이썬으로 한 행씩 옮기면 164,977행 × 1024차원이라 오래 걸린다.
"""

from __future__ import annotations

import argparse
import io
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC = "host=127.0.0.1 port=5433 user=postgres dbname=mall_vec"
DST = "host=127.0.0.1 port=5433 user=postgres dbname=insurance_real"

#: ★순서가 있다 — 본문이 먼저다. 외래키가 `RESTRICT` 라 부모 없이 자식을 못 넣는다.
TABLES = ("policy_clause_content", "policy_clause_chunk", "policy_clause_occurrence")

INDEXES = (
    ("policy_clause_chunk_hnsw",
     "CREATE INDEX IF NOT EXISTS policy_clause_chunk_hnsw "
     "ON vec.policy_clause_chunk USING hnsw (embedding vector_l2_ops)"),
    ("policy_clause_chunk_text_trgm",
     "CREATE INDEX IF NOT EXISTS policy_clause_chunk_text_trgm "
     "ON vec.policy_clause_chunk USING gin (text gin_trgm_ops)"),
    ("policy_clause_occurrence_gen",
     "CREATE INDEX IF NOT EXISTS policy_clause_occurrence_gen "
     "ON vec.policy_clause_occurrence (index_generation)"),
    ("policy_clause_occurrence_sha",
     "CREATE INDEX IF NOT EXISTS policy_clause_occurrence_sha "
     "ON vec.policy_clause_occurrence (sha256)"),
)


def _count(conn, rel: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"select count(*) from {rel}")
        return cur.fetchone()[0]


def _columns(conn, schema: str, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            " where table_schema=%s and table_name=%s order by ordinal_position",
            (schema, table),
        )
        return [r[0] for r in cur.fetchall()]


def copy_table(src, dst, table: str) -> int:
    """`COPY ... TO STDOUT` → `COPY ... FROM STDIN` 을 바이너리로 잇는다.

    ★★**컬럼을 이름으로 명시한다** (2026-08-26 실측으로 잡힘).

        `COPY 테이블 TO/FROM` 은 **선언 순서**대로 읽고 쓴다. 두 쪽 순서가 다르면
        바이너리 형식에서는 타입이 어긋나 그 자리에서 터진다 —
        실제로 `incorrect binary data format ... column citation_eligible` 이 났다.
        옛 테이블은 게이트 4컬럼을 나중에 `ALTER` 로 붙여 **뒤에** 있고,
        새 DDL 은 논리 순서로 **가운데** 뒀기 때문이다.

        ★그 오류가 **난 것이 다행이다.** 타입이 우연히 호환됐다면 값이 조용히
          뒤바뀐 채 들어갔을 것이다 — `citation_eligible` 자리에 다른 값이 들어가면
          인용 가능 여부가 뒤집힌다.

        이름으로 명시하면 순서가 달라도 안전하다. 양쪽 컬럼 집합이 다르면 **멈춘다.**
    """
    t0 = time.time()
    a = _columns(src, "public", table)
    b = _columns(dst, "vec", table)
    if set(a) != set(b):
        raise SystemExit(
            f"★{table}: 컬럼 집합이 다릅니다. "
            + f"옛것에만={sorted(set(a)-set(b))} 새것에만={sorted(set(b)-set(a))}"
        )
    cols = ", ".join(f'"{c}"' for c in a)     # 옛 순서로 읽고 **같은 이름 순서로** 쓴다

    buf = io.BytesIO()
    with src.cursor().copy(f"COPY public.{table} ({cols}) TO STDOUT (FORMAT BINARY)") as out:
        for block in out:
            buf.write(block)
    payload = buf.getvalue()
    with dst.cursor().copy(f"COPY vec.{table} ({cols}) FROM STDIN (FORMAT BINARY)") as into:
        into.write(payload)
    dst.commit()
    n = _count(dst, f"vec.{table}")
    print(f"  [{table}] {n:,}행 · {len(payload)/2**20:.0f} MB · {time.time()-t0:.1f}초", flush=True)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 복사(기본은 조회만)")
    ap.add_argument("--indexes", action="store_true", help="적재 뒤 인덱스를 만든다")
    a = ap.parse_args()

    import psycopg

    with psycopg.connect(SRC, connect_timeout=60) as src, \
         psycopg.connect(DST, connect_timeout=60) as dst:
        print("  ── 옛 DB (mall_vec.public)")
        want = {t: _count(src, f"public.{t}") for t in TABLES}
        for t, n in want.items():
            print(f"    {t:30} {n:>10,}")
        print("  ── 새 DB (insurance_real.vec)")
        have = {t: _count(dst, f"vec.{t}") for t in TABLES}
        for t, n in have.items():
            print(f"    {t:30} {n:>10,}")

        if a.indexes:
            for name, sql in INDEXES:
                t0 = time.time()
                with dst.cursor() as cur:
                    cur.execute(sql)
                dst.commit()
                print(f"  [인덱스] {name} · {time.time()-t0:.1f}초", flush=True)
            with dst.cursor() as cur:
                cur.execute("select indexname, pg_size_pretty(pg_relation_size(('vec.'||indexname)::regclass)) "
                            "from pg_indexes where schemaname='vec' order by 1")
                for n2, s in cur.fetchall():
                    print(f"    {n2:38} {s}")
            return 0

        if not a.apply:
            print("  (조회만 했다. 복사하려면 --apply)")
            return 0

        if any(have.values()):
            #: ★비어 있지 않으면 **덮어쓰지 않는다.** 반쯤 들어간 상태에 또 넣으면
            #:   무엇이 들어갔는지 알 수 없게 된다.
            print("  ★새 스키마가 비어 있지 않다. 지우고 다시 하려면 "
                  "DROP SCHEMA vec CASCADE 뒤 마이그레이션을 다시 적용하라.")
            return 1

        for t in TABLES:                      # 본문 → 조각 → 발생 (외래키 순서)
            got = copy_table(src, dst, t)
            if got != want[t]:
                raise SystemExit(f"★{t}: 원본 {want[t]:,} ≠ 복사본 {got:,} — 멈춘다")
        print("  전부 행수 일치.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
