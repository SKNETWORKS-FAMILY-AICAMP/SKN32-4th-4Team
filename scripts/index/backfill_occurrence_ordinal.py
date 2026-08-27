"""이미 적재된 발생행에 **수록 순번(`ordinal`)** 을 매긴다 — 소급 적용용 CLI.

결함: `docs/reports/debugs/2026-08-25_1400_pg조항색인에_수록순번이_없어_인용검증이_전건_실패한다.md`

★**규칙은 여기 없다.** 순위를 매기는 방법은
  `db.postgres.pgvector_clause_index.assign_ordinals()` 한 곳에만 둔다.
  이 스크립트는 그것을 **소급 적용**하는 얇은 껍데기다 —
  같은 기능을 두 곳에 두면 어긋났을 때 무엇이 맞는지 알 수 없다(`RULE.md` §3.3).

★**앞으로의 적재에는 필요 없다.** `upsert_occurrences()` 가 넣은 뒤 스스로 매긴다(2026-08-25).
  이 스크립트는 **그 수정 이전에 적재된 행**을 위한 것이다.

★**멱등하다.** 여러 번 돌려도 같은 값이 된다.

사용:
    python -m scripts.index.backfill_occurrence_ordinal --dry-run   # 대상만 센다
    python -m scripts.index.backfill_occurrence_ordinal             # 매긴다
"""

from __future__ import annotations

import argparse


def main() -> int:
    p = argparse.ArgumentParser(description="적재된 발생행에 수록 순번 소급 적용")
    p.add_argument("--clause-tag", default=None, help="기본값은 승인 릴리스의 clause_tag")
    p.add_argument("--generation", default=None, help="기본값은 clause-tag 에서 유도")
    p.add_argument("--dry-run", action="store_true", help="쓰지 않고 대상만 센다")
    #: ★★**기본을 조회로 뒤집었다**(2026-08-26 · 코덱스 감사 P1).
    #:   인자 없이 실행하면 세대 전체 순번을 다시 매기던 것이 원래 동작이었다.
    #:   순번이 바뀌면 `occurrence_id` 가 바뀌고 = 어제 발급한 인용을 못 찾는다.
    p.add_argument("--apply", action="store_true",
                   help="실제로 다시 매긴다. 없으면 세기만 한다(기본).")
    args = p.parse_args()

    from app.core import release
    from db.postgres import pgvector_clause_index as ix
    #: ★연결은 `pgvector_index` 가 준다(`pg_clause_store` 와 **같은 경로**).
    #:   여기서 DSN 을 따로 만들면 판정이 보는 DB 와 다른 곳을 고칠 수 있다.
    from db.postgres.pgvector_index import get_conn

    rel = release.current()
    tag = args.clause_tag or rel.clause_tag
    generation = args.generation or ix.generation_of(tag)
    print(f"[대상] clause_tag={tag} · index_generation={generation} · 릴리스={rel.release_id}")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), count(ordinal) FROM policy_clause_occurrence "
                "WHERE index_generation = %s",
                (generation,),
            )
            total, filled = cur.fetchone()
        print(f"[현황] 발생 {total:,}행 · 순번 있음 {filled:,}행")
        #: ★★**세기만 하는 것을 먼저 보여 준다**(2026-08-26 · 코덱스 감사 P1).
        #:   순번이 바뀌면 `occurrence_id` 가 바뀌고, 그건 「어제 발급한 판정의 근거를
        #:   오늘 못 찾는다」는 뜻이다. 몇 행이 바뀔지 **보고 나서** 정하게 한다.
        would = ix.assign_ordinals(conn, generation=generation, dry_run=True)
        print(f"[예상] 다시 매길 행 {would:,}  "
              f"(그만큼 occurrence_id 가 바뀐다 — 이미 발급된 인용이 못 찾게 된다)")

        if args.dry_run:
            print("★ --dry-run 이었다. 아무것도 쓰지 않았다.")
            return 0

        if not args.apply:
            #: ★기본을 조회로 뒤집었다. 인자 없이 실행하면 세기만 한다.
            print("★ 쓰려면 --apply 를 명시하라. 세대 전체를 다시 매긴다.")
            return 0

        changed = ix.assign_ordinals(conn, generation=generation,
                                     #: ★전 세대 대상임을 **말해서** 부른다.
                                     scope="all_in_generation")

        #: ★매긴 뒤 **유일성을 직접 확인한다.** 이게 이 작업의 유일한 목적이다.
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM (
                    SELECT sha256, source_kind, ordinal
                      FROM policy_clause_occurrence
                     WHERE index_generation = %s AND ordinal IS NOT NULL
                     GROUP BY 1,2,3 HAVING count(*) > 1) x
                """,
                (generation,),
            )
            dupes = cur.fetchone()[0]
            #: ★인용 가능한 행(게이트가 채워진 행)에 순번이 빠지면 그 조항은 근거로 못 쓴다.
            cur.execute(
                "SELECT count(*) FROM policy_clause_occurrence "
                "WHERE index_generation = %s AND parse_status IS NOT NULL AND ordinal IS NULL",
                (generation,),
            )
            citable_without = cur.fetchone()[0]
    finally:
        conn.close()

    print(f"[결과] 값이 바뀐 행 {changed:,}")
    print(f"[검사] 순번 충돌(같은 문서·종류에 같은 번호): {dupes:,}건")
    print(f"[검사] 게이트는 있는데 순번이 없는 행: {citable_without:,}건")
    if dupes or citable_without:
        #: ★조용히 넘어가지 않는다. 둘 다 인용 검증이 근거를 버리는 조건이다.
        print("★남은 문제가 있습니다 — 해당 조항은 인용 근거로 쓰이지 못합니다.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
