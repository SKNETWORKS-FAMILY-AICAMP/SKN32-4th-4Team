"""표 수를 **단위를 밝혀서** 센다.

    python -m scripts.eval.table_counts

★왜 이 파일이 따로 있나

    표 개수를 세면서 **부착 횟수를 표 개수로 보고**했다(실측 2026-08-03).
    한 표가 조항과 부록에 둘 다 붙으면 2로 세고 있었다 —
    실제 613개를 **1,313개**로, 레코드 8,849를 **18,127**로 적었다. 2.1배다.

    판정을 직접 틀리게 하지는 않지만 **커버리지를 두 배 좋아 보이게** 만든다.
    "1,313개 실렸다"는 팀에 "충분하다"로 읽힌다.

    코덱스가 정확히 이걸 경고했었다 —
      *"수량 단위를 분리해야 합니다: unique_page_tables / attached_table_occurrences /
        withheld_unique_tables"*
    읽고 `table_id` 는 넣었는데 **세는 쪽을 안 고쳤다.** 지적을 절반만 반영한 것이다.

★그래서 이 스크립트는 **이름으로 단위를 못박는다.** 어느 쪽인지 모르는 숫자를 내지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def count(tag: str) -> dict:
    struct = _ROOT / "data" / "structured"
    uniq_tables: set[tuple[str, str]] = set()
    uniq_records = attached_tables = attached_records = withheld = 0
    by_method: Counter = Counter()
    by_insurer_unique: Counter = Counter()
    docs = 0

    for p in sorted(struct.glob(f"*/{tag}/*.clauses.json")):
        j = json.loads(p.read_text(encoding="utf-8"))
        docs += 1
        sha = p.name.split(".")[0]
        withheld += (j.get("stats") or {}).get("tables_withheld_unverified", 0)
        seen: set[tuple[str, str]] = set()
        for k in ("clauses", "annexes"):
            for x in j.get(k) or []:
                for t in x.get("tables") or []:
                    n_rec = len(t.get("records") or [])
                    attached_tables += 1
                    attached_records += n_rec
                    key = (sha, str(t.get("table_id")))
                    if key in seen:
                        continue
                    seen.add(key)
                    uniq_tables.add(key)
                    uniq_records += n_rec
                    by_method[t.get("method")] += 1
                    by_insurer_unique[p.parent.parent.name] += 1

    return {
        "clause_tag": tag,
        "documents": docs,
        #: ★이름이 단위를 말한다. `tables` 같은 모호한 이름을 쓰지 않는다.
        "unique_tables": len(uniq_tables),
        "unique_records": uniq_records,
        "attached_occurrences": attached_tables,
        "attached_records": attached_records,
        "withheld_unverified": withheld,
        "unique_by_method": dict(by_method),
        "unique_by_insurer": dict(by_insurer_unique.most_common()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clause-tag", default="")
    a = ap.parse_args()
    tag = a.clause_tag
    if not tag:
        from app.core import release

        tag = release.load().clause_tag

    r = count(tag)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    print()
    print(f"★표 {r['unique_tables']:,}개(**고유**) · 부착 {r['attached_occurrences']:,}회 — "
          f"{r['attached_occurrences'] / max(r['unique_tables'], 1):.2f}배")
    print("  숫자를 인용할 때 **어느 단위인지 함께** 적는다.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    raise SystemExit(main())
