"""사람 라벨 59건으로 **표 판별 신호의 분리도**를 잰다.

    python -m scripts.eval.table_label_fit

★`table_signal_fit.py` 와 무엇이 다른가

    저쪽은 내가 눈으로 고른 **참 4 · 거짓 3** 으로 맞춘 것이다. 표본이 너무 작아
    T1 임계값을 전량에 적용했더니 통과분의 **80%가 본문**이었다.

    이쪽은 팀원이 원문을 보고 찍은 **59건**이다(`data/eval/table_labels.jsonl`).

★★한계를 먼저 적는다

    라벨 단위가 **(문서, 쪽)** 이다. 한 쪽에 표가 여럿이면 **어느 표를 보고 찍었는지 모른다.**
    그래서 그 쪽의 표를 전부 같은 라벨로 본다 — 실제보다 나쁘게 나올 수도, 좋게 나올 수도 있다.
    **이 수치를 "정확도"라고 부르지 않는다.**

    `check` 3건은 팀원이 "원문 확인 필요"라 남긴 것이라 **적합에서 뺀다.**
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_LABELS = _ROOT / "data" / "eval" / "table_labels.jsonl"

KEYS = ("T1_corridor", "T2_dup_cells", "T3_left_cv", "T4_sentence",
        "T5_fragment", "T6_prose_marks", "T7_column_use", "T8_rule_span")


def _load_labels() -> list[dict]:
    out = []
    for line in _LABELS.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d.get("_meta"):
            continue
        out.append(d)
    return out


def main() -> int:
    labels = _load_labels()
    idx = {p.name.split(".")[0]: p
           for p in (_ROOT / "data" / "extracted").rglob("s5_pymupdf-1.28.0/*.json")}

    #: (라벨, 방식) → 신호 목록
    vals: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    kept = Counter()
    n_tables = Counter()

    for lb in labels:
        if lb["label"] == "check" or not lb.get("in_corpus"):
            continue
        f = idx.get(lb["sha12"])
        if not f:
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        pg = next((x for x in doc["pages"] if x["page"] == lb["page"]), None)
        if not pg:
            continue
        for t in pg.get("tables_coords") or []:
            m = t.get("method") or "?"
            n_tables[(lb["label"], m)] += 1
            #: 현재 게이트가 이 표를 싣는가
            keep = (m == "선" and t.get("is_table") is not False) or t.get("is_table") is True
            kept[(lb["label"], "실림" if keep else "보류")] += 1
            sig = t.get("signals") or {}
            #: T7 은 산출물에 없을 수 있어 여기서 계산한다
            if "T7_column_use" not in sig:
                recs = t.get("records") or []
                cols = t.get("cols") or 0
                used = {c for r in recs for c, v in (r.get("cols") or {}).items() if str(v).strip()}
                if cols > 1:
                    sig = {**sig, "T7_column_use": len(used) / max(cols - 1, 1)}
            for k in KEYS:
                v = sig.get(k)
                if v is not None:
                    vals[(lb["label"], m, k)].append(float(v))

    print(f"라벨 {len(labels)}건 (check 제외하고 적합)")
    print(f"표 수: {dict(n_tables)}")
    print(f"현재 게이트: {dict(kept)}")

    for method in ("선", "2열짝짓기"):
        print(f"\n── {method} ── 신호별 분리도")
        any_row = False
        for k in KEYS:
            t = vals.get(("true", method, k)) or []
            f_ = vals.get(("false", method, k)) or []
            if len(t) < 2 or len(f_) < 2:
                continue
            any_row = True
            gap = min(f_) - max(t)
            mark = "★갈림" if gap > 0 else " 겹침"
            extra = f"  → 임계값 {(max(t) + min(f_)) / 2:.3f}" if gap > 0 else ""
            print(f"  {k:16s} 참 n={len(t):<3} [{min(t):.3f}~{max(t):.3f}] med {statistics.median(t):.3f}"
                  f"   거짓 n={len(f_):<3} [{min(f_):.3f}~{max(f_):.3f}] med {statistics.median(f_):.3f}"
                  f"   간격 {gap:+.3f}{mark}{extra}")
        if not any_row:
            print("  (양쪽 표본이 2개 미만이라 잴 수 없다)")

    print("\n★라벨 단위가 **(문서, 쪽)** 이다 - 한 쪽에 표가 여럿이면 어느 표인지 모른다.")
    print("★이 수치를 '정확도'라고 부르지 않는다. 분리도만 말한다.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    raise SystemExit(main())
