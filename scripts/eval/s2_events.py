# -*- coding: utf-8 -*-
"""S2 번호 건너뜀 사건의 **위치**를 남긴다.

    python -m scripts.eval.s2_events --schema s6

★S1 과 다르게 다뤄야 한다 (코덱스 1라운드)

    S1 은 게이트가 돌고 있다(B·두 번째 A 를 끈다). S2 는 **게이트하지 않는다** —
    원문 자체의 결번·발췌 문서도 걸리는 저정밀 신호이기 때문이다.

    ★그리고 게이트 **대상**부터 틀렸다.
      번호가 진짜 빠졌다고 확인돼도 **A·B 가 잘못된 게 아니다.**
      이웃 ordinal 을 끄면 멀쩡한 조항을 버린다. 그때는
      **그 페이지·문서의 completeness 를 게이트하고 재추출**해야 한다.
      `B` 를 조항 게이트하는 건 **B 가 거짓 헤더로 확인된 경우에만**.

    그래서 이 사건들은 결국 **두 갈래**로 나뉘어야 한다.
      `B_false_header`      → 조항 게이트
      `clause_really_missing` → 페이지·문서 게이트 + 재추출 대상

★여기서 하는 것

    위치와 **관찰값**만 남긴다. 판정은 증거 단계(`s2_evidence`)에서 붙인다.
    자동판정 불가는 `unknown` 으로 보존한다.

★자기 대조

    재계산한 사건 수가 산출물의 `S2_number_gap` 과 **정확히 일치**해야 한다.
    다르면 판정 로직을 잘못 옮긴 것이므로 실패로 보고한다.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_STRUCTURED = _ROOT / "data" / "structured"
_OUT = _ROOT / "data" / "eval" / "struct_events"

sys.path.insert(0, str(_ROOT))
#: ★번호 파싱을 복제하지 않는다(ARCH-003). S1 쪽과 같은 함수를 쓴다.
from scripts.extract.to_clauses import _clause_num  # noqa: E402
from scripts.eval.s1_events import _blocks, _brief  # noqa: E402

#: 이보다 크게 뛰면 "발췌 문서"일 가능성이 커진다. 분류가 아니라 관찰 구간이다.
BIG_GAP = 5


def _gaps(blocks: list[dict]) -> list[tuple[int, int]]:
    """`struct_audit.structure_faults` 의 번호 비연속 판정을 **그대로** 옮긴다.

        for a, b in zip(seq, seq[1:]):
            if b[1] > a[1] + 1:
    """
    by_kind: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    for i, b in enumerate(blocks):
        if isinstance(b.get("no"), int):
            by_kind[b.get("kind", "article")].append((i, b["no"]))
    out = []
    for seq in by_kind.values():
        for a, b in zip(seq, seq[1:]):
            if b[1] > a[1] + 1:
                out.append((a[0], b[0]))
    return out


def events_for(doc: dict, sha12: str, insurer: str) -> list[dict]:
    blocks = _blocks(doc)
    rows = []
    for i_a, i_b in _gaps(blocks):
        a, b = blocks[i_a], blocks[i_b]
        la, lb = _brief(a["c"]), _brief(b["c"])
        missing = list(range(a["no"] + 1, b["no"]))
        rows.append({
            "sha12": sha12,
            "insurer": insurer,
            "numbering": doc.get("numbering"),
            "A": la, "B": lb,
            "missing_numbers": missing[:20],
            "missing_count": len(missing),
            "obs": {
                "same_section": la["section"] == lb["section"],
                #: ★"같은 페이지"는 정의가 갈린다. **둘 다 남긴다.**
                #:   한 번 어긋난 적이 있다 — 나는 68.6%, 코덱스는 95.7% 를 말했는데
                #:   둘 다 맞았다. 아래 두 줄이 각각 그 수다(18,931 / 26,423).
                "same_start_page": (la["page_from"] is not None
                                    and la["page_from"] == lb["page_from"]),
                "pages_overlap": (la["page_to"] is not None
                                  and la["page_to"] == lb["page_from"]),
                "page_span": [la["page_from"], lb["page_to"]],
                "any_table": la["tables_on_pages"] or lb["tables_on_pages"],
                "single_gap": len(missing) == 1,
                "big_gap": len(missing) >= BIG_GAP,
                "a_short": (la["char_length"] or 0) < 200,
                "b_short": (lb["char_length"] or 0) < 200,
                #: 부가 바뀌면 새 특약이 시작한 것일 수 있다 — 번호가 재시작한다.
                "section_changed": la["section"] != lb["section"],
                "ordinal_step": (lb["ordinal"] or 0) - (la["ordinal"] or 0),
            },
            #: ★증거 전이므로 판정하지 않는다.
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
    out = _OUT / f"s2_gap_{a.schema}.jsonl"
    files = sorted(_STRUCTURED.glob(f"*/{a.schema}_*/*.clauses.json"))
    if a.limit:
        files = files[:a.limit]

    rows, recorded, mismatch = [], 0, []
    for p in files:
        doc = json.loads(p.read_text(encoding="utf-8"))
        sha12, insurer = p.name.split(".")[0], p.parent.parent.name
        ev = events_for(doc, sha12, insurer)
        want = int((doc.get("structure_faults") or {}).get("S2_number_gap") or 0)
        recorded += want
        if len(ev) != want:
            mismatch.append((sha12, want, len(ev)))
        rows.extend(ev)

    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"{out.relative_to(_ROOT)}")
    print(f"  문서 {len(files):,} · 사건 {len(rows):,}")
    print(f"  산출물이 적어둔 S2 합계 {recorded:,}")
    if mismatch:
        print(f"  ★불일치 {len(mismatch)}문서 — 판정 로직을 잘못 옮겼다")
        for sha, w, g in mismatch[:10]:
            print(f"    {sha}: 기록 {w} / 재계산 {g}")
        return 1
    print("  ✅ 문서별 개수가 전부 일치한다")

    if rows:
        print(f"\n  {'관찰':<22}{'건수':>8}{'비율':>8}")
        for k in ("same_section", "same_start_page", "pages_overlap", "any_table", "single_gap",
                  "big_gap", "a_short", "b_short", "section_changed"):
            n = sum(1 for r in rows if r["obs"].get(k))
            print(f"  {k:<22}{n:>8,}{n/len(rows):>8.1%}")
        mc = collections.Counter(min(r["missing_count"], 10) for r in rows)
        print(f"  빠진 개수 분포(10+ 묶음): {dict(sorted(mc.items()))}")
        docs = collections.Counter(r["sha12"] for r in rows)
        print(f"  사건 문서 {len(docs):,} · 한 문서 최대 {max(docs.values()):,}건")
        #: 한 문서에 사건이 몰리면 그 문서는 **발췌본**일 가능성이 크다.
        heavy = [s for s, n in docs.items() if n >= 50]
        print(f"  사건 50건 이상 문서 {len(heavy):,} — 발췌본 의심(개별 결함이 아니라 문서 성격)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
