"""s6 **부록 본문 텍스트**에서 질병명↔KCD 코드 짝이 살아 있는지 채점한다.

★`table_gold_check.py` 와 **묻는 것이 다르다.**

    `table_gold_check`  — `table_coords.py` 의 **좌표 복원**이 맞나 (F1 1.000 / 66레코드)
    이 파일             — 지금 **실제로 색인되는 텍스트**에서 짝이 맞나

  좌표 복원은 아직 파이프라인에 **연결되어 있지 않다**(`table_coords` 를 부르는 곳은
  `table_gold_check.py` 뿐이다). 그래서 판정이 실제로 읽는 것은 PyMuPDF 가 뱉은
  **읽기 순서 텍스트**다. 그게 맞는지는 **한 번도 재지 않았다.**

  "표 작업을 했다"와 "표가 판정에 도달한다"는 다른 말이다. 이 파일이 그 간극을 잰다.

판정 기준: 질병명 바로 뒤 `WINDOW` 자 안에 **그 질병의 코드가 전부** 있고
           **다른 질병의 코드는 없어야** 한다. 하나라도 섞이면 오짝이다.

실행:
    python -m scripts.eval.annex_table_text_check
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CODE = re.compile(r"[A-Z]\d{2}(?:\.\d)?")
_WS = re.compile(r"\s+")

#: 질병명 뒤 몇 자까지를 "그 질병의 코드 자리"로 볼 것인가.
#: ★표 한 행의 코드 칸 길이다. 넉넉히 잡으면 다음 행 코드까지 들어와
#:   **오짝을 못 잡는다.** 실측 최장 행(척추질환)이 40자라 그 1.5배로 둔다.
WINDOW = 60


def _norm(s: str) -> str:
    return _WS.sub("", s or "")


def main() -> int:
    gold = json.loads((_ROOT / "data" / "eval" / "table_gold.json").read_text(encoding="utf-8"))
    by_id = {t["id"]: t for t in gold["tables"]}

    tot = hit = miss_name = mispair = 0
    for t in gold["tables"]:
        ref = by_id[t["same_as"]] if t.get("same_as") else t
        recs = ref["records"]
        #: 다른 질병의 코드 — 섞여 들어왔는지 보려면 필요하다.
        all_codes = {r["no"]: set(_CODE.findall(r["codes"])) for r in recs}

        paths = list((_ROOT / "data" / "structured").glob(
            f"*/s6_pymupdf-1.28.0/{t['sha12']}.clauses.json"))
        if not paths:
            print(f"[건너뜀] s6 없음 {t['id']}")
            continue
        doc = json.loads(paths[0].read_text(encoding="utf-8"))
        #: ★조항·부록을 **둘 다** 본다. 표가 어디에 실렸는지 가정하지 않는다.
        blob = "\n".join(
            (x.get("text") or "")
            for key in ("clauses", "annexes")
            for x in (doc.get(key) or [])
        )
        flat = _norm(blob)

        #: ★행의 끝은 **다음 질병명**이다. 고정 창을 쓰면 다음 행 코드가 들어와
        #:   맞는 데이터도 오짝으로 찍힌다(처음에 그렇게 재서 0/66 이 나왔다).
        #:   평평해진 텍스트에서 LLM 이 실제로 쓸 수 있는 단서가 이 경계다.
        pos = {}
        for r in recs:
            nm = _norm(r["name"]).replace(",", "")
            pos[r["no"]] = (flat.find(nm), nm)

        n_ok = n_name = n_pair = 0
        for r in recs:
            tot += 1
            i, name = pos[r["no"]]
            if i < 0:
                n_name += 1
                continue
            start = i + len(name)
            #: 이 이름 뒤에 오는 **가장 가까운 다른 이름**까지가 이 행의 코드 자리다.
            nxt = min((p for p, _ in pos.values() if p > i), default=len(flat))
            win = flat[start: min(nxt, start + WINDOW)]
            got = set(_CODE.findall(win))
            want = all_codes[r["no"]]
            others = set().union(*(v for k, v in all_codes.items() if k != r["no"]))
            if want <= got and not (got & (others - want)):
                n_ok += 1
            else:
                n_pair += 1
        hit += n_ok
        miss_name += n_name
        mispair += n_pair
        print(f"{t['id']}\n    {len(recs)}레코드 중 짝 맞음 {n_ok} · 이름 못 찾음 {n_name} · 오짝 {n_pair}")

    print(f"\n텍스트 짝 정확도 {hit}/{tot} = {hit / tot:.3f}" if tot else "\n대상 없음")
    print(f"  이름 못 찾음 {miss_name} · 오짝 {mispair}")
    print(f"\n★표본 {tot}레코드. 전체 정확도로 일반화하지 말 것.")
    print("★이 수치는 **지금 색인되는 텍스트** 기준이다. `table_coords` 좌표 복원은")
    print("  아직 파이프라인에 연결되어 있지 않다 — 두 수치를 섞어 말하지 말 것.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    raise SystemExit(main())
