"""12 시나리오별 정답 근거 후보 5개씩 출력 + 시나리오 뼈대 JSONL 생성.

실행: python scripts/eval/pick_llm_bench_gold.py
출력: data/eval/llm_bench_scenarios.jsonl (gold 칸은 사람이 채움)
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
CAND = _ROOT / "data" / "eval" / "llm_bench_candidates.csv"
OUT = _ROOT / "data" / "eval" / "llm_bench_scenarios.jsonl"

SCENARIOS = [
    ("S01", "지정", "삼성화재", "4", "G2_공제", "%"),
    ("S02", "지정", "DB손해보험", "5", "G2_공제", "%"),
    ("S03", "지정", "NH농협생명", "3", "G2_공제", "%"),
    ("S04", "지정", "삼성생명", "4", "G2_공제", "원"),
    ("S05", "지정", "KB손해보험", "3", "G2_공제", "원"),
    ("S06", "지정", "현대해상", "4", "G5_통원+입원", "원"),
    ("S07", "지정", "흥국화재", "3", "G5_통원+입원", "원"),
    ("S08", "지정", "롯데손해보험", "5", "G3_통원", "회"),
    ("S09", "미지정", "", "", "G1_자기부담금", "%"),
    ("S10", "미지정", "", "", "G2_공제", "원"),
    ("S11", "미지정", "", "", "G4_입원", "원"),
    ("S12", "기권", "한화손해보험", "", "", ""),
]


import re

GOOD_TITLE = re.compile(r"보상내용|보장종목별|보상하는 사항|공제금액|자기부담")
BAD_TEXT = re.compile(r"세액공제|공제계약|공제조합|공제회|전환|장애인전용")
SUMMARY_SEC = re.compile(r"^(머리말|약관요약서|요약)")


def score(r: dict) -> int:
    s = 0
    blob = r["title"] + " " + r["snippet"]
    if GOOD_TITLE.search(r["title"]):
        s += 4
    if "보통약관" in r["qualified_no"]:
        s += 2
    if SUMMARY_SEC.search(r["qualified_no"]):
        s -= 3
    if "ANNEX" in r["groups"]:
        s -= 1
    if BAD_TEXT.search(blob):
        s -= 6
    if "%" in r["numbers"]:
        s += 1
    return -s  # 정렬용(작을수록 좋음)


def main() -> None:
    rows = list(csv.DictReader(CAND.open(encoding="utf-8-sig")))
    out = []
    for sid, kind, ins, gen, grp, unit in SCENARIOS:
        base = [
            r for r in rows
            if (not ins or r["insurer"] == ins)
            and (not gen or r["generation"] == gen)
            and (not grp or grp in r["groups"].split("|"))
        ]
        pool = [r for r in base if unit and unit in r["numbers"]] or base  # 단위 없으면 완화
        pool.sort(key=score)
        if kind == "미지정":  # 보험사별 최상위 1건 → 섞임 검사용
            seen, top = set(), []
            for r in pool:
                if r["insurer"] not in seen:
                    seen.add(r["insurer"]); top.append(r)
                if len(top) == 5:
                    break
        else:
            top = pool[:5]
        print(f"\n===== {sid} {kind} {ins or '-'} {gen or '-'} {grp or '-'} (후보 {len(pool)})")
        for r in top:
            print(f"  [{r['sha12']}] {r['insurer']} {r['generation']} | {r['qualified_no']} | {r['title'][:24]} | p{r['page_from']} | {r['numbers'][:70]}")
            print(f"      {r['snippet'][:150]}")
        out.append({
            "id": sid, "kind": kind, "insurer": ins or None, "generation": gen or None, "group": grp or None,
            "question": "",
            "gold": {"sha12": "", "qualified_no": "", "numbers": [], "must_not_mix": []},
            "candidates": [{k: r[k] for k in ("insurer", "generation", "sha12", "qualified_no", "title", "page_from", "numbers")} for r in top],
        })
    OUT.write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in out), encoding="utf-8")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()