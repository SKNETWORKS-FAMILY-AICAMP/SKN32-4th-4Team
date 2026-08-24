"""Refine A1 uncovered pages into deterministic cause/recovery proxies.

The output is still an audit artifact.  It never mutates clause locators and it
does not turn proxy classes into serving decisions.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from scripts.eval.outside_clause_pages import ROOT, WS


STATUTE_REFERENCE = re.compile(r"【\s*법규\s*\d*\s*】|(?:^|\n)\s*참\s*고(?:\s|$)")
FRONT_MATTER = re.compile(
    r"가입자\s*유의사항|주요\s*내용\s*요약서|보험\s*용어\s*해설|"
    r"보험금\s*청구시\s*준비|신용정보\s*제공|고객\s*권리\s*안내"
)
ANNEX_MATERIAL = re.compile(
    r"(?:^|\n)\s*(?:별\s*표|붙\s*임|별\s*첨)|장해의\s*분류|장해\s*분류\s*지급률|"
    r"특정\s*질병\s*분류표|질병\s*분류표|재해\s*분류표"
)


def _norm(text: str) -> str:
    return WS.sub("", text or "")


def content_reachability(
    page_text: str,
    corpus_text: str,
    *,
    width: int = 48,
    corpus_is_normalized: bool = False,
) -> dict[str, Any]:
    """Estimate whether page content already exists in clause/annex text.

    Non-overlapping fixed-width anchors make this deterministic and cheap.  It
    is intentionally an estimate: repeated headers and boilerplate can match by
    chance, so the ratio is stored rather than silently converted to truth.
    """
    page = _norm(page_text)
    corpus = corpus_text if corpus_is_normalized else _norm(corpus_text)
    if len(page) < width:
        return {"anchor_width": width, "anchors": 0, "matched": 0, "ratio": None}
    anchors = [page[i:i + width] for i in range(0, len(page) - width + 1, width)]
    matched = sum(1 for anchor in anchors if anchor in corpus)
    return {
        "anchor_width": width,
        "anchors": len(anchors),
        "matched": matched,
        "ratio": round(matched / len(anchors), 4),
    }


def cause_proxy(row: dict[str, Any], reach: dict[str, Any]) -> tuple[str, list[str]]:
    text = row.get("text_preview") or ""
    ratio = reach.get("ratio")
    reasons: list[str] = []
    if row.get("risk_class") == "blank_or_image_only_proxy":
        return "blank_or_image_only_proxy", ["텍스트 10자 이하·표 없음"]
    if STATUTE_REFERENCE.search(text):
        return "statute_reference_proxy", ["법규/참고 표지"]
    if row.get("gap_context") == "before_first_covered" and FRONT_MATTER.search(text):
        return "front_matter_proxy", ["첫 조항 앞 안내문/요약서 표지"]
    if ANNEX_MATERIAL.search(text):
        reasons.append("별표·붙임·장해분류 재료")
        if ratio is not None and ratio <= 0.25:
            reasons.append(f"조항·부록 본문 anchor 도달률 {ratio:.2f}")
        return "annex_boundary_candidate", reasons
    if ratio is not None and ratio >= 0.65:
        return "locator_only_proxy", [f"본문 anchor 도달률 {ratio:.2f}"]
    if row.get("gap_context") == "between_covered" and ratio is not None and ratio <= 0.25:
        return "content_loss_candidate", [f"앞뒤 조항 사이·본문 anchor 도달률 {ratio:.2f}"]
    if ratio is not None and 0.25 < ratio < 0.65:
        return "partial_reach_candidate", [f"본문 anchor 도달률 {ratio:.2f}"]
    if row.get("gap_context") == "before_first_covered":
        return "unclassified_front_zone", ["첫 조항 앞·알려진 안내문 표지 없음"]
    return "unclassified_gap", ["자동 원인 확정 불가"]


def _dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def run(audit_path: Path, *, page_tag: str, clause_tag: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in base:
        grouped[(row["insurer_dir"], row["sha12"])].append(row)

    output: list[dict[str, Any]] = []
    counts: collections.Counter[str] = collections.Counter()
    for (insurer, sha12), rows in sorted(grouped.items()):
        page_path = ROOT / "data/extracted" / insurer / page_tag / f"{sha12}.json"
        clause_path = ROOT / "data/structured" / insurer / clause_tag / f"{sha12}.clauses.json"
        page_doc = json.loads(page_path.read_text(encoding="utf-8"))
        clause_doc = json.loads(clause_path.read_text(encoding="utf-8"))
        by_page = {page["page"]: page for page in page_doc.get("pages") or []}
        corpus = _norm("\n".join(
            item.get("text") or ""
            for key in ("clauses", "annexes")
            for item in (clause_doc.get(key) or [])
        ))
        for base_row in rows:
            full_text = (by_page.get(base_row["page"]) or {}).get("text") or ""
            reach = content_reachability(full_text, corpus, corpus_is_normalized=True)
            cause, reasons = cause_proxy(base_row, reach)
            row = dict(base_row)
            row.update({"cause_proxy": cause, "cause_reasons": reasons, "content_reachability": reach})
            output.append(row)
            counts[f"cause:{cause}"] += 1
            counts[f"cause_context:{cause}|{row['gap_context']}"] += 1

    output.sort(key=lambda row: (row["insurer_dir"], row["sha12"], row["page"]))
    run_rows: dict[tuple[str, str, int, int], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in output:
        run_rows[(row["insurer_dir"], row["sha12"], row["gap_start"], row["gap_end"])].append(row)
    run_summary: collections.Counter[str] = collections.Counter()
    run_page_summary: collections.Counter[str] = collections.Counter()
    for members in run_rows.values():
        causes = collections.Counter(row["cause_proxy"] for row in members)
        context = members[0]["gap_context"]
        # A marker often appears only on the first page of a multi-page legal or
        # annex block.  Propagate only strong markers within the same contiguous
        # uncovered run; never across a covered page.
        if "statute_reference_proxy" in causes:
            run_cause = "statute_reference_run_proxy"
        elif context == "before_first_covered" and "front_matter_proxy" in causes:
            run_cause = "front_matter_run_proxy"
        elif "annex_boundary_candidate" in causes:
            run_cause = "annex_boundary_run_candidate"
        else:
            run_cause = sorted(causes.items(), key=lambda item: (-item[1], item[0]))[0][0]
        run_summary[run_cause] += 1
        run_page_summary[run_cause] += len(members)
        for row in members:
            row["run_cause_proxy"] = run_cause
    summary = {
        "schema_version": "a1-gap-causes-v1",
        "classification_warning": "cause_proxy and reachability are deterministic audit proxies, not human labels",
        "page_tag": page_tag,
        "clause_tag": clause_tag,
        "pages": len(output),
        "gap_runs": len(run_rows),
        "page_counts": dict(sorted(counts.items())),
        "dominant_run_counts": dict(sorted(run_summary.items())),
        "run_page_counts": dict(sorted(run_page_summary.items())),
    }
    return output, summary


def review_sample(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    priority = {
        "content_loss_candidate": 0,
        "annex_boundary_candidate": 1,
        "partial_reach_candidate": 2,
        "locator_only_proxy": 3,
        "unclassified_gap": 4,
        "statute_reference_proxy": 5,
        "front_matter_proxy": 6,
        "unclassified_front_zone": 7,
        "blank_or_image_only_proxy": 8,
    }
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        key = (row["cause_proxy"], row["gap_context"], row["insurer_dir"])
        buckets[key].append(row)
    population = {key: len(value) for key, value in buckets.items()}
    for bucket in buckets.values():
        bucket.sort(key=lambda row: hashlib.sha256(f"{row['sha12']}:{row['page']}".encode()).hexdigest())
    keys = sorted(buckets, key=lambda key: (priority.get(key[0], 99), key[1], key[2]))
    sample: list[dict[str, Any]] = []
    while len(sample) < limit and any(buckets.values()):
        for key in keys:
            if len(sample) >= limit:
                break
            if not buckets[key]:
                continue
            row = dict(buckets[key].pop(0))
            row.update({
                "sampling_stratum": "|".join(key),
                "stratum_population": population[key],
                "review_label": None,
                "review_notes": "",
            })
            sample.append(row)
    return sample


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, default=ROOT / "data/eval/outside_clause_pages_s6.jsonl")
    parser.add_argument("--page-tag", default="s5_pymupdf-1.28.0")
    parser.add_argument("--clause-tag", default="s6_pymupdf-1.28.0")
    parser.add_argument("--output", type=Path, default=ROOT / "data/eval/a1_gap_causes_s6.jsonl")
    parser.add_argument("--summary", type=Path, default=ROOT / "data/eval/a1_gap_causes_s6_summary.json")
    parser.add_argument("--review", type=Path, default=ROOT / "data/eval/a1_gap_causes_s6_review240.jsonl")
    parser.add_argument("--review-limit", type=int, default=240)
    args = parser.parse_args()
    rows, summary = run(args.audit, page_tag=args.page_tag, clause_tag=args.clause_tag)
    for path in (args.output, args.summary, args.review):
        path.parent.mkdir(parents=True, exist_ok=True)
    _dump_jsonl(args.output, rows)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _dump_jsonl(args.review, review_sample(rows, args.review_limit))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
