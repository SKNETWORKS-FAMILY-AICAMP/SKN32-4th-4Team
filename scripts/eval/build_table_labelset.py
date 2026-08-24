"""표 정답셋 **후보를 층화해서 뽑는다.** 사람이 찍기만 하면 되도록.

    python -m scripts.eval.build_table_labelset --per-stratum 3

★왜 이게 먼저인가

    지금 판별 게이트는 참 4 · 거짓 3 으로 맞춘 임계값 **하나**(T1 통로)에 걸려 있다.
    표본이 이 정도면 "명백한 오탐을 거른다"까지가 정직한 주장이고,
    품질을 말하려면 층화 표본이 있어야 한다(계획서 L1).

★라벨링 비용을 줄이는 방법

    사람이 **백지에서 셀을 타이핑하지 않는다.** 추출 결과를 보여 주고
    `label` 만 찍게 한다 — `table`(진짜 표) · `prose`(본문 오인) · `unsure`.
    셀 값이 틀린 경우에만 `fix` 에 고친다.

★층화 기준

    (보험사 12) × (복원 방식 2: 선 · 2열짝짓기)

    조판 세대까지 나누면 층이 너무 잘게 쪼개져 층당 표본이 1개도 안 남는다.
    ★그래서 **지금은 안 나눈다.** 나누지 않았다는 사실을 여기 적어 둔다 —
      "층화했다"고만 적으면 다음 사람이 조판까지 덮은 줄 안다.

★뽑기는 **결정적**이어야 한다

    무작위 시드를 고정한다. 재실행할 때마다 다른 표가 나오면
    라벨링한 것이 무효가 된다.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_OUT = _ROOT / "data" / "eval" / "table_labelset_candidates.jsonl"
SEED = 20260803


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-stratum", type=int, default=3)
    ap.add_argument("--out", default=str(_OUT))
    a = ap.parse_args()

    #: 층 → 후보 목록
    strata: dict[tuple[str, str], list[dict]] = defaultdict(list)
    n_docs = 0
    for p in sorted((_ROOT / "data" / "extracted").glob("*/s5_pymupdf-1.28.0/*.json")):
        insurer = p.parent.parent.name
        doc = json.loads(p.read_text(encoding="utf-8"))
        n_docs += 1
        for pg in doc.get("pages") or []:
            for t in pg.get("tables_coords") or []:
                if not t.get("records"):
                    continue
                strata[(insurer, t.get("method") or "?")].append({
                    "sha12": p.stem, "insurer": insurer, "page": pg["page"],
                    "table_id": t.get("table_id"), "method": t.get("method"),
                    "cols": t.get("cols"), "rows": t.get("rows"),
                    "is_table_auto": t.get("is_table"),
                    "signals": t.get("signals"),
                    #: 사람이 볼 미리보기. 앞 4행이면 표인지 본문인지 대개 보인다.
                    "preview": [
                        {k: (v or "")[:60] for k, v in (r.get("cols") or {}).items()}
                        for r in (t["records"] or [])[:4]
                    ],
                    #: ★사람이 채울 칸. 비워 둔다 — 자동값을 미리 넣으면 그걸 따라 찍는다.
                    "label": "", "fix": "", "note": "",
                })

    rnd = random.Random(SEED)
    picked = []
    for key in sorted(strata):
        pool = strata[key]
        #: ★같은 문서에서 몰아 뽑지 않는다. 문서마다 하나씩 돌려 가며 뽑는다.
        by_doc: dict[str, list] = defaultdict(list)
        for c in pool:
            by_doc[c["sha12"]].append(c)
        docs = sorted(by_doc)
        rnd.shuffle(docs)
        take = []
        i = 0
        while len(take) < a.per_stratum and docs:
            d = docs[i % len(docs)]
            if by_doc[d]:
                take.append(rnd.choice(by_doc[d]))
                by_doc[d] = []
            else:
                docs.remove(d)
                if not docs:
                    break
                continue
            i += 1
        picked.extend(take)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        for c in picked:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"문서 {n_docs:,} · 층 {len(strata)}개 · 후보 {sum(len(v) for v in strata.values()):,}")
    print(f"층당 {a.per_stratum}개씩 뽑아 **{len(picked)}개** → {out.relative_to(_ROOT)}")
    print("\n층별 후보 수(뽑은 수):")
    for key in sorted(strata):
        got = sum(1 for c in picked if (c["insurer"], c["method"]) == key)
        print(f"  {key[0]:14s} {key[1]:10s} {len(strata[key]):7,}  → {got}")
    print("\n★다음: `label` 칸을 table / prose / unsure 로 채운다.")
    print("  채운 뒤 `python -m scripts.eval.table_signal_fit --labelset` 로 임계값을 다시 맞춘다.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    raise SystemExit(main())
