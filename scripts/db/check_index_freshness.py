# -*- coding: utf-8 -*-
"""색인이 **현행 산출물과 같은 판인가**를 잰다.

    python -m scripts.db.check_index_freshness
    python -m scripts.db.check_index_freshness --sample 50   # 표본만(빠르게)

★★왜 (2026-08-26 실측으로 잡힘)

    다른 세션이 `s6` 산출물을 **전량 재생성**했다(17:06~17:07). 그런데 색인은
    그 전 판이었다. **아무도 몰랐다** — 검색도 판정도 정상으로 보였고,
    다만 **낡은 조항을 근거로 냈다.**

    실측: DB 문서 1,313 중 **1,172개(89.3%)** 가 산출물에 없는 해시를 들고 있었다.
    한 문서에서 파일 저장소는 인용 3건, PG 는 2건 — 순번(13·67·76 vs 28·83)도 갈렸다.

    ★기존 `ensure_index_matches_release()` 는 이걸 **못 잡는다.** 그건
      「세대·모델 필터가 맞는가」를 보지 「내용이 같은 판인가」를 안 본다.
      필터는 맞는데 내용이 낡은 상태가 정확히 이번 경우다.

★**판정을 막지 않는다.** 이 도구는 세고 말할 뿐이다 —
  낡았다고 판정을 멈추면 그 순간 서비스가 죽는다. 무엇이 낡았는지 알리고,
  다시 적재할지는 사람이 정한다.

★`--sample` 은 **표본이라고 말한다.** 비율을 전체로 말하지 않는다(CLAUDE.md §4).
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _artifact_hashes(generation: str) -> tuple[dict[str, set[str]], int]:
    """`{sha12: {content_hash…}}` 와 못 읽은 파일 수."""
    out: dict[str, set[str]] = {}
    broken = 0
    pattern = str(ROOT / "data" / "structured" / "*" / f"{generation}_*" / "*.clauses.json")
    for f in glob.glob(pattern):
        stem = pathlib.Path(f).name.split(".")[0]
        try:
            d = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            broken += 1
            continue
        hs = {x.get("content_hash") for key in ("clauses", "annexes")
              for x in (d.get(key) or []) if x.get("content_hash")}
        if hs:
            out[stem] = hs
    return out, broken


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generation", default=None, help="기본: 승인 릴리스의 세대")
    ap.add_argument("--sample", type=int, default=0,
                    help="문서 표본 수(0=전수). ★표본이면 그렇다고 찍는다")
    #: ★★**산출물이 낳지 않은 행은 심판하지 않는다** (2026-08-27, 실측하고 고쳤다).
    #:
    #:   처음 판은 `source_kind` 를 안 봤다. 그래서 S7.1 승인 OCR fact
    #:   (`approved_ocr_table_fact`, 850행·179문서)를 「산출물에 없으니 낡았다」고
    #:   세고 있었다 — 그건 `load_s7_1_approved_facts.py` 가 **다른 출처에서** 넣은 것이라
    #:   구조화 산출물에 없는 게 당연하다.
    #:   실측: 낡은 행 정리를 끝낸 뒤에도 179문서가 남았는데, 그 179가
    #:   OCR fact 문서 179와 **정확히 같은 집합**이었고 잡힌 850행이 전부 그 출처였다.
    #:
    #:   ★`reconcile_occurrences` 는 이 교훈을 2026-08-25 에 이미 배웠다(그 docstring 참조).
    #:     이 도구가 물려받지 못해 같은 실수를 되풀이했다 — 새 도구를 만들 때
    #:     **같은 대상을 심판하는 기존 도구의 제외 규칙을 먼저 본다.**
    ap.add_argument("--source-kinds", default="clause,annex",
                    help="대조할 출처(쉼표). 그 밖은 심판하지 않는다. "
                         "빈 값이면 전부 대조한다(권장하지 않음)")
    a = ap.parse_args()

    from db.postgres import pgvector_clause_index as ix
    from db.postgres.pgvector_index import get_conn

    gen = a.generation or ix.current_generation()
    art, broken = _artifact_hashes(gen)
    print(f"[산출물] 세대 {gen} · 문서 {len(art):,}개"
          + (f" · ★못 읽음 {broken}" if broken else ""))
    if not art:
        print("★산출물을 하나도 못 읽었다. 판정할 수 없다.")
        return 1

    kinds = [k.strip() for k in a.source_kinds.split(",") if k.strip()]
    with get_conn() as conn, conn.cursor() as cur:
        if kinds:
            cur.execute("SELECT sha256, array_agg(DISTINCT content_hash) "
                        "FROM policy_clause_occurrence "
                        "WHERE index_generation = %s AND source_kind = ANY(%s) GROUP BY 1",
                        (gen, kinds))
        else:
            cur.execute("SELECT sha256, array_agg(DISTINCT content_hash) "
                        "FROM policy_clause_occurrence WHERE index_generation = %s GROUP BY 1",
                        (gen,))
        rows = cur.fetchall()
        #: ★뺀 것을 **세어서 찍는다.** 조용히 빼면 다음 사람이 분모를 모른다(CLAUDE.md §3).
        excluded = []
        if kinds:
            cur.execute("SELECT source_kind, count(*) FROM policy_clause_occurrence "
                        "WHERE index_generation = %s AND NOT (source_kind = ANY(%s)) "
                        "GROUP BY 1 ORDER BY 1", (gen, kinds))
            excluded = cur.fetchall()
    print(f"[대조 대상] 출처 {', '.join(kinds) if kinds else '전부'}")
    for k, n in excluded:
        print(f"    심판하지 않음: {k} {n:,}행 — 구조화 산출물이 낳은 행이 아니다")

    if a.sample and a.sample < len(rows):
        #: ★결정적으로 뽑는다 — 다시 돌려도 같은 표본이라야 비교가 된다.
        random.seed(0)
        rows = random.sample(rows, a.sample)
        scope = f"표본 {len(rows):,}문서"
    else:
        scope = f"전수 {len(rows):,}문서"

    stale, no_artifact, fresh = [], 0, 0
    for sha, hashes in rows:
        keep = art.get(sha[:12])
        if keep is None:
            #: ★산출물이 없으면 **판정하지 않는다.** 「없다」와 「낡았다」는 다른 말이다.
            no_artifact += 1
            continue
        extra = set(hashes) - keep
        if extra:
            stale.append((sha[:12], len(extra), len(hashes)))
        else:
            fresh += 1

    checked = fresh + len(stale)
    print(f"[대조] {scope}")
    print(f"    산출물 없어 판단 보류 {no_artifact:,}")
    print(f"    현행과 같음          {fresh:,}")
    print(f"    ★산출물에 없는 해시를 든 문서 {len(stale):,}"
          + (f" ({len(stale)/checked*100:.1f}% / 대조한 {checked:,}문서)" if checked else ""))
    for s, n, tot in sorted(stale, key=lambda x: -x[1])[:5]:
        print(f"      {s} 낡은 해시 {n:,} / 이 문서 {tot:,}")

    if a.sample:
        print("    ★표본이다. 이 비율을 전체로 말하지 않는다 — 전수는 --sample 없이.")

    if stale:
        print("\n★★색인이 현행 산출물과 다른 판이다. **판정이 낡은 조항을 근거로 낸다.**")
        print("   고치려면 그 세대를 다시 적재한다:")
        print("     python -m scripts.index.build_clause_index")
        print("   ★이 도구는 판정을 막지 않는다 — 막으면 그 순간 서비스가 죽는다.")
        return 2
    print("\n  색인이 현행 산출물과 같은 판이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
