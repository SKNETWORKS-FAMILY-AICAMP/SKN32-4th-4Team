"""Audit pages not covered by any S6 clause or annex locator.

This is a triage artifact, not a recovery rule.  The automatic classes below are
explicit proxies so that blank/front-matter pages are not silently counted as
business-content loss and, conversely, risky pages are not attached to a nearby
clause without evidence.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
EXTRACTED = ROOT / "data" / "extracted"
STRUCTURED = ROOT / "data" / "structured"

CLAUSE_HEAD = re.compile(r"(?:^|\n)\s*제\s*\d+\s*조(?:\s*의\s*\d+)?\s*[\(（]")
KCD = re.compile(r"(?<![A-Z0-9])[A-Z]\s*\d{2}(?:\s*[.\-~～]\s*[A-Z]?\d{1,3})?")
MONEY = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)\s*(?:원|만원|천원)")
RATE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|퍼센트)")
BUSINESS_TERMS = re.compile(
    r"자기\s*부담|공제\s*액|보상하지|지급하지|면책|보상\s*한도|한도\s*금액|"
    r"보험금|장해\s*분류|질병\s*분류|특정\s*질병|별표|붙임|별첨"
)
WS = re.compile(r"\s+")


def _covered_pages(doc: dict[str, Any]) -> set[int]:
    covered: set[int] = set()
    for key in ("clauses", "annexes"):
        for item in doc.get(key) or []:
            loc = item.get("locator") or {}
            a, b = loc.get("page_from"), loc.get("page_to")
            if isinstance(a, int) and isinstance(b, int) and a <= b:
                covered.update(range(a, b + 1))
    return covered


def classify_page(page: dict[str, Any], *, page_no: int, total_pages: int) -> dict[str, Any]:
    text = page.get("text") or ""
    compact = WS.sub("", text)
    tables = page.get("tables_coords") or []
    trusted_tables = sum(1 for table in tables if table.get("method") == "선")
    signals = {
        "clause_head": bool(CLAUSE_HEAD.search(text)),
        "business_term": bool(BUSINESS_TERMS.search(text)),
        "kcd": bool(KCD.search(text)),
        "money": bool(MONEY.search(text)),
        "rate": bool(RATE.search(text)),
        "table": bool(tables),
        "trusted_table": bool(trusted_tables),
    }
    signal_count = sum(signals.values())
    if len(compact) <= 10 and not tables:
        risk_class = "blank_or_image_only_proxy"
    elif signal_count:
        risk_class = "business_signal"
    elif len(compact) <= 80:
        risk_class = "short_text_proxy"
    else:
        risk_class = "unclassified_narrative"

    if page_no <= 5:
        position = "first5"
    elif page_no > max(5, total_pages - 5):
        position = "last5"
    else:
        position = "middle"

    return {
        "risk_class": risk_class,
        "signals": signals,
        "signal_count": signal_count,
        "text_chars": len(text),
        "compact_chars": len(compact),
        "table_count": len(tables),
        "trusted_table_count": trusted_tables,
        "position": position,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_preview": WS.sub(" ", text).strip()[:500],
    }


def _runs(pages: Iterable[int]) -> dict[int, tuple[int, int]]:
    nums = sorted(set(pages))
    out: dict[int, tuple[int, int]] = {}
    if not nums:
        return out
    start = prev = nums[0]
    for number in nums[1:] + [nums[-1] + 2]:
        if number != prev + 1:
            for page in range(start, prev + 1):
                out[page] = (start, prev)
            start = number
        prev = number
    return out


def _dump_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def audit(*, page_tag: str, clause_tag: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    counters: collections.Counter[str] = collections.Counter()
    by_insurer: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)

    clause_files = sorted(STRUCTURED.glob(f"*/{clause_tag}/*.clauses.json"))
    counters["structured_files"] = len(clause_files)
    for clause_path in clause_files:
        insurer_dir = clause_path.parents[1].name
        page_path = EXTRACTED / insurer_dir / page_tag / clause_path.name.replace(".clauses.json", ".json")
        if not page_path.exists():
            counters["missing_page_file"] += 1
            continue
        clause_doc = json.loads(clause_path.read_text(encoding="utf-8"))
        if clause_doc.get("parse_status") != "ok":
            counters[f"skipped_{clause_doc.get('parse_status') or 'unknown'}"] += 1
            continue
        page_doc = json.loads(page_path.read_text(encoding="utf-8"))
        pages = page_doc.get("pages") or []
        total_pages = len(pages)
        toc = set(clause_doc.get("toc_pages") or [])
        covered = _covered_pages(clause_doc)
        uncovered = [p.get("page") for p in pages if p.get("page") not in covered and p.get("page") not in toc]
        run_map = _runs(p for p in uncovered if isinstance(p, int))
        covered_sorted = sorted(covered)

        counters["ok_documents"] += 1
        counters["all_pages"] += total_pages
        counters["toc_pages"] += len(toc)
        counters["non_toc_pages"] += total_pages - len(toc)
        counters["covered_non_toc_pages"] += sum(1 for p in range(1, total_pages + 1) if p in covered and p not in toc)
        source = clause_doc.get("source") or page_doc.get("source") or {}
        for page in pages:
            page_no = page.get("page")
            if page_no not in run_map:
                continue
            features = classify_page(page, page_no=page_no, total_pages=total_pages)
            previous = max((p for p in covered_sorted if p < page_no), default=None)
            following = min((p for p in covered_sorted if p > page_no), default=None)
            if previous is None:
                gap_context = "before_first_covered"
            elif following is None:
                gap_context = "after_last_covered"
            else:
                gap_context = "between_covered"
            run_start, run_end = run_map[page_no]
            row = {
                "insurer_dir": insurer_dir,
                "insurer": source.get("insurer"),
                "product_name": source.get("product_name"),
                "sha12": clause_path.stem.split(".")[0],
                "source_sha256": source.get("sha256"),
                "page": page_no,
                "total_pages": total_pages,
                "gap_context": gap_context,
                "gap_start": run_start,
                "gap_end": run_end,
                "gap_length": run_end - run_start + 1,
                "previous_covered_page": previous,
                "next_covered_page": following,
                **features,
            }
            rows.append(row)
            counters["uncovered_pages"] += 1
            counters[f"risk:{features['risk_class']}"] += 1
            counters[f"context:{gap_context}"] += 1
            counters[f"position:{features['position']}"] += 1
            counters[f"risk_context:{features['risk_class']}|{gap_context}"] += 1
            for signal, present in features["signals"].items():
                if present:
                    counters[f"signal:{signal}"] += 1
            by_insurer[insurer_dir]["documents_with_uncovered"] += 0  # filled below
            by_insurer[insurer_dir]["uncovered_pages"] += 1
            by_insurer[insurer_dir][f"risk:{features['risk_class']}"] += 1

        if uncovered:
            by_insurer[insurer_dir]["documents_with_uncovered"] += 1

    rows.sort(key=lambda row: (row["insurer_dir"], row["sha12"], row["page"]))
    denominator = counters["non_toc_pages"]
    summary = {
        "schema_version": "outside-clause-pages-audit-v1",
        "page_tag": page_tag,
        "clause_tag": clause_tag,
        "classification_warning": "risk_class is a deterministic proxy, not a human truth label",
        "counts": dict(sorted(counters.items())),
        "uncovered_rate_non_toc": counters["uncovered_pages"] / denominator if denominator else None,
        "by_insurer": {key: dict(sorted(value.items())) for key, value in sorted(by_insurer.items())},
    }
    return rows, summary


def review_sample(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    priority = {
        "business_signal": 0,
        "unclassified_narrative": 1,
        "short_text_proxy": 2,
        "blank_or_image_only_proxy": 3,
    }
    # Stratify by proxy class × gap context × insurer.  A high-risk-only sample
    # cannot estimate how much of the 15.5% is normal front matter or blank pages.
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        key = (row["risk_class"], row["gap_context"], row["insurer_dir"])
        buckets[key].append(row)
    populations = {key: len(bucket) for key, bucket in buckets.items()}
    for key, bucket in buckets.items():
        bucket.sort(key=lambda row: hashlib.sha256(f"{row['sha12']}:{row['page']}".encode()).hexdigest())
    keys = sorted(buckets, key=lambda key: (priority[key[0]], key[1], key[2]))
    sample: list[dict[str, Any]] = []
    while len(sample) < limit and any(buckets.values()):
        for key in keys:
            if buckets[key] and len(sample) < limit:
                row = dict(buckets[key].pop(0))
                row.update({
                    "sampling_stratum": "|".join(key),
                    "stratum_population": populations[key],
                    "review_label": None,
                    "review_notes": "",
                })
                sample.append(row)
    return sample


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-tag", default="s5_pymupdf-1.28.0")
    parser.add_argument("--clause-tag", default="s6_pymupdf-1.28.0")
    parser.add_argument("--output", type=Path, default=ROOT / "data/eval/outside_clause_pages_s6.jsonl")
    parser.add_argument("--summary", type=Path, default=ROOT / "data/eval/outside_clause_pages_s6_summary.json")
    parser.add_argument("--review-sample", type=Path, default=ROOT / "data/eval/outside_clause_pages_s6_review200.jsonl")
    parser.add_argument("--review-limit", type=int, default=200)
    args = parser.parse_args()

    rows, summary = audit(page_tag=args.page_tag, clause_tag=args.clause_tag)
    for path in (args.output, args.summary, args.review_sample):
        path.parent.mkdir(parents=True, exist_ok=True)
    _dump_jsonl(args.output, rows)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _dump_jsonl(args.review_sample, review_sample(rows, args.review_limit))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
