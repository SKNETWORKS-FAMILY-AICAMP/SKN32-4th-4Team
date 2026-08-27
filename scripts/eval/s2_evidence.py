# -*- coding: utf-8 -*-
"""S2 사건에 **원본 PDF 증거**를 붙인다 — 빠진 번호의 헤더가 실제로 있나.

    python -m scripts.eval.s2_evidence --limit 400
    python -m scripts.eval.s2_evidence --shard 1/6

★가르려는 것 (코덱스 1라운드)

    번호가 `제18조 → 제20조` 로 뛰었을 때 원인은 둘 중 하나다.

      ① **추출이 제19조를 떨어뜨렸다** → 원본 PDF 에 제19조 헤더가 **있다**
         이때 A·B 는 멀쩡하다. 이웃 ordinal 을 끄면 안 되고
         **그 페이지·문서의 completeness 를 게이트하고 재추출**해야 한다.
      ② **원문에 원래 제19조가 없다**(결번·발췌본) → 헤더가 **없다**
         이건 결함이 아니다. 게이트 대상이 아니다.

    그래서 A~B 사이 페이지에서 **빠진 번호의 조 머리를 직접 찾는다.**

★`numbered` 문서는 판정하지 않는다 (정직 기록)

    `제19조` 는 줄머리에서 거의 유일하지만, `19.` 은 목록 항목·표 행 번호와
    구별되지 않는다. 억지로 판정하면 그 숫자는 **측정이 아니라 소음**이다.
    그래서 `numbering != "article"` 인 사건은 `unknown_numbered` 로 남긴다.

★음성 대조군을 함께 돌린다

    있을 리 없는 번호(빠진 번호 + 500)로 같은 탐색을 한다.
    그게 자주 걸리면 탐지기가 헐거운 것이고, 그러면 본 판정도 못 믿는다.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_EVENTS = _ROOT / "data" / "eval" / "struct_events"

sys.path.insert(0, str(_ROOT))
#: ★탐지기를 복제하지 않는다(ARCH-003). S1 증거와 같은 것을 쓴다.
from scripts.eval.s1_evidence import _spans, _pdf_for, _norm  # noqa: E402

#: 한 사건에서 훑을 페이지 상한. 이보다 넓으면 그건 사건이 아니라 문서 성격이다.
MAX_PAGES = 12
#: 빠진 번호가 이보다 많으면 개별 확인이 무의미하다(발췌본 쪽).
MAX_MISSING = 12


def _head_re(no: int) -> re.Pattern:
    """`제19조` 를 **줄 시작**에서만 찾는다. 문장 안 인용은 헤더가 아니다."""
    return re.compile(rf"^\s{{0,6}}제\s*{no}\s*조(?:\s*의\s*\d{{1,2}})?\s*[（(\[【]?")


def _scan(spans: list[dict], no: int) -> dict | None:
    pat = _head_re(no)
    for s in spans:
        if pat.match(s["text"]):
            return s
    return None


def probe_event(pdf, cache, r: dict) -> dict:
    a, b = r["A"], r["B"]
    p0, p1 = a.get("page_from"), b.get("page_to")
    if not p0 or not p1:
        return {"status": "unknown", "why": "페이지 정보 없음"}
    if p1 - p0 + 1 > MAX_PAGES:
        return {"status": "unknown", "why": f"페이지 범위 {p1-p0+1}쪽 — 상한 초과"}
    missing = r.get("missing_numbers") or []
    if not missing:
        return {"status": "unknown", "why": "빠진 번호 없음"}
    if len(missing) > MAX_MISSING:
        return {"status": "unknown", "why": f"빠진 번호 {r['missing_count']}개 — 상한 초과"}

    spans = []
    for pg in range(p0, p1 + 1):
        if pg not in cache:
            try:
                cache[pg] = _spans(pdf[pg - 1])
            except Exception:
                cache[pg] = []
        spans.extend(cache[pg])
    if not spans:
        return {"status": "unknown", "why": "span 없음(스캔 이미지일 수 있다)"}

    found = [no for no in missing if _scan(spans, no)]
    #: ★음성 대조군 — 있을 리 없는 번호
    bogus = [no + 500 for no in missing]
    fake = [no for no in bogus if _scan(spans, no)]

    if fake:
        return {"status": "unknown", "why": f"음성 대조군이 {len(fake)}개 걸렸다 — 탐지 불신",
                "pages": [p0, p1]}
    if len(found) == len(missing):
        return {"status": "clause_really_missing", "found": found,
                "missing": missing, "pages": [p0, p1],
                "why": "빠진 번호의 조 머리가 원본에 전부 있다 — 추출이 떨어뜨렸다"}
    if not found:
        return {"status": "original_numbering_gap", "found": [],
                "missing": missing, "pages": [p0, p1],
                "why": "빠진 번호의 조 머리가 원본에 없다 — 원문 결번·발췌본. 결함이 아니다"}
    return {"status": "partially_missing", "found": found,
            "missing": missing, "pages": [p0, p1],
            "why": f"{len(found)}/{len(missing)} 만 원본에 있다"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default="s2_gap_s6.jsonl")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--shard", default="")
    a = ap.parse_args()

    try:
        import fitz
    except ImportError:
        print("FAIL  pymupdf 가 없다")
        return 1

    src = _EVENTS / a.events
    if not src.exists():
        print(f"FAIL  {src.relative_to(_ROOT)} 이 없다 — 먼저 s2_events 를 돌려라")
        return 1
    rows = [json.loads(l) for l in src.open(encoding="utf-8") if l.strip()]
    if a.limit:
        rows = rows[:a.limit]

    by_doc: dict[tuple, list] = collections.defaultdict(list)
    for r in rows:
        by_doc[(r["insurer"], r["sha12"])].append(r)

    out = _EVENTS / src.name.replace(".jsonl", "_evidence.jsonl")
    if a.shard:
        i, n = (int(x) for x in a.shard.split("/"))
        keys = sorted(by_doc)
        by_doc = {k: by_doc[k] for j, k in enumerate(keys) if j % n == (i - 1)}
        rows = [r for g in by_doc.values() for r in g]
        out = _EVENTS / src.name.replace(".jsonl", f"_evidence.part{i}of{n}.jsonl")

    tally = collections.Counter()
    with open(out, "w", encoding="utf-8") as f:
        for (insurer, sha12), group in by_doc.items():
            #: ★`numbered` 문서는 판정하지 않는다. PDF 를 열 필요도 없다.
            if group[0].get("numbering") != "article":
                for r in group:
                    r["evidence"] = {"status": "unknown_numbered",
                                     "why": "`19.` 은 목록·표 행 번호와 구별되지 않는다"}
                    r["verdict"] = "unknown_numbered"
                    tally["unknown_numbered"] += 1
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                continue
            p = _pdf_for(insurer, sha12)
            if p is None:
                for r in group:
                    r["evidence"] = {"status": "unknown", "why": "원본 PDF 없음"}
                    r["verdict"] = "unknown"
                    tally["unknown"] += 1
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                continue
            try:
                pdf = fitz.open(p)
            except Exception as e:
                for r in group:
                    r["evidence"] = {"status": "unknown", "why": f"열기 실패: {type(e).__name__}"}
                    r["verdict"] = "unknown"
                    tally["unknown"] += 1
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                continue
            cache: dict[int, list] = {}
            for r in group:
                ev = probe_event(pdf, cache, r)
                r["evidence"] = ev
                r["verdict"] = ev["status"]
                r["verdict_basis"] = ev.get("why", "")
                tally[ev["status"]] += 1
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            pdf.close()

    print(f"{out.relative_to(_ROOT)}")
    print(f"  사건 {len(rows):,} · 문서 {len(by_doc):,}")
    print(f"\n  {'판정':<26}{'건수':>8}{'비율':>8}")
    for k, v in tally.most_common():
        print(f"  {k:<26}{v:>8,}{v/max(len(rows),1):>8.1%}")
    print("\n  ★게이트 규칙 변경은 표본 사람 판정 뒤다. 이건 분포이지 확정이 아니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
