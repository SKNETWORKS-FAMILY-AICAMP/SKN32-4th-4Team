# -*- coding: utf-8 -*-
"""조항 본문의 **KCD 코드 범위**를 `core.clause_code_rule` 에 넣는다.

★무엇을 넣나

    `app/core/domain/kcd_ranges.scan_clause()` 가 조항 본문에서 코드 범위를 뽑고
    그 성격을 표시한다 —

        exclude    면책 ("보상하지 않습니다")
        exception  면책의 예외 ("다만 … 보상합니다")
        mention    ★**성격 불명**

    셋 다 넣는다. **`mention` 을 빼지 않는다** — 뺐다는 사실이 어디에도 안 남으면
    다음 사람은 "이 조항에는 코드가 없다"고 읽는다. 넣되 `kind` 로 구분해 두고,
    판정하는 쪽이 `mention` 을 쓰지 않으면 된다(§0: 모르면 모른다고 한다).

★무엇을 **안** 넣나

    `core.kcd_code`(코드 → 질병명)는 **채우지 않는다.**
    `data/exports/kcd_catalog.json` 이 스스로 적어 뒀다 —
    「이건 KCD 사전이 아니다 — 코드→질병명 표를 우리는 갖고 있지 않다(약 2만 항목)」.
    이름을 모르는 채 코드만 넣고 `name_ko` 를 비우거나 지어내면,
    그 표를 읽는 사람이 **사전이 있다고 믿는다.** 없는 것은 없다고 둔다.

★`content_hash` 는 `core.clause_content` 를 가리킨다(FK). 그래서 **core 적재가 먼저**다.

사용:

    python -m scripts.db.load_clause_code_rules              # 조회만
    python -m scripts.db.load_clause_code_rules --apply
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

#: ★추출기 판을 박아 둔다. 규칙이 바뀌면 이 값이 바뀌고, UNIQUE 에 들어 있어
#:   **옛 판과 새 판이 공존**한다(덮어쓰지 않는다 — CLAUDE.md §1 「버전을 박는다」).
EXTRACTOR_VERSION = "kcd_ranges@2026-08-27"


def _rows(limit: int = 0):
    from app.core.domain import kcd_ranges as K
    from db.postgres.pgvector_index import get_conn

    out = []
    seen = set()
    stats = collections.Counter()
    with get_conn() as conn, conn.cursor() as cur:
        #: ★`core.clause_content` 에 있는 것만 본다 — FK 대상이 없으면 못 넣는다.
        cur.execute("SELECT content_hash, body FROM core.clause_content"
                    + (f" LIMIT {int(limit)}" if limit else ""))
        for content_hash, body in cur.fetchall():
            stats["clauses"] += 1
            for m in K.scan_clause(body or ""):
                lo, hi = m.range.lo, m.range.hi
                key = (content_hash, lo.letter, lo.number, lo.sub, hi.number, hi.sub, m.kind)
                #: ★UNIQUE 와 같은 키로 **미리** 접는다. 안 접으면 같은 배치 안에서
                #:   `ON CONFLICT` 가 한 문장에 두 번 걸려 죽는다.
                if key in seen:
                    stats["중복접음"] += 1
                    continue
                seen.add(key)
                stats[m.kind] += 1
                out.append((
                    content_hash, lo.letter, lo.number, lo.sub, hi.number, hi.sub,
                    m.kind, m.context[:2000],
                    json.dumps({"range": str(m.range)}, ensure_ascii=False),
                ))
    return out, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="조항의 KCD 코드 범위를 core 에 적재")
    ap.add_argument("--apply", action="store_true", help="실제로 넣는다(기본은 조회만)")
    ap.add_argument("--limit", type=int, default=0, help="조항 수 제한(맛보기)")
    a = ap.parse_args(argv)

    rows, stats = _rows(a.limit)
    print(f"[스캔] 조항 본문 {stats['clauses']:,}건 → 규칙 {len(rows):,}개")
    #: ★성격별로 **나눠 찍는다.** `mention` 이 얼마나 되는지가 이 자료의 한계다.
    for kind in ("exclude", "exception", "mention"):
        print(f"       {kind:<10} {stats[kind]:>7,}"
              + ("   ★성격 불명 — 판정에 쓰면 안 된다" if kind == "mention" else ""))
    if stats["중복접음"]:
        print(f"       같은 조항에 같은 범위가 거듭 나와 접은 것 {stats['중복접음']:,}")

    if not a.apply:
        print("       (조회만 했다. 넣으려면 --apply)")
        return 0
    if not rows:
        print("★넣을 것이 없다. `core.clause_content` 가 비어 있는지 확인하라.")
        return 1

    from db.postgres.pgvector_index import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout='60min'")
            cur.executemany(
                "INSERT INTO core.clause_code_rule("
                "content_hash,code_letter,code_lo,code_lo_sub,code_hi,code_hi_sub,"
                "kind,quote,source_span,extractor_version) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) "
                #: ★같은 추출기 판으로 다시 돌리면 **아무것도 안 바뀐다**(멱등).
                #:   판이 다르면 UNIQUE 가 달라 **따로 쌓인다** — 덮지 않는다.
                "ON CONFLICT DO NOTHING",
                [(*r, EXTRACTOR_VERSION) for r in rows],
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT kind, count(*) FROM core.clause_code_rule "
                        "WHERE extractor_version=%s GROUP BY 1 ORDER BY 1",
                        (EXTRACTOR_VERSION,))
            print(f"[적재] {EXTRACTOR_VERSION}: {dict(cur.fetchall())}")
            cur.execute("SELECT count(*) FROM core.clause_code_rule")
            print(f"       core.clause_code_rule 전체 {cur.fetchone()[0]:,}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
