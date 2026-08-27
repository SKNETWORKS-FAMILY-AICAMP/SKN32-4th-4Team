# -*- coding: utf-8 -*-
"""S1 사건에 **원본 PDF 레이아웃 증거**를 붙인다.

    python -m scripts.eval.s1_evidence --limit 60      # 표본
    python -m scripts.eval.s1_evidence                 # 전량(느리다)

★왜 PDF 를 다시 여나 — 페이지 text 로 대조하면 **순환논증**이다

    조 머리 분할기는 s5 의 `pages[].text` 를 보고 헤더를 찾는다.
    그러니 "그 text 에 헤더가 있나"를 물으면 **찾아낸 것은 언제나 있다.**
    답이 정해진 질문이다.

    비순환 증거는 분할기가 **안 보는 것**이라야 한다 — 글자 크기·굵기·x 좌표·
    표 영역과의 겹침. 그래서 PDF 를 span 단위로 다시 연다.

★무엇을 판정하나 (코덱스 1라운드 분류축)

    A2 헤더가 원본에 없음   → 본문·표를 헤더로 오인
    B  헤더가 원본에 없음   → **B 가 거짓 경계**
    셋 다 실재             → 부모/section 경계 누락
    그 밖                  → `unknown` (지우지 않는다)

★확신하지 않는다

    글자 크기·굵기 판정은 **정답셋으로 검증된 적이 없다.** 여기서 내는 것은
    분포와 가설이지 확정이 아니다. 게이트 규칙을 바꾸려면 표본 사람 판정이 필요하다
    (체크리스트 S1/S2 완료 정의).
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_RAW = _ROOT / "data" / "raw" / "insurance_terms"
_EXTRACTED = _ROOT / "data" / "extracted"
_EVENTS = _ROOT / "data" / "eval" / "struct_events"

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub("", s or "")


def _pdf_for(insurer: str, sha12: str) -> Path | None:
    d = _RAW / insurer
    if not d.is_dir():
        return None
    hits = sorted(d.glob(f"{sha12}_*.pdf"))
    return hits[0] if hits else None


def _page_tables(pdf, pg: int) -> list | None:
    """표 영역을 **PDF 에서 직접** 구한다. 헤더가 이 안이면 표 셀을 조 머리로 읽은 것이다.

    ★s5 의 `tables_coords` 를 쓰려 했으나 **좌표가 없다.** 이름과 달리
      `table_id`(`p3-1-2열짝짓기`)·`method`·`records` 만 있고 bbox 가 없다.
      그걸 모르고 `t.get("bbox")` 로 읽어 **1,367문서 전부 빈 목록**을 만들고 있었다 —
      `in_table` 이 언제나 False 였다. 조용한 무동작이라 더 나빴다.
      실패로 드러나게 고쳤다: 못 구하면 `None`(=검사 안 함)이지 False 가 아니다.
    """
    try:
        found = pdf[pg - 1].find_tables()
        return [tuple(t.bbox) for t in found.tables]
    except Exception:
        return None


def _spans(page) -> list[dict]:
    out = []
    for blk in page.get_text("dict").get("blocks", []):
        for line in blk.get("lines", []):
            sp = line.get("spans", [])
            if not sp:
                continue
            txt = "".join(s.get("text", "") for s in sp)
            if not txt.strip():
                continue
            out.append({
                "text": txt,
                "size": max(s.get("size", 0) for s in sp),
                "bold": any("bold" in (s.get("font", "") or "").lower() for s in sp),
                "x0": line["bbox"][0], "y0": line["bbox"][1],
                "bbox": line["bbox"],
            })
    return out


def _inside(bbox, boxes) -> bool:
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return any(bx0 <= cx <= bx1 and by0 <= cy <= by1 for bx0, by0, bx1, by1 in boxes)


def _find_head(spans, clause_no: str, title: str) -> dict | None:
    """조 머리로 보이는 **줄 시작**을 찾는다. 줄 중간 언급은 헤더가 아니다."""
    key = _norm(clause_no)
    if not key:
        return None
    ttl = _norm(title)[:12]
    cands = [s for s in spans if _norm(s["text"]).startswith(key)]
    if ttl:
        better = [s for s in cands if ttl in _norm(s["text"])]
        if better:
            cands = better
    return cands[0] if cands else None


def probe(pdf, spans_cache, tbox_cache, part: dict) -> dict:
    """A1/B/A2 하나에 대한 증거."""
    pg = part.get("page_from")
    if not pg:
        return {"found": None, "why": "page_from 없음"}
    if pg not in spans_cache:
        try:
            spans_cache[pg] = _spans(pdf[pg - 1])          # locator 는 1-based
        except Exception as e:
            return {"found": None, "why": f"페이지 열기 실패: {type(e).__name__}"}
    if pg not in tbox_cache:
        tbox_cache[pg] = _page_tables(pdf, pg)
    spans = spans_cache[pg]
    if not spans:
        return {"found": None, "why": "span 없음(스캔 이미지일 수 있다)"}

    body = statistics.median([s["size"] for s in spans]) or 1.0
    hit = _find_head(spans, part.get("clause_no") or "", part.get("title") or "")
    if hit is None:
        return {"found": False, "why": "줄 시작에 조 머리가 없다", "body_size": round(body, 2)}
    return {
        "found": True,
        "size": round(hit["size"], 2),
        "body_size": round(body, 2),
        "size_ratio": round(hit["size"] / body, 3) if body else None,
        "bold": hit["bold"],
        "x0": round(hit["x0"], 1),
        #: ★못 구했으면 None 이다. False(=표 밖) 로 단정하지 않는다.
        "in_table": (None if tbox_cache.get(pg) is None
                     else _inside(hit["bbox"], tbox_cache[pg])),
    }


def classify(a1: dict, b: dict, a2: dict) -> tuple[str, str]:
    """★확정할 수 있는 것만 확정한다. 나머지는 `unknown`."""
    if b.get("found") is False:
        return "B_false_header", "B 의 조 머리가 원본 줄 시작에 없다 — 거짓 경계"
    if a2.get("found") is False:
        return "A2_false_header", "A2 의 조 머리가 원본 줄 시작에 없다 — 본문·표를 헤더로 오인"
    if a1.get("found") is False:
        return "A1_false_header", "A1 의 조 머리가 원본 줄 시작에 없다"
    if b.get("in_table") or a2.get("in_table"):
        return "header_in_table", "조 머리가 표 영역 안에 있다 — 표 셀을 헤더로 읽었다"
    if all(x.get("found") for x in (a1, b, a2)):
        return "all_headers_real", "셋 다 원본에 실재 — 부모/section 경계 누락 쪽"
    return "unknown", "증거를 붙이지 못했다"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default="s1_aba_s6.jsonl")
    ap.add_argument("--limit", type=int, default=0, help="사건 수 제한(표본)")
    ap.add_argument("--out", default="")
    #: ★문서 단위로 쪼갠다. 한 문서의 사건은 한 조각에 모여야 PDF 를 한 번만 연다.
    ap.add_argument("--shard", default="", help="예: 2/6 — 6조각 중 2번째만 처리")
    a = ap.parse_args()

    try:
        import fitz
    except ImportError:
        print("FAIL  pymupdf 가 없다 — 증거를 붙일 수 없다")
        return 1

    src = _EVENTS / a.events
    if not src.exists():
        print(f"FAIL  {src.relative_to(_ROOT)} 이 없다 — 먼저 s1_events 를 돌려라")
        return 1
    rows = [json.loads(l) for l in src.open(encoding="utf-8") if l.strip()]
    if a.limit:
        rows = rows[:a.limit]

    out = _EVENTS / (a.out or src.name.replace(".jsonl", "_evidence.jsonl"))
    by_doc: dict[tuple, list] = collections.defaultdict(list)
    for r in rows:
        by_doc[(r["insurer"], r["sha12"])].append(r)

    if a.shard:
        i, n = (int(x) for x in a.shard.split("/"))
        keys = sorted(by_doc)                       # 결정론적으로 나눈다
        by_doc = {k: by_doc[k] for j, k in enumerate(keys) if j % n == (i - 1)}
        rows = [r for g in by_doc.values() for r in g]
        if not a.out:
            out = _EVENTS / src.name.replace(".jsonl", f"_evidence.part{i}of{n}.jsonl")

    tally = collections.Counter()
    fails = collections.Counter()
    done = 0
    with open(out, "w", encoding="utf-8") as f:
        for (insurer, sha12), group in by_doc.items():
            p = _pdf_for(insurer, sha12)
            if p is None:
                for r in group:
                    r["evidence"] = {"error": "원본 PDF 없음"}
                    tally["unknown"] += 1
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                fails["원본 없음"] += len(group)
                continue
            try:
                pdf = fitz.open(p)
            except Exception as e:
                for r in group:
                    r["evidence"] = {"error": f"열기 실패: {type(e).__name__}"}
                    tally["unknown"] += 1
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                fails["열기 실패"] += len(group)
                continue
            cache: dict[int, list] = {}
            tbox: dict[int, list | None] = {}
            for r in group:
                ev = {k: probe(pdf, cache, tbox, r[k]) for k in ("A1", "B", "A2")}
                verdict, basis = classify(ev["A1"], ev["B"], ev["A2"])
                r["evidence"] = ev
                r["verdict"], r["verdict_basis"] = verdict, basis
                tally[verdict] += 1
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                done += 1
            pdf.close()

    print(f"{out.relative_to(_ROOT)}")
    print(f"  사건 {len(rows):,} · 증거 부착 {done:,} · 문서 {len(by_doc):,}")
    if fails:
        print(f"  ★붙이지 못함: {dict(fails)}")
    print(f"\n  {'판정':<20}{'건수':>8}{'비율':>8}")
    for k, v in tally.most_common():
        print(f"  {k:<20}{v:>8,}{v/max(len(rows),1):>8.1%}")
    print("\n  ★이 판정은 정답셋으로 검증되지 않았다. 분포와 가설이지 확정이 아니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
