"""Audit page-boundary coverage-limit records by insurer.

This is a read-only audit.  It runs the production adjacent-page entrypoint and
keeps the unresolved rows as evidence instead of silently dropping them.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import glob
import hashlib
import json
import os
import platform
import time
from pathlib import Path

from scripts.extract.coverage_limits import parse_page, parse_page_with_previous


def _value_kind(record: dict) -> list[str]:
    kinds: list[str] = []
    if record.get("공제액") is not None:
        kinds.append("amount")
    if record.get("자기부담률") is not None:
        kinds.append("rate")
    if record.get("자기부담률_급여") is not None:
        kinds.append("split_rate")
    if "한도" in (record.get("금액_원문") or ""):
        kinds.append("limit_token")
    return kinds or ["no_value"]


def _audit_file(file_name: str) -> tuple[str, dict, list[dict], list[dict]]:
    """Audit one document; kept top-level so process pools can pickle it."""
    path = Path(file_name)
    insurer = path.parents[1].name
    doc = json.loads(path.read_text(encoding="utf-8"))
    counts = collections.Counter(documents=1)
    unresolved: list[dict] = []
    recovered: list[dict] = []
    pages = doc.get("pages") or []
    for page_index, page in enumerate(pages):
        text = page.get("text") or ""
        if "상급종합병원" not in text or ("공제" not in text and "자기부담" not in text):
            continue
        counts["candidate_pages"] += 1
        previous = pages[page_index - 1] if page_index else None
        baseline = parse_page(page)
        production = parse_page_with_previous(previous, page)
        counts["records"] += len(production)

        baseline_suspects = [r for r in baseline if r.get("쪽경계_절단의심")]
        production_recovered = [r for r in production if r.get("쪽경계_복구")]
        production_unresolved = [r for r in production if r.get("쪽경계_절단의심")]
        counts["suspected_before"] += len(baseline_suspects)
        counts["recovered"] += len(production_recovered)
        counts["unresolved"] += len(production_unresolved)
        for row in production:
            counts[f"status_{row.get('parse_status')}"] += 1
            for kind in _value_kind(row):
                counts[f"value_{kind}"] += 1

        for row in production_recovered:
            recovered.append({
                "insurer": insurer,
                "sha12": path.stem,
                "page": page.get("page"),
                "value_kind": _value_kind(row),
                "recovery": row.get("쪽경계_복구"),
                "locator": row.get("근거_locator"),
            })
        for row in production_unresolved:
            unresolved.append({
                "insurer": insurer,
                "sha12": path.stem,
                "page": page.get("page"),
                "value_kind": _value_kind(row),
                "amount_raw": row.get("금액_원문"),
                "institution_raw": row.get("기관종별_원문"),
                "reasons": row.get("미파싱_사유"),
                "locator": row.get("근거_locator"),
            })
    return insurer, dict(counts), recovered, unresolved


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/extracted")
    ap.add_argument("--schema", default="s5_pymupdf-1.28.0")
    ap.add_argument("--insurer", action="append", default=[])
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    started = time.perf_counter()
    insurers = args.insurer or ["*"]
    files: list[str] = []
    for insurer in insurers:
        files.extend(glob.glob(os.path.join(args.root, insurer, args.schema, "*.json")))
    files = sorted(set(files))

    totals = collections.Counter()
    per_insurer: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    unresolved: list[dict] = []
    recovered: list[dict] = []

    workers = max(1, min(args.workers, len(files) or 1))
    if workers == 1:
        results = map(_audit_file, files)
    else:
        executor = concurrent.futures.ProcessPoolExecutor(max_workers=workers)
        results = executor.map(_audit_file, files, chunksize=1)
    try:
        for insurer, counts, file_recovered, file_unresolved in results:
            totals.update(counts)
            per_insurer[insurer].update(counts)
            recovered.extend(file_recovered)
            unresolved.extend(file_unresolved)
    finally:
        if workers != 1:
            executor.shutdown()

    payload = {
        "schema_version": "coverage-boundary-audit-v1",
        "source": {"root": args.root, "schema": args.schema, "insurers": insurers},
        "provenance": {
            "host": platform.node(),
            "python": platform.python_version(),
            "workers": workers,
            "seconds": round(time.perf_counter() - started, 3),
        },
        "totals": dict(sorted(totals.items())),
        "per_insurer": {k: dict(sorted(v.items())) for k, v in sorted(per_insurer.items())},
        "recovered_rows": recovered,
        "unresolved_rows": unresolved,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["payload_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "totals": payload["totals"],
                      "payload_sha256": payload["payload_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
