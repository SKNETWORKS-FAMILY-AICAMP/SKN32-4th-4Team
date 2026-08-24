"""표 판별 신호의 **임계값을 데이터로 맞춘다.**

    python -m scripts.eval.table_signal_fit

★임계값을 지어내지 않는다. 알려진 참/거짓 집합에서 각 신호의 분포를 재고,
  **겹치지 않는 구간**이 있으면 그 사이를 임계값으로 쓴다. 겹치면 그 신호는 약하다고 적는다.

★표본이 작다. 참 3 · 거짓 4 다. 이걸로 "정확도"를 말하지 않는다 —
  **명백한 오탐을 거르는 하한선**을 정하는 데만 쓴다. 표본은 계획서 L1 에서 늘린다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

#: `(sha12, 0-based page, 라벨, 메모)` — 사람이 원문을 열어 확인한 것만.
CASES = [
    # ── 진짜 표 ──
    ("02aaee47b190", 52, True, "DB 용어정의표 — 좌 용어 / 우 정의"),
    ("02aaee47b190", 53, True, "DB 용어정의표 이어지는 쪽"),
    ("ff0521a99902", 108, True, "흥국화재 특정질병 분류표(정답셋)"),
    # ── 표가 아닌 것(본문 두 단 조판) ──
    ("01dda5aefa18", 48, False, "삼성화재 — '늦어지는 경우 보험수익자…' 본문"),
    ("04c08230f158", 120, False, "삼성화재 — '응급환자의 이송 등…' 이 연속 3행 반복"),
    ("02aaee47b190", 33, False, "DB — 한 문장을 두 열로 쪼개 어절이 뒤섞임"),
    ("02aaee47b190", 45, False, "DB — '보험나이 계산 예시' 본문"),
]

KEYS = ("T1_corridor", "T2_dup_cells", "T3_left_cv", "T4_sentence", "T5_fragment", "T6_prose_marks")


def _manifest() -> dict:
    man = {}
    for g in (_ROOT / "data" / "raw" / "manifests").glob("*.jsonl"):
        for line in open(g, encoding="utf-8"):
            r = json.loads(line)
            man.setdefault(r["sha256"][:12], r["saved_as"].replace("\\", "/"))
    return man


def main() -> int:
    import fitz

    from scripts.extract.table_coords import extract

    man = _manifest()
    rows = []
    for sha, pno, label, memo in CASES:
        rel = man.get(sha)
        if not rel or not (_ROOT / rel).exists():
            print(f"[건너뜀] 원본 없음 {sha}")
            continue
        doc = fitz.open(_ROOT / rel)
        for t in extract(doc[pno]):
            if t.get("method") != "2열짝짓기" or not t.get("signals"):
                continue
            rows.append((label, sha, pno + 1, t["signals"], memo))
        doc.close()

    if not rows:
        print("대상 없음")
        return 0

    print(f"{'라벨':4s} {'문서':14s}{'쪽':>5}  " + "  ".join(f"{k:>13s}" for k in KEYS))
    for label, sha, pg, s, memo in rows:
        vals = "  ".join(
            ("      (못잼)" if s.get(k) is None else f"{s.get(k, 0):13.3f}") for k in KEYS)
        print(f"{'참' if label else '거짓':4s} {sha:14s}{pg:5d}  {vals}   {memo[:28]}")

    print("\n── 신호별 분리도 ──")
    for k in KEYS:
        t = [r[3][k] for r in rows if r[0] and r[3].get(k) is not None]
        f = [r[3][k] for r in rows if not r[0] and r[3].get(k) is not None]
        if not t or not f:
            print(f"{k:15s} 표본 부족")
            continue
        #: 참은 작아야 좋은 신호(T1·T2·T4·T5)를 가정한다. T3 는 방향이 반대일 수 있어 둘 다 본다.
        gap_lo = min(f) - max(t)      # 참 최대 < 거짓 최소 이면 양수 = 깨끗이 갈림
        mark = "★갈림" if gap_lo > 0 else " 겹침"
        print(f"{k:15s} 참 [{min(t):.3f}~{max(t):.3f}]  거짓 [{min(f):.3f}~{max(f):.3f}]"
              f"  간격 {gap_lo:+.3f} {mark}"
              + (f"  → 임계값 {(max(t) + min(f)) / 2:.3f}" if gap_lo > 0 else ""))

    print("\n★표본 참 %d · 거짓 %d. 이 값으로 정확도를 말하지 않는다."
          % (sum(1 for r in rows if r[0]), sum(1 for r in rows if not r[0])))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    raise SystemExit(main())
