"""Measure active S7.1 pgvector index search latency with approved OCR vectors."""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.adapters import pgvector_clause_index as ix
from app.adapters.pgvector_index import get_conn


ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "data" / "work" / "s7_1_approved_facts" / "vectors.npz"
OUTPUT = ROOT / "data" / "eval" / "s7_1_index_latency.json"


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1))
    return ordered[rank]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=int, default=10)
    args = parser.parse_args()
    vectors = np.load(VECTORS, allow_pickle=False)["vectors"].astype(np.float32)[: args.queries]
    latencies: list[float] = []
    hit_counts: list[int] = []
    with get_conn() as conn:
        # Warm HNSW/catalog/cache once; do not include this in the distribution.
        ix.search(conn, vectors[0], sha256s=None, limit=20, max_distance=0)
        for vector in vectors:
            start = time.perf_counter()
            hits = ix.search(conn, vector, sha256s=None, limit=20, max_distance=0)
            latencies.append((time.perf_counter() - start) * 1000)
            hit_counts.append(len(hits))
    result = {
        "schema_version": "s7.1-index-latency-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "direct policy_clause_chunk pgvector search; warm connection; top20; approved OCR vectors",
        "note": "initial 75-query run exceeded the 94-second command limit",
        "queries": len(latencies),
        "latency_ms": {
            "min": min(latencies),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": max(latencies),
            "mean": sum(latencies) / len(latencies),
        },
        "hit_count": {"min": min(hit_counts), "max": max(hit_counts)},
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
