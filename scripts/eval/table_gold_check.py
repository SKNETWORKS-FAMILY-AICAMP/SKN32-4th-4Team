"""정답셋으로 표 복원을 채점한다.

★무참조 신호(행 수·단어배정률)로는 **"많다"만 알 수 있고 "맞다"는 모른다.**
  질병명↔KCD 코드 오짝은 이 서비스에서 가장 위험한 실패라 정답이 필요하다.

★표본이 작다. `data/eval/table_gold.json` 은 표 3개·레코드 22개뿐이다.
  여기 점수를 **정확도로 일반화하면 안 된다.** 확장 계획은 그 파일 머리말 참조.

실행:
    python -m scripts.eval.table_gold_check
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

_WS = re.compile(r"\s+")
_CODE = re.compile(r"[A-Z]\d{2}(?:\.\d)?")


def _norm(s: str) -> str:
    return _WS.sub("", s or "")


def _codes(s: str) -> set[str]:
    """★코드 **집합**으로 비교한다. 순서·구분자는 조판마다 다르다."""
    return set(_CODE.findall(s or ""))


def main() -> int:
    import fitz

    from scripts.extract.table_coords import extract

    gold = json.loads((_ROOT / "data" / "eval" / "table_gold.json").read_text(encoding="utf-8"))
    by_id = {t["id"]: t for t in gold["tables"]}
    man = {}
    for g in (_ROOT / "data" / "raw" / "manifests").glob("*.jsonl"):
        for line in open(g, encoding="utf-8"):
            r = json.loads(line)
            man.setdefault(r["sha256"][:12], r["saved_as"])

    tot_tp = tot_gold = tot_pred = 0
    name_err = code_err = 0
    for t in gold["tables"]:
        ref = by_id[t["same_as"]] if t.get("same_as") else t
        recs = {r["no"]: r for r in ref["records"]}
        rel = man.get(t["sha12"])
        if not rel or not (_ROOT / rel).exists():
            print(f"[건너뜀] 원본 없음 {t['id']}")
            continue
        doc = fitz.open(_ROOT / rel)
        page = doc[t["page_0based"]]
        got = {}
        for tbl in extract(page):
            for r in tbl.get("records") or []:
                cols = r["cols"]
                #: anchor 다음 열이 이름, 그 다음이 코드
                keys = sorted(cols)
                got[r["no"]] = (cols.get(keys[0], ""), cols.get(keys[-1], ""))
        doc.close()

        tp = 0
        for no, g in recs.items():
            p = got.get(no)
            if not p:
                continue
            ok_name = _norm(g["name"]) in _norm(p[0]) or _norm(p[0]) in _norm(g["name"])
            ok_code = _codes(g["codes"]) == _codes(p[1])
            if ok_name and ok_code:
                tp += 1
            else:
                if not ok_name:
                    name_err += 1
                if not ok_code:
                    code_err += 1
        tot_tp += tp
        tot_gold += len(recs)
        tot_pred += len(got)
        print(f"{t['id']}\n    정답 {len(recs)} · 예측 {len(got)} · 일치 {tp}")

    p = tot_tp / tot_pred if tot_pred else 0.0
    r = tot_tp / tot_gold if tot_gold else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    print(f"\n레코드 precision {p:.3f} · recall {r:.3f} · F1 {f1:.3f}")
    print(f"  이름 불일치 {name_err} · 코드 불일치 {code_err}")
    print(f"\n★표본 {tot_gold}레코드. 이 수치를 전체 정확도로 일반화하지 말 것.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    raise SystemExit(main())
