"""Encode a fixed rerank query list on a remote GPU for candidate generation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def query_digest(queries: list[str]) -> str:
    payload = json.dumps(queries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--prefix", default="query: ")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    queries = [row["query"] for row in source["records"]]

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(args.model, revision=args.revision, device="cuda")
    vectors = model.encode(
        [args.prefix + query for query in queries],
        batch_size=args.batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=True,
    ).astype(np.float32, copy=False)
    np.savez_compressed(
        args.output,
        vectors=vectors,
        query_sha256=np.asarray(query_digest(queries)),
        model=np.asarray(args.model),
        revision=np.asarray(args.revision),
        prefix=np.asarray(args.prefix),
    )
    print(json.dumps({"queries": len(queries), "shape": list(vectors.shape), "query_sha256": query_digest(queries)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
