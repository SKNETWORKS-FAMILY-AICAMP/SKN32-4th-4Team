"""Materialize human-approved S7 OCR fact signatures for S7.1 retrieval.

Only rows behind an explicit ``approve`` signature label are emitted.  Labels
such as ``fix``, ``reject`` and ``unsure`` remain quarantined.  Repeated facts
share one content row while retaining every source occurrence for document
scoping and citation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from scripts.eval.build_s7_fact_review import ROOT, _eligible, _signature


SCHEMA_VERSION = "s7.1-approved-ocr-facts-v1"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _signature_id(row: dict) -> str:
    payload = json.dumps(_signature(row), ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fact_text(row: dict) -> str:
    lines = [
        "[검수 승인 자기부담금 표 사실]",
        f"가입유형: {row['plan']}",
        f"의료서비스: {', '.join(row['service'])}",
        f"의료기관: {row['institution']}",
    ]
    if row.get("coverage"):
        lines.append(f"급여구분: {', '.join(row['coverage'])}")
    lines.append(f"자기부담금: {row['amount_formula']}")
    return "\n".join(lines)


def _load_labels(path: Path) -> dict[str, dict]:
    labels: dict[str, dict] = {}
    allowed = {"approve", "fix", "reject", "unsure"}
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        signature_id = row.get("signature_id")
        label = row.get("label")
        if not isinstance(signature_id, str) or not signature_id.startswith("sha256:"):
            raise SystemExit(f"invalid signature_id at label line {line_number}")
        if label not in allowed:
            raise SystemExit(f"invalid/empty label at line {line_number}: {label!r}")
        if signature_id in labels:
            raise SystemExit(f"duplicate signature label: {signature_id}")
        labels[signature_id] = row
    return labels


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--labels",
        type=Path,
        default=ROOT / "data/eval/s7_fact_signature_labels_20260804.jsonl",
    )
    parser.add_argument(
        "--candidate-dir",
        type=Path,
        default=ROOT / "data/candidates/s7_selfpay",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data/work/s7_1_approved_facts",
    )
    args = parser.parse_args()

    labels = _load_labels(args.labels)
    if not labels:
        raise SystemExit("no fact signature labels")

    source_files = sorted(args.candidate_dir.glob("*.json"))
    if not source_files:
        raise SystemExit(f"no candidate files: {args.candidate_dir}")

    candidates_by_signature: dict[str, list[dict]] = defaultdict(list)
    all_candidate_ids: set[str] = set()
    for path in source_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("candidates") or []:
            if not _eligible(row):
                continue
            candidate_id = row.get("candidate_id")
            if not candidate_id or candidate_id in all_candidate_ids:
                raise SystemExit(f"missing/duplicate candidate_id: {candidate_id!r}")
            all_candidate_ids.add(candidate_id)
            candidates_by_signature[_signature_id(row)].append(row)

    unknown_labels = sorted(set(labels) - set(candidates_by_signature))
    if unknown_labels:
        raise SystemExit(f"labels do not resolve to current candidates: {unknown_labels[:3]}")

    approved_facts: list[dict] = []
    occurrences: list[dict] = []
    content_by_hash: dict[str, dict] = {}
    label_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"patterns": 0, "facts": 0})
    for signature_id, label_row in sorted(labels.items()):
        rows = candidates_by_signature[signature_id]
        declared_ids = set(label_row.get("candidate_ids") or [])
        actual_ids = {row["candidate_id"] for row in rows}
        if declared_ids != actual_ids:
            raise SystemExit(
                f"signature membership changed: {signature_id} "
                f"declared={len(declared_ids)} current={len(actual_ids)}"
            )
        label = label_row["label"]
        label_counts[label]["patterns"] += 1
        label_counts[label]["facts"] += len(rows)
        if label != "approve":
            continue
        for row in sorted(rows, key=lambda x: (x["document_sha12"], x["page_1based"], x["candidate_id"])):
            text = _fact_text(row)
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            content_by_hash.setdefault(
                content_hash,
                {
                    "content_hash": content_hash,
                    "seq": 0,
                    "n_chunks": 1,
                    "text": text,
                    "chunk_type": "approved_ocr_fact",
                },
            )
            approved = {
                **row,
                "signature_id": signature_id,
                "content_hash": content_hash,
                "approval": "human_pattern_approved",
                "serving_eligible": True,
                "citation_eligible": True,
                "review": {
                    "label_file": args.labels.name,
                    "label_file_sha256": _sha(args.labels),
                    "label": label,
                    "note": label_row.get("note") or "",
                },
            }
            approved_facts.append(approved)
            occurrences.append(
                {
                    "content_hash": content_hash,
                    "candidate_id": row["candidate_id"],
                    "signature_id": signature_id,
                    "sha12": row["document_sha12"],
                    "insurer": row["insurer"],
                    "page_from": row["page_1based"],
                    "page_to": row["page_1based"],
                    "source_kind": "approved_ocr_table_fact",
                    "citation_eligible": True,
                    "table_bbox": (row.get("source") or {}).get("table_bbox"),
                    "image_sha256": (row.get("source") or {}).get("image_sha256"),
                }
            )

    chunks = sorted(content_by_hash.values(), key=lambda row: row["content_hash"])
    for global_index, chunk in enumerate(chunks):
        chunk["global_index"] = global_index
    approved_facts.sort(key=lambda row: row["candidate_id"])
    occurrences.sort(key=lambda row: (row["sha12"], row["page_from"], row["candidate_id"]))
    if len(approved_facts) != len(occurrences):
        raise SystemExit("approved fact/occurrence cardinality mismatch")
    if not chunks or not approved_facts:
        raise SystemExit("approval produced no retrievable content")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "approved_facts": args.output_dir / "approved_facts.jsonl",
        "chunks": args.output_dir / "chunks.jsonl",
        "occurrences": args.output_dir / "occurrences.jsonl",
    }
    _write_jsonl(paths["approved_facts"], approved_facts)
    _write_jsonl(paths["chunks"], chunks)
    _write_jsonl(paths["occurrences"], occurrences)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "release_state": "shadow",
        "serving_eligible": False,
        "candidate_facts_included": True,
        "labels": {
            "path": str(args.labels),
            "sha256": _sha(args.labels),
            "counts": dict(label_counts),
        },
        "counts": {
            "reviewed_patterns": len(labels),
            "approved_patterns": label_counts["approve"]["patterns"],
            "quarantined_patterns": len(labels) - label_counts["approve"]["patterns"],
            "approved_facts": len(approved_facts),
            "approved_contents": len(chunks),
            "occurrences": len(occurrences),
        },
        "artifacts": {
            name: {"path": str(path), "sha256": _sha(path)} for name, path in paths.items()
        },
    }
    manifest_path = args.output_dir / "manifest.json"
    temp = manifest_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(manifest_path)
    print(json.dumps({"manifest": str(manifest_path), **manifest["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
