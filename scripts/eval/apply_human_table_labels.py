"""Merge the self-contained S7 table review result into a page label set."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LABEL_MAP = {"table": "true", "broken": "true", "prose": "false", "unsure": "check"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=ROOT / "data/eval/table_labels.jsonl")
    parser.add_argument(
        "--review", type=Path, default=ROOT / "data/eval/human_table_labels_20260804.jsonl"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/eval/table_labels_s7_human_20260804.jsonl",
    )
    args = parser.parse_args()

    base_rows = [row for row in _rows(args.base) if not row.get("_meta")]
    review_rows = _rows(args.review)
    if len(review_rows) != 68:
        raise SystemExit(f"expected 68 review rows, got {len(review_rows)}")
    if any(row.get("label") not in LABEL_MAP for row in review_rows):
        raise SystemExit("review contains an empty or unknown label")
    if len({row.get("id") for row in review_rows}) != len(review_rows):
        raise SystemExit("duplicate review ids")

    merged: dict[tuple[str, int], dict] = {
        (row["sha12"], int(row["page"])): row for row in base_rows
    }
    reviews_by_page: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in review_rows:
        reviews_by_page[(row["sha12"], int(row["page"]))].append(row)
    overrides = 0
    for key, page_reviews in sorted(reviews_by_page.items()):
        labels = {row["label"] for row in page_reviews}
        mapped = "true" if labels & {"table", "broken"} else "check" if "unsure" in labels else "false"
        prior = merged.get(key)
        queues = {row.get("queue") for row in page_reviews}
        if "B6-check3" in queues and (not prior or prior.get("label") != "check"):
            raise SystemExit(f"B6 row does not resolve a check label: {key}")
        if prior and prior.get("label") != mapped:
            overrides += 1
        merged[key] = {
            "label": mapped,
            "sha12": key[0],
            "page": key[1],
            "why": "; ".join(filter(None, (row.get("note") for row in page_reviews))) or (
                "사람 검수: 표 구조 존재, 현재 추출 깨짐"
                if "broken" in labels
                else "사람 검수: 본문 오탐"
                if labels == {"prose"}
                else "사람 검수"
            ),
            "review_label": ",".join(sorted(labels)),
            "review_queue": ",".join(sorted(str(x) for x in queues)),
            "review_ids": sorted(row["id"] for row in page_reviews),
            "prior_label": prior.get("label") if prior else None,
            "labeled_by": "사용자 원문 육안 검수",
            "source": args.review.name,
        }

    ordered = sorted(merged.values(), key=lambda row: (row["sha12"], int(row["page"])))
    counts = Counter(row["label"] for row in ordered)
    queue_counts = Counter((row.get("review_queue") or "base", row.get("review_label") or row["label"])
                           for row in ordered)
    meta = {
        "_meta": True,
        "schema_version": "s7-human-table-labels-v1",
        "base": {"path": str(args.base), "sha256": _sha(args.base)},
        "review": {"path": str(args.review), "sha256": _sha(args.review), "rows": len(review_rows)},
        "review_pages": len(reviews_by_page),
        "prior_label_overrides": overrides,
        "counts": dict(counts),
        "review_counts": {f"{queue}:{label}": count for (queue, label), count in queue_counts.items()
                          if queue != "base"},
        "label_contract": {
            "table": "true",
            "broken": "true (table exists, extraction repair required)",
            "prose": "false",
            "unsure": "check",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n")
        for row in ordered:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temp.replace(args.output)
    print(json.dumps({"output": str(args.output), "rows": len(ordered), "counts": dict(counts)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
