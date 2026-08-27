"""Validate and publish the human-approved S7.1 OCR fact reranker release."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--raw-result", required=True, type=Path)
    parser.add_argument("--fact-manifest", required=True, type=Path)
    parser.add_argument("--vector-meta", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reranker-revision", required=True)
    args = parser.parse_args()

    candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
    raw = json.loads(args.raw_result.read_text(encoding="utf-8"))
    fact_manifest = json.loads(args.fact_manifest.read_text(encoding="utf-8"))
    vector_meta = json.loads(args.vector_meta.read_text(encoding="utf-8"))
    fact_dir = args.fact_manifest.parent
    chunks_path = fact_dir / "chunks.jsonl"
    occurrences_path = fact_dir / "occurrences.jsonl"
    approved_path = fact_dir / "approved_facts.jsonl"
    chunks = _jsonl(chunks_path)
    occurrences = _jsonl(occurrences_path)
    approved = _jsonl(approved_path)

    if candidates.get("schema_version") != "rerank-candidates-v2":
        raise SystemExit("unexpected candidate schema")
    if candidates.get("scope", {}).get("structured_tag") != "s7_hybrid-table-v1":
        raise SystemExit("candidate set is not S7")
    if candidates.get("candidate_facts_included") is not True:
        raise SystemExit("S7.1 candidate set does not include approved facts")
    if raw.get("reranker_model") != "Qwen/Qwen3-Reranker-4B":
        raise SystemExit("unexpected reranker model")
    if fact_manifest.get("schema_version") != "s7.1-approved-ocr-facts-v1":
        raise SystemExit("unexpected approved fact manifest")
    counts = fact_manifest.get("counts") or {}
    if counts != {
        "reviewed_patterns": 29,
        "approved_patterns": 24,
        "quarantined_patterns": 5,
        "approved_facts": 850,
        "approved_contents": 75,
        "occurrences": 850,
    }:
        raise SystemExit(f"unexpected approval counts: {counts}")
    if vector_meta.get("model") != "dragonkue/snowflake-arctic-embed-l-v2.0-ko":
        raise SystemExit("unexpected embedding model")
    if vector_meta.get("revision") != "55ec6e9358a56d56af759bc8372e970caf8c305f":
        raise SystemExit("unexpected embedding revision")
    if vector_meta.get("rows") != len(chunks) or vector_meta.get("output_sha256") != _sha(fact_dir / "vectors.npz"):
        raise SystemExit("incremental vector provenance mismatch")

    chunk_hashes = {row["content_hash"] for row in chunks}
    if len(chunk_hashes) != len(chunks) or len(approved) != 850 or len(occurrences) != 850:
        raise SystemExit("approved fact artifact cardinality mismatch")
    if {row["content_hash"] for row in approved} != chunk_hashes:
        raise SystemExit("approved facts/chunks content mismatch")
    if {row["content_hash"] for row in occurrences} != chunk_hashes:
        raise SystemExit("occurrences/chunks content mismatch")
    if any(not row.get("serving_eligible") or not row.get("citation_eligible") for row in approved):
        raise SystemExit("approved fact eligibility mismatch")
    if any(
        not row.get("citation_eligible")
        or not row.get("sha12")
        or not row.get("page_from")
        or not row.get("image_sha256")
        or not row.get("table_bbox")
        for row in occurrences
    ):
        raise SystemExit("approved occurrence locator is incomplete")

    before_rows = candidates.get("records") or []
    after_rows = raw.get("records") or []
    if len(before_rows) != len(after_rows) or not before_rows:
        raise SystemExit("query count mismatch")
    scores: list[float] = []
    supplemental_pairs = 0
    supplemental_queries: set[str] = set()
    pair_count = 0
    for before, after in zip(before_rows, after_rows, strict=True):
        if before.get("query_id") != after.get("query_id"):
            raise SystemExit("query order mismatch")
        before_hashes = {row.get("content_hash") for row in before.get("candidates") or []}
        after_candidates = after.get("candidates") or []
        after_hashes = {row.get("content_hash") for row in after_candidates}
        if before_hashes != after_hashes:
            raise SystemExit(f"candidate membership mismatch: {before.get('query_id')}")
        new_here = before_hashes & chunk_hashes
        supplemental_pairs += len(new_here)
        if new_here:
            supplemental_queries.add(before["query_id"])
        pair_count += len(after_candidates)
        for row in after_candidates:
            score = float(row["rerank_score"])
            if not math.isfinite(score):
                raise SystemExit("non-finite reranker score")
            scores.append(score)
    if supplemental_pairs <= 0 or not supplemental_queries:
        raise SystemExit("approved facts did not reach the reranker")
    if len(scores) != pair_count or statistics.pstdev(scores) < 1e-6:
        raise SystemExit("invalid/constant reranker scores")

    artifact = {
        "schema_version": "s7.1-reranker-release-v1",
        "release_state": "shadow",
        "serving_eligible": False,
        "candidate_facts_included": True,
        "approval": counts,
        "scope": candidates["scope"],
        "input": {
            "candidate_file": str(args.candidates),
            "candidate_sha256": _sha(args.candidates),
            "fact_manifest": str(args.fact_manifest),
            "fact_manifest_sha256": _sha(args.fact_manifest),
            "approved_facts_sha256": _sha(approved_path),
            "occurrences_sha256": _sha(occurrences_path),
            "incremental_chunks_sha256": _sha(chunks_path),
            "incremental_vectors_sha256": _sha(fact_dir / "vectors.npz"),
        },
        "models": {
            "retriever": vector_meta["model"],
            "retriever_revision": vector_meta["revision"],
            "reranker": raw["reranker_model"],
            "reranker_revision": args.reranker_revision,
        },
        "validation": {
            "queries": len(after_rows),
            "pairs": pair_count,
            "scores_finite": True,
            "candidate_membership_equal": True,
            "approved_fact_pairs": supplemental_pairs,
            "queries_with_approved_facts": len(supplemental_queries),
            "approved_occurrence_locators_complete": True,
            "score_min": min(scores),
            "score_max": max(scores),
            "score_std": statistics.pstdev(scores),
        },
        "metrics": raw.get("metrics"),
        "provenance": {
            **(raw.get("provenance") or {}),
            "raw_result": str(args.raw_result),
            "raw_result_sha256": _sha(args.raw_result),
        },
        "records": after_rows,
    }
    canonical = json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    artifact["payload_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(args.output)
    print(json.dumps({"output": str(args.output), "payload_sha256": artifact["payload_sha256"],
                      "validation": artifact["validation"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
