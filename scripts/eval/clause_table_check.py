"""표가 **판정까지 도달하는가** — 조항 JSON 의 `tables[].records` 를 채점한다.

★검증이 셋인데 **묻는 것이 다 다르다.** 섞어 말하면 안 된다.

    `table_gold_check`        `table_coords.extract()` 가 맞나 — 추출기 단독
    `annex_table_text_check`  평평해진 **텍스트**에서 짝이 살아 있나 — 통합 전 기준선
    이 파일                   조항 JSON 까지 **실려 왔나** — 판정이 실제로 읽는 것

  추출기가 완벽해도 파이프라인에 안 실리면 판정은 못 쓴다.
  실제로 그랬다 — `table_coords` 는 F1 1.000 인데 부르는 곳이 평가 스크립트뿐이었다.

판정 기준: 정답 레코드의 질병명과 코드 집합이 **한 레코드 안에서** 함께 나와야 한다.
           다른 질병의 코드가 섞이면 오짝이다.

실행:
    python -m scripts.eval.clause_table_check
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CODE = re.compile(r"[A-Z]\d{2}(?:\.\d)?")
_WS = re.compile(r"\s+")

def _clause_tag() -> str:
    """★세대를 여기 박지 않는다. 승인 릴리스에서 읽는다.

    평가 스크립트가 상수를 들고 있으면 **승인을 바꿔도 옛 세대를 채점한다** —
    그러면 "좋아졌다"가 무엇에 대한 말인지 알 수 없다.
    `--clause-tag` 로 shadow 세대를 명시할 수 있다.
    """
    import argparse

    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--clause-tag", default="")
    a, _ = ap.parse_known_args()
    if a.clause_tag:
        return a.clause_tag
    from app.core import release

    return release.load().clause_tag


def _norm(s: str) -> str:
    """공백과 **쉼표**를 지운다.

    ★쉼표를 정답에서만 지우고 레코드에서는 안 지웠더니
      `관절병증, 연골병증` 이 "못 찾음"으로 찍혔다. 실제로는 실려 있었다 —
      **측정이 만든 가짜 실패**다. 양쪽에 같은 정규화를 건다.
    """
    return _WS.sub("", (s or "").replace(",", "").replace("，", ""))


def main() -> int:
    gold = json.loads((_ROOT / "data" / "eval" / "table_gold.json").read_text(encoding="utf-8"))
    by_id = {t["id"]: t for t in gold["tables"]}

    tag = _clause_tag()
    print(f"[세대] {tag}")
    tot = hit = miss = mispair = n_extra = 0
    for t in gold["tables"]:
        ref = by_id[t["same_as"]] if t.get("same_as") else t
        recs = ref["records"]
        want_by_no = {r["no"]: set(_CODE.findall(r["codes"])) for r in recs}
        page1 = t["page_0based"] + 1        # 조항 JSON 은 1-based

        paths = list((_ROOT / "data" / "structured").glob(
            f"*/{tag}/{t['sha12']}.clauses.json"))
        if not paths:
            print(f"[건너뜀] 조항 JSON 없음 {t['id']}")
            continue
        doc = json.loads(paths[0].read_text(encoding="utf-8"))

        #: ★조항과 부록을 **둘 다** 본다. 표가 어디에 실렸는지 가정하지 않는다.
        #: ★`table_id` 로 **중복을 뺀다.** 같은 표가 조항과 부록에 둘 다 붙으면
        #:   레코드가 두 벌 들어와 "정답 22 · 실린 44" 처럼 보인다.
        #:   그건 표가 두 개라는 뜻이 아니라 **한 표가 두 번 붙었다**는 뜻이다.
        seen_tid: set[str] = set()
        got: list[dict] = []
        n_attach = 0
        for key in ("clauses", "annexes"):
            for x in doc.get(key) or []:
                for tb in x.get("tables") or []:
                    if tb.get("page") != page1:
                        continue
                    n_attach += 1
                    tid = tb.get("table_id") or f"{tb.get('panel')}-{tb.get('method')}"
                    if tid in seen_tid:
                        continue
                    seen_tid.add(tid)
                    got.extend(tb.get("records") or [])
        if not got:
            print(f"{t['id']}\n    p{page1} 에 실린 표 레코드 **0개** — 파이프라인에 안 실렸다")
            tot += len(recs)
            miss += len(recs)
            continue

        #: 레코드를 "이름 + 코드집합"으로 평탄화한다. 열 이름은 조판마다 다르다.
        flat = []
        for r in got:
            cols = r.get("cols") or {}
            joined = " ".join(str(v) for v in cols.values())
            flat.append((r.get("no"), _norm(joined), set(_CODE.findall(joined))))

        n_ok = n_miss = n_pair = 0
        for g in recs:
            tot += 1
            name = _norm(g["name"])
            want = want_by_no[g["no"]]
            others = set().union(*(v for k, v in want_by_no.items() if k != g["no"]))
            #: 이름이 들어 있는 레코드를 찾는다. `no` 로 찾지 않는다 —
            #: 2열 경로의 `no` 는 행 순서라 원문 번호가 아니다(`no_source`).
            cand = [c for c in flat if name and name in c[1]]
            if not cand:
                n_miss += 1
                continue
            if any(want <= c[2] and not (c[2] & (others - want)) for c in cand):
                n_ok += 1
            else:
                n_pair += 1
        hit += n_ok
        miss += n_miss
        mispair += n_pair
        print(f"{t['id']}\n    정답 {len(recs)} · 실린 레코드 {len(got)} · "
              f"짝 맞음 {n_ok} · 이름 못 찾음 {n_miss} · 오짝 {n_pair}")

    if not tot:
        print("\n대상 없음")
        return 0
    print(f"\n조항 JSON 도달 짝 정확도 {hit}/{tot} = {hit / tot:.3f}")
    print(f"  이름 못 찾음 {miss} · 오짝 {mispair}")
    print(f"\n★표본 {tot}레코드. 전체 정확도로 일반화하지 말 것.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    raise SystemExit(main())
