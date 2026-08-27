"""Validate a raw reranker run and publish an explicitly S7-scoped artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--raw-result", required=True)
    ap.add_argument("--embedding-meta", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--reranker-revision", required=True)
    args = ap.parse_args()

    candidate_path = Path(args.candidates)
    raw_path = Path(args.raw_result)
    meta_path = Path(args.embedding_meta)
    candidates = json.loads(candidate_path.read_text(encoding="utf-8"))
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    if candidates.get("schema_version") != "rerank-candidates-v2":
        raise SystemExit("unexpected candidate schema")
    if candidates.get("scope", {}).get("structured_tag") != "s7_hybrid-table-v1":
        raise SystemExit("candidate set is not S7")
    if raw.get("reranker_model") != "Qwen/Qwen3-Reranker-4B":
        raise SystemExit("unexpected reranker model")
    if meta.get("candidate_facts_included") is not False:
        raise SystemExit("candidate facts must be excluded from the release input")
    if meta.get("release_state") != "shadow" or meta.get("serving_eligible") is not False:
        raise SystemExit("S7 embedding input must remain shadow/non-serving")
    if raw.get("retriever_model") != meta.get("model"):
        raise SystemExit("retriever model mismatch")
    candidate_records = candidates.get("records") or []
    raw_records = raw.get("records") or []
    if len(candidate_records) != len(raw_records):
        raise SystemExit("query count mismatch")

    scores: list[float] = []
    pair_count = 0
    for before, after in zip(candidate_records, raw_records):
        if before.get("query_id") != after.get("query_id"):
            raise SystemExit("query order mismatch")
        before_hashes = {x.get("content_hash") for x in before.get("candidates") or []}
        after_rows = after.get("candidates") or []
        after_hashes = {x.get("content_hash") for x in after_rows}
        if before_hashes != after_hashes:
            raise SystemExit(f"candidate membership mismatch: {before.get('query_id')}")
        pair_count += len(after_rows)
        for row in after_rows:
            score = float(row["rerank_score"])
            if not math.isfinite(score):
                raise SystemExit("non-finite reranker score")
            scores.append(score)
    if pair_count != 8273 or len(scores) != pair_count:
        raise SystemExit(f"unexpected pair count: {pair_count}")
    score_std = statistics.pstdev(scores)
    if score_std < 1e-6:
        raise SystemExit("constant/near-constant reranker output")

    artifact = {
        "schema_version": "s7-reranker-release-v1",
        "release_state": "shadow",
        "serving_eligible": False,
        "candidate_facts_included": False,
        "scope": candidates["scope"],
        "input": {
            "candidate_file": str(candidate_path),
            "candidate_sha256": _sha(candidate_path),
            "embedding_meta": str(meta_path),
            "embedding_meta_sha256": _sha(meta_path),
            "chunks_sha256": meta.get("chunks_sha256"),
            "vectors_sha256": meta.get("vectors_sha256"),
        },
        "models": {
            "retriever": meta.get("model"),
            "retriever_revision": meta.get("revision"),
            "reranker": raw.get("reranker_model"),
            "reranker_revision": args.reranker_revision,
        },
        "validation": {
            "queries": len(raw_records),
            "pairs": pair_count,
            "scores_finite": True,
            "candidate_membership_equal": True,
            "score_min": min(scores),
            "score_max": max(scores),
            "score_std": score_std,
        },
        "metrics": raw.get("metrics"),
        "provenance": {
            **(raw.get("provenance") or {}),
            "raw_result": str(raw_path),
            "raw_result_sha256": _sha(raw_path),
        },
        "records": raw_records,
    }
    canonical = json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    artifact["payload_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "payload_sha256": artifact["payload_sha256"],
                      "validation": artifact["validation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
