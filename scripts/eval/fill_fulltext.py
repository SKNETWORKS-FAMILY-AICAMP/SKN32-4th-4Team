"""리랭커 평가 후보셋에 **조 전체 본문**을 채워 넣는다.

    python -m scripts.eval.fill_fulltext \
        --in  data/eval/s7_1_arctic_ko_top20_rerank.json \
        --out data/eval/s7_1_arctic_ko_top20_rerank_fulltext.json

★왜 필요한가
  후보셋(`*_top20_rerank.json`)에는 **조각(`text`)만** 들어 있다(8,285쌍 전부).
  그런데 서비스는 조 전체(`citable_text`)로도 채점할 수 있어야 하고,
  둘을 **같은 후보셋에서** 비교해야 「채점 본문이 성능을 가르는가」를 잴 수 있다.
  실제로 갈랐다 — 조각이 hit@1 을 5.04%p 더 낸다
  (`docs/reports/2026-08-05_0100_리랭커_붙는자리_실측.md`).

★★**산출물은 커밋하지 않는다.** 조 전체 본문은 약관 원문이고 저작물이다
  (CLAUDE.md §2). `.gitignore` 가 `data/eval/*fulltext*.json` 을 막는다.
  2026-08-05 에 38.8MB 짜리가 실제로 커밋됐다가 origin 직전에 잡혔다.
  **필요할 때 이 스크립트로 다시 만든다** — 저장소에 두지 않는다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", type=pathlib.Path,
                    default=ROOT / "data/eval/s7_1_arctic_ko_top20_rerank.json")
    ap.add_argument("--out", dest="dst", type=pathlib.Path,
                    default=ROOT / "data/eval/s7_1_arctic_ko_top20_rerank_fulltext.json")
    a = ap.parse_args()

    import psycopg

    from app.core.config import get_settings

    data = json.loads(a.src.read_text(encoding="utf-8"))
    hashes = {c["content_hash"] for r in data["records"] for c in r["candidates"]}
    print(f"  고유 content_hash {len(hashes):,}")

    with psycopg.connect(get_settings().PGVECTOR_DSN, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content_hash, text FROM policy_clause_content WHERE content_hash = ANY(%s)",
                (list(hashes),),
            )
            body = dict(cur.fetchall())

    missing = len(hashes) - len(body)
    print(f"  DB 에서 찾은 본문 {len(body):,} · 못 찾은 것 {missing:,}")
    if missing:
        #: ★못 찾은 것을 조용히 조각으로 때우지 않는다. 그러면 어떤 후보는 조 전체,
        #:   어떤 후보는 조각으로 채점돼 **비교 자체가 무의미해진다.**
        print("  ★본문이 없는 해시가 있다. 적재가 반쪽이라는 뜻이므로 중단한다.")
        return 1

    filled = 0
    for rec in data["records"]:
        for cand in rec["candidates"]:
            cand["full_text"] = body[cand["content_hash"]]
            filled += 1

    lens = [len(c["full_text"]) for r in data["records"] for c in r["candidates"]]
    chunks = [len(c.get("text") or "") for r in data["records"] for c in r["candidates"]]
    print(f"  채움 {filled:,} · full_text 중앙 {statistics.median(lens):.0f}자 "
          f"(조각 중앙 {statistics.median(chunks):.0f}자)")

    data["_full_text_출처"] = (
        "policy_clause_content 직접 조회. 서비스가 채점하는 citable_text 와 같은 본문. "
        "★약관 원문이므로 커밋하지 않는다(CLAUDE.md §2)."
    )
    a.dst.parent.mkdir(parents=True, exist_ok=True)
    a.dst.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"  기록 → {a.dst.relative_to(ROOT)}  ({a.dst.stat().st_size / 1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
