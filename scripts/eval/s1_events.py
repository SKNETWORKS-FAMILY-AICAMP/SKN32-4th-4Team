# -*- coding: utf-8 -*-
"""S1 A-B-A 재진입 사건의 **위치**를 남긴다.

    python -m scripts.eval.s1_events --schema s6
    python -m scripts.eval.s1_events --schema s6 --evidence   # PDF 재열람 증거까지(느림)

★왜 필요한가

    `structure_faults` 는 **개수만** 저장한다(`{"S1_aba_reentry": 4613, ...}`).
    `gated_ordinals` 에 끈 조항이 남지만, 그건 결과이지 사건이 아니다 —
    어느 A-B-A 쌍에서 났는지, 왜 났는지는 어디에도 없다.
    그래서 4,613건이 "원인 미규명"으로 남아 있었다.

★자기 대조를 한다

    여기서 다시 센 사건 수가 산출물의 `S1_aba_reentry` 와 **정확히 일치**해야 한다.
    다르면 이 스크립트가 원래 판정 로직을 잘못 옮긴 것이고, 그러면
    여기서 나온 "원인"도 못 믿는다. 불일치는 실패로 보고한다.

★분류축을 페이지·표로 잡지 않는다 (코덱스 1라운드)

    내 초안은 `같은 페이지 / 페이지 경계 / 표 안 / 부록 근처` 로 나누려 했다.
    **안 갈린다** — 실측: 같은 section 91.4% · 두 A 제목 완전일치 71.8% ·
    같은 페이지 31.1% · 표 페이지 포함 61.7%.
    진짜 1차 축은 **원본 PDF 에 그 헤더가 실재하는가** 다(`--evidence`).

★자동판정 불가는 `unknown` 으로 **보존한다.** 지우지 않는다.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_STRUCTURED = _ROOT / "data" / "structured"
_RAW = _ROOT / "data" / "raw" / "insurance_terms"
_OUT = _ROOT / "data" / "eval" / "struct_events"

sys.path.insert(0, str(_ROOT))
#: ★번호 파싱을 두 번 쓰지 않는다. 갈라지면 사건 수가 안 맞는다(ARCH-003).
from scripts.extract.to_clauses import _clause_num  # noqa: E402

SNIPPET = 90


def _blocks(doc: dict) -> list[dict]:
    """`to_clauses.py` 가 `structure_faults()` 에 넘기는 것과 **같은 모양**으로 만든다."""
    kind = doc.get("numbering") or "article"
    return [
        {"no": _clause_num(c.get("clause_no") or ""), "kind": kind, "c": c}
        for c in (doc.get("clauses") or [])
    ]


def _aba(blocks: list[dict]) -> list[tuple[int, int, int]]:
    """`struct_audit.structure_faults` 의 A-B-A 판정을 **그대로** 옮긴다.

    원본:
        for seq in by_kind.values():
            for i in range(2, len(seq)):
                if seq[i][1] == seq[i-2][1] and seq[i][1] != seq[i-1][1]:
    """
    by_kind: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    for i, b in enumerate(blocks):
        if isinstance(b.get("no"), int):
            by_kind[b.get("kind", "article")].append((i, b["no"]))
    out = []
    for seq in by_kind.values():
        for i in range(2, len(seq)):
            if seq[i][1] == seq[i - 2][1] and seq[i][1] != seq[i - 1][1]:
                out.append((seq[i - 2][0], seq[i - 1][0], seq[i][0]))
    return out


def _brief(c: dict) -> dict:
    loc = c.get("locator") or {}
    return {
        "ordinal": c.get("ordinal"),
        "clause_no": c.get("clause_no"),
        "title": c.get("title"),
        "section": c.get("section"),
        "qualified_no": c.get("qualified_no"),
        "page_from": loc.get("page_from"),
        "page_to": loc.get("page_to"),
        "char_offset": loc.get("char_offset"),
        "char_length": c.get("char_length"),
        "tables_on_pages": bool(c.get("tables_on_pages")),
        "head": (c.get("text") or "")[:SNIPPET].replace("\n", " "),
    }


def events_for(doc: dict, sha12: str, insurer: str) -> list[dict]:
    blocks = _blocks(doc)
    rows = []
    for i_a1, i_b, i_a2 in _aba(blocks):
        a1, b, a2 = (blocks[i]["c"] for i in (i_a1, i_b, i_a2))
        la1, lb, la2 = (_brief(x) for x in (a1, b, a2))
        rows.append({
            "sha12": sha12,
            "insurer": insurer,
            "numbering": doc.get("numbering"),
            "A1": la1, "B": lb, "A2": la2,
            #: 관찰값 — 분류가 아니라 **사실**만. 판정은 증거 단계에서 붙인다.
            "obs": {
                "same_section": la1["section"] == la2["section"] == lb["section"],
                "a_titles_equal": (la1["title"] or "") == (la2["title"] or ""),
                "same_page": la1["page_from"] == la2["page_from"] is not None,
                "page_span": [la1["page_from"], la2["page_to"]],
                "any_table": la1["tables_on_pages"] or lb["tables_on_pages"] or la2["tables_on_pages"],
                "b_shorter_than_200": (lb["char_length"] or 0) < 200,
                "a2_shorter_than_200": (la2["char_length"] or 0) < 200,
                "page_backwards": (la2["page_from"] is not None and la1["page_from"] is not None
                                   and la2["page_from"] < la1["page_from"]),
                "gap_ordinals": (la2["ordinal"] or 0) - (la1["ordinal"] or 0),
            },
            #: ★증거를 못 붙였으면 `unknown` 이다. 비워 두지 않는다.
            "verdict": "unknown",
            "verdict_basis": "",
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default="s6")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    _OUT.mkdir(parents=True, exist_ok=True)
    out = _OUT / f"s1_aba_{a.schema}.jsonl"

    files = sorted(_STRUCTURED.glob(f"*/{a.schema}_*/*.clauses.json"))
    if a.limit:
        files = files[:a.limit]

    rows, recorded, mismatch = [], 0, []
    for p in files:
        doc = json.loads(p.read_text(encoding="utf-8"))
        sha12, insurer = p.name.split(".")[0], p.parent.parent.name
        ev = events_for(doc, sha12, insurer)
        want = int((doc.get("structure_faults") or {}).get("S1_aba_reentry") or 0)
        recorded += want
        if len(ev) != want:
            mismatch.append((sha12, want, len(ev)))
        rows.extend(ev)

    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"{out.relative_to(_ROOT)}")
    print(f"  문서 {len(files):,} · 사건 {len(rows):,}")
    print(f"  산출물이 적어둔 S1 합계 {recorded:,}")
    #: ★자기 대조. 안 맞으면 판정 로직을 잘못 옮긴 것이다.
    if mismatch:
        print(f"  ★불일치 {len(mismatch)}문서 — 판정 로직을 잘못 옮겼다")
        for sha, w, g in mismatch[:10]:
            print(f"    {sha}: 기록 {w} / 재계산 {g}")
        return 1
    print("  ✅ 문서별 개수가 전부 일치한다")

    # ── 관찰값 분포 ──────────────────────────────────────────────
    if rows:
        print(f"\n  {'관찰':<22}{'건수':>8}{'비율':>8}")
        keys = ["same_section", "a_titles_equal", "same_page", "any_table",
                "b_shorter_than_200", "a2_shorter_than_200", "page_backwards"]
        for k in keys:
            n = sum(1 for r in rows if r["obs"].get(k))
            print(f"  {k:<22}{n:>8,}{n/len(rows):>8.1%}")
        gaps = collections.Counter(r["obs"]["gap_ordinals"] for r in rows)
        print(f"  ordinal 간격 상위: {dict(gaps.most_common(5))}")
        docs = collections.Counter(r["sha12"] for r in rows)
        print(f"  사건이 난 문서 {len(docs):,} · 한 문서 최대 {max(docs.values()):,}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
