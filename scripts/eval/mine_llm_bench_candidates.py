"""LLM 비교 시험(12 시나리오)용 정답 근거 후보 채굴.

확정 원장(config/confirmed_documents.jsonl)의 문서에서 자기부담금·공제·통원·입원 조항 중
숫자(%·원·회·일)가 든 것을 뽑아 CSV로 낸다. 판정 게이트(eligibility)를 통과한 조항만.

실행: python scripts/eval/mine_llm_bench_candidates.py
출력: data/eval/llm_bench_candidates.csv, data/eval/llm_bench_candidates_summary.md
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from app.core import release  # noqa: E402

CLAUSE_TAG = release.load().clause_tag

from app.adapters.file_clause_store import load_clauses  # noqa: E402

LEDGER = _ROOT / "config" / "confirmed_documents.jsonl"
OUT_CSV = _ROOT / "data" / "eval" / "llm_bench_candidates.csv"
OUT_MD = _ROOT / "data" / "eval" / "llm_bench_candidates_summary.md"

GROUPS = {
    "G1_자기부담금": re.compile(r"자기부담"),
    "G2_공제": re.compile(r"공제"),
    "G3_통원": re.compile(r"통원"),
    "G4_입원": re.compile(r"입원"),
}
NUM = re.compile(r"\d+(?:\.\d+)?\s*%|\d{1,3}(?:,\d{3})*\s*만?\s*원|\d+\s*회|\d+\s*일")
SNIP = 160


def _snippet(text: str, pat: re.Pattern) -> str:
    m = pat.search(text)
    if not m:
        return text[:SNIP]
    a = max(0, m.start() - SNIP // 2)
    return text[a : a + SNIP].replace("\n", " ")


def main() -> None:
    rows = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    out: list[dict] = []
    n_annex_total = 0
    missing: list[str] = []
    for r in rows:
        sha = r["sha256"]
        try:
            clauses = load_clauses(sha, usable_only=True)
        except Exception as exc:  # 파일 없음 등 — 건너뛰되 기록
            missing.append(f"{sha[:12]} {r['insurer']} {type(exc).__name__}")
            continue
        for c in clauses:
            text = c.text or ""
            nums = NUM.findall(text)
            if not nums:
                continue
            hit = [g for g, pat in GROUPS.items() if pat.search(text)]
            if not hit:
                continue
            combo = "G5_통원+입원" if "G3_통원" in hit and "G4_입원" in hit else ""
            out.append(
                {
                    "groups": "|".join(hit + ([combo] if combo else [])),
                    "insurer": r["insurer"],
                    "generation": str(r.get("generation") or r.get("basis", {}).get("generation_expected") or ""),
                    "product_name": r["product_name"],
                    "sha12": sha[:12],
                    "qualified_no": c.qualified_no,
                    "title": c.title,
                    "page_from": c.page_from,
                    "numbers": " ".join(dict.fromkeys(n.strip() for n in nums))[:120],
                    "snippet": _snippet(text, GROUPS[hit[0]]),
                }
            )

        # 부록(별표·붙임): 자기부담률·한도 표는 대부분 여기 있다
        try:
            # 부록(별표·붙임): 자기부담률·한도 표는 대부분 여기 있다
            hits = list((_ROOT / "data" / "structured").glob(f"*/{CLAUSE_TAG}/{sha[:12]}.clauses.json"))
            doc = json.loads(hits[0].read_text(encoding="utf-8")) if hits else {}
            n_annex_total += len(doc.get("annexes") or [])
        except StopIteration:
            doc = {}
        for a in doc.get("annexes") or []:
            text = a.get("text") or ""
            nums = NUM.findall(text)
            hit = [g for g, pat in GROUPS.items() if pat.search(text)]
            if not nums or not hit:
                continue
            combo = "G5_통원+입원" if "G3_통원" in hit and "G4_입원" in hit else ""
            loc = a.get("locator") or {}
            out.append(
                {
                    "groups": "|".join(hit + ([combo] if combo else [])) + "|ANNEX",
                    "insurer": r["insurer"],
                    "generation": str(r.get("generation") or r.get("basis", {}).get("generation_expected") or ""),
                    "product_name": r["product_name"],
                    "sha12": sha[:12],
                    "qualified_no": a.get("label") or "",
                    "title": a.get("title") or "",
                    "page_from": int(loc.get("page_from") or 0),
                    "numbers": " ".join(dict.fromkeys(n.strip() for n in nums))[:120],
                    "snippet": _snippet(text, GROUPS[hit[0]]),
                }
            )

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()) if out else ["empty"])
        w.writeheader()
        w.writerows(out)

    # 요약: 보험사×세대×그룹 건수 — 12개 층화 선택용
    by = Counter()
    docs_by = defaultdict(set)
    for o in out:
        for g in o["groups"].split("|"):
            by[(o["insurer"], o["generation"], g)] += 1
            docs_by[(o["insurer"], g)].add(o["sha12"])
    lines = [
        "# LLM 비교 시험 후보 요약",
        "",
        f"- 원장 {len(rows)}건 중 조항 로드 실패 {len(missing)}건",
        f"- 후보 조항 {len(out)}건",
        "",
        "| 보험사 | 세대 | 그룹 | 조항 수 |",
        "|---|---|---|---:|",
    ]
    for (ins, gen, g), n in sorted(by.items()):
        lines.append(f"| {ins} | {gen} | {g} | {n} |")
    lines += ["", "## 그룹별 문서 수(보험사 지정/미지정 시나리오 설계용)", ""]
    for (ins, g), s in sorted(docs_by.items()):
        lines.append(f"- {ins} / {g}: {len(s)}문서")
    if missing:
        lines += ["", "## 로드 실패", ""] + [f"- {m}" for m in missing[:30]]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"candidates={len(out)} missing={len(missing)} annexes_seen={n_annex_total} tag={CLAUSE_TAG} -> {OUT_CSV.name}, {OUT_MD.name}")


if __name__ == "__main__":
    main()