# -*- coding: utf-8 -*-
"""이관·정리가 남긴 **백업 테이블**을 세고, 언제 지워도 되는지 말한다.

    python -m scripts.db.list_backup_tables            # 조회만(기본)
    python -m scripts.db.list_backup_tables --drop <이름>   # 하나 지운다

★★왜 (2026-08-26)

    2026-08-25~26 정리·이관에서 백업 테이블 3개가 생겼다(171,909행 · 48 MB).
    「나중에 지운다」로 두면 **아무도 안 지운다.** 그리고 어느 것이 무엇의 백업인지,
    되돌릴 일이 아직 남았는지를 다음 사람이 알 수 없다.

★**보존 조건을 코드가 판정한다.** 「기간이 지났으니 지워도 된다」가 아니라
  **「그 백업이 없어도 되는 상태인가」**를 본다 — 아래 `_verdict()` 참조.
  기간만 보면, 되돌릴 일이 남았는데 지우는 일이 생긴다.

★기본은 조회만. 지우려면 이름을 **명시**해야 한다. 한 번에 하나씩.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 어느 DB 를 볼지. 이관 때문에 둘 다 봐야 한다.
TARGETS = (
    ("insurance_real", "host=127.0.0.1 port=5433 user=postgres dbname=insurance_real"),
    ("mall_vec", "host=127.0.0.1 port=5433 user=postgres dbname=mall_vec"),
)

#: 무엇의 백업인가 · 언제 지워도 되나. **손으로 적는다** — 자동 판정할 수 있는 것이 아니다.
LEDGER = {
    "pco_removed_20260826_s6": (
        "산출물에 없는 낡은 발생행 정리(reconcile_occurrences)",
        "이관이 확정되고 `mall_vec` 을 폐기할 때 함께 사라진다. 그전에는 둔다.",
    ),
    "pco_removed_20260826_s5mixed": (
        "s5-mixed 세대 은퇴 — 발생행만 제거",
        "★재생성 불가(158,186행 중 294행이 s5 산출물에 없다). "
        "`mall_vec` 폐기 전까지 **반드시 둔다.**",
    ),
    "vec_pruned_alias_20260826": (
        "별칭(중복본) 문서의 발생행 정리 — DB손해보험 2660c88b05cc",
        "산출물에서 다시 만들 수 있다(별칭 원장이 그 문서를 건너뛰게 한다). "
        "고아 0 이 한 달 유지되면 지워도 된다.",
    ),
    #: ── 2026-08-27 s6 재색인 ─────────────────────────────────────────────
    "pco_removed_20260827_regen": (
        "s6 전량 재생성(23:18판) 후 낡은 발생행 정리 — 산출물에 없는 내용 64,171행",
        "산출물에서 **다시 만들 수 있다**(현행 s6 로 재적재하면 같은 상태가 된다). "
        "신선도 0 과 시험 통과가 한 달 유지되면 지워도 된다.",
    ),
    "pco_missing_artifact_20260827": (
        "산출물이 아예 없는 문서 11건의 발생행 — 비의료실손 격리 5 + 판매개시일 미상 6",
        "★**되살림용이다. 지우지 않는다.** 판매개시일 미상 6건은 날짜를 알아내면 "
        "복구 대상이다(먼저 s6 산출물부터 만들어야 한다). 그 6건이 처리되기 전에는 둔다.",
    ),
    "pco_stale_slot_20260827": (
        "내용은 살아 있는데 **자리가 사라진** 발생행 361행 — reconcile 이 못 잡던 무리",
        "산출물에서 다시 만들 수 있다(현행 `_collect` 이 만들지 않는 자리임을 확인했다). "
        "`source_ordinal` 이 전량 채워진 상태가 한 달 유지되면 지워도 된다.",
    ),
    #: ── 전체 스냅샷(`CREATE TABLE ... AS TABLE`) ────────────────────────
    #: ★★이것들은 **테이블 통째 복사**라 크다(합계 약 3GB). 위 `pco_*` 는 「지운 행만」이다.
    #:   둘을 같은 눈으로 보면 안 된다 — 스냅샷은 **더 빨리 버려야** 한다.
    "policy_clause_occurrence_bak_20260826_2350": (
        "s6 재색인 착수 직전 전체 스냅샷(발생 197,010행)",
        "그 뒤 `_0130` · `_0330` 스냅샷이 더 있고 재색인이 검증됐다(신선도 0 · 시험 통과). "
        "**가장 오래된 것부터 버린다.**",
    ),
    "policy_clause_chunk_bak_20260826_2350": (
        "s6 재색인 착수 직전 전체 스냅샷(조각 164,977행)",
        "조각은 **산출물+GPU 임베딩으로 다시 만들 수 있다**(65.8% 재사용). "
        "더 최신 스냅샷이 있으므로 버린다.",
    ),
    "policy_clause_content_bak_20260826_2350": (
        "s6 재색인 착수 직전 전체 스냅샷(내용 67,633행)",
        "산출물에서 다시 만들 수 있다. 더 최신 스냅샷이 있으므로 버린다.",
    ),
    "policy_clause_occurrence_bak_20260827_0130": (
        "낡은 발생 64,171행 삭제 직전 전체 스냅샷(251,403행)",
        "삭제분은 `pco_removed_20260827_regen` 에 따로 있고, `_0330` 스냅샷이 더 최신이다.",
    ),
    "policy_clause_chunk_bak_20260827_0130": (
        "낡은 발생 삭제 직전 전체 스냅샷(조각 223,372행)",
        "조각은 이 작업에서 **바뀌지 않았다**(발생만 지웠다). 현행과 같은 내용이라 버려도 된다.",
    ),
    "policy_clause_content_bak_20260827_0130": (
        "낡은 발생 삭제 직전 전체 스냅샷(내용 88,530행)",
        "내용도 이 작업에서 바뀌지 않았다. 현행과 같은 내용이라 버려도 된다.",
    ),
    "policy_clause_occurrence_bak_20260827_0330": (
        "산출물 없는 11문서 671행 삭제 직전 전체 스냅샷(187,232행)",
        "★**가장 최신 스냅샷이다.** 그 뒤 `occurrence_id` v2 작업까지 얹혔으므로, "
        "되돌릴 곳으로 **당분간 남긴다.** 다음 스냅샷이 생기면 그때 버린다.",
    ),
}


def _verdict(name: str, rows: int) -> str:
    """지금 지워도 되나 — **기간이 아니라 상태로** 판정한다."""
    if name not in LEDGER:
        return "★원장에 없는 백업이다. 무엇의 백업인지 적기 전에는 지우지 않는다."
    #: ★★`mall_vec` 것만 보류다. `insurance_real` 로 옮긴 뒤 만든 것은
    #:   되돌아갈 곳이 이미 옮겨졌으므로 같은 규칙을 쓰면 안 된다(2026-08-27).
    if name.startswith("pco_removed_20260826_"):
        return "보류 — `mall_vec` 폐기와 함께 처리한다(되돌아갈 곳이 아직 살아 있다)."
    if name == "pco_missing_artifact_20260827":
        #: ★되살림 대상이 남아 있다. 상태로 판정한다 — 기간이 아니다.
        return "★두다 — 판매개시일 미상 6건의 복구 재료다. 그 6건이 처리되기 전에는 못 지운다."
    if name.endswith("_bak_20260827_0330"):
        return "★두다 — 가장 최신 전체 스냅샷이다. 다음 스냅샷이 생기면 그때 버린다."
    if "_bak_" in name:
        #: ★전체 스냅샷은 크다(합계 약 3GB). 더 최신 스냅샷이 있으면 **버리는 쪽**이 기본이다.
        return "지워도 된다 — 더 최신 전체 스냅샷이 있고 재색인이 검증됐다(신선도 0 · 시험 통과)."
    return "조건부 — 위 「언제」를 확인하고 지운다."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", default=None, metavar="TABLE",
                    help="이 백업 테이블을 지운다(이름을 정확히 준다)")
    a = ap.parse_args()

    import psycopg

    found = []
    for db, dsn in TARGETS:
        try:
            conn = psycopg.connect(dsn, connect_timeout=20)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{db}] ★붙지 못했다: {str(exc)[:70]}")
            continue
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT table_schema, table_name FROM information_schema.tables"
                " WHERE table_type='BASE TABLE'"
                #: ★`\_` 는 SQL 에서 「밑줄 그 자체」다. 파이썬 문자열에서도
                #:   그대로 남기려면 raw 로 쓴다 — 안 그러면 SyntaxWarning 이 난다.
                #: ★★`%_bak_%` 를 **빠뜨리고 있었다** (2026-08-27).
                #:   `CREATE TABLE ... AS TABLE` 로 뜬 **전체 스냅샷**이 그 이름을 쓰는데
                #:   여기서 안 봐서 **3GB 가 아무한테도 안 보였다.**
                #:   백업을 세는 도구가 백업을 못 보면, 그건 없는 것과 같다.
                r"   AND (table_name LIKE 'pco\_%' OR table_name LIKE 'vec\_pruned\_%'"
                r"        OR table_name LIKE '%\_bak\_%')"
                " ORDER BY 1, 2"
            )
            for schema, table in cur.fetchall():
                cur.execute(f'SELECT count(*) FROM "{schema}"."{table}"')
                rows = cur.fetchone()[0]
                cur.execute(
                    "SELECT pg_size_pretty(pg_total_relation_size(%s))",
                    (f'"{schema}"."{table}"',),
                )
                size = cur.fetchone()[0]
                found.append((db, schema, table, rows, size))

    if not found:
        print("  백업 테이블이 없다.")
        return 0

    total = sum(r for *_, r, _ in found)
    print(f"  백업 테이블 {len(found)}개 · 합계 {total:,}행")
    for db, schema, table, rows, size in found:
        what, when = LEDGER.get(table, ("★원장에 없음", "★적기 전에는 지우지 않는다"))
        print(f"\n  {db}.{schema}.{table}")
        print(f"    {rows:,}행 · {size}")
        print(f"    무엇  {what}")
        print(f"    언제  {when}")
        print(f"    판정  {_verdict(table, rows)}")

    if not a.drop:
        print("\n  (조회만 했다. 지우려면 --drop <이름>)")
        return 0

    target = [x for x in found if x[2] == a.drop]
    if not target:
        print(f"\n★그런 백업 테이블이 없다: {a.drop}")
        return 1
    db, schema, table, rows, _ = target[0]
    if table.startswith("pco_removed_"):
        #: ★★되돌아갈 곳이 살아 있는 동안은 안 지운다. 이름만 맞으면 지우게 두지 않는다.
        print(f"\n★{table} 은 `mall_vec` 폐기와 함께 처리한다. 지금은 안 지운다.")
        print("  이유: 이관을 되돌릴 때 이 행들이 필요하다(s5-mixed 는 재생성 불가).")
        return 1
    dsn = dict(TARGETS)[db]
    with psycopg.connect(dsn, connect_timeout=20) as conn, conn.cursor() as cur:
        cur.execute(f'DROP TABLE "{schema}"."{table}"')
    print(f"\n  지웠다: {db}.{schema}.{table} ({rows:,}행)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
