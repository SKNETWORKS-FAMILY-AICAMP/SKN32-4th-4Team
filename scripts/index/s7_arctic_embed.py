"""Prepare, run, and merge revision-pinned Arctic-ko S7 shadow embeddings.

The approved S7 candidate facts are deliberately excluded.  Only
``has_eligible=true`` clause/annex contents from the S7 shadow export may be
represented by the reused deterministic chunk input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any


MODEL = "dragonkue/snowflake-arctic-embed-l-v2.0-ko"
REVISION = "55ec6e9358a56d56af759bc8372e970caf8c305f"
DIM = 1024
MAX_SEQ_LENGTH = 8192
CHUNK_BUDGET = 448
OVERLAP = 80


class EmbedError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    body = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _eligible_hashes(path: Path) -> set[str]:
    result: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("has_eligible"):
                result.add(str(row["content_hash"]))
    return result


def prepare(*, clauses: Path, chunks: Path, output_dir: Path, shards: int) -> dict[str, Any]:
    if shards < 1:
        raise EmbedError("shards must be positive")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise EmbedError(f"refusing non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    eligible = _eligible_hashes(clauses)
    handles = [
        (output_dir / f"shard-{index:02d}.jsonl").open("w", encoding="utf-8", newline="\n")
        for index in range(shards)
    ]
    counts = [0] * shards
    chunk_hashes: set[str] = set()
    keys: set[tuple[str, int]] = set()
    total = 0
    try:
        with chunks.open(encoding="utf-8") as source:
            for global_index, line in enumerate(source):
                row = json.loads(line)
                content_hash = str(row["content_hash"])
                seq = int(row["seq"])
                key = (content_hash, seq)
                if key in keys:
                    raise EmbedError(f"duplicate chunk key: {key}")
                keys.add(key)
                chunk_hashes.add(content_hash)
                shard = global_index % shards
                handles[shard].write(
                    json.dumps(
                        {
                            "global_index": global_index,
                            "content_hash": content_hash,
                            "seq": seq,
                            "n_chunks": int(row["n_chunks"]),
                            "text": str(row["text"]),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                counts[shard] += 1
                total += 1
    finally:
        for handle in handles:
            handle.close()
    if chunk_hashes != eligible:
        raise EmbedError(
            f"eligible/chunk content hash sets differ: "
            f"eligible={len(eligible)} chunks={len(chunk_hashes)} "
            f"missing={len(eligible - chunk_hashes)} extra={len(chunk_hashes - eligible)}"
        )
    manifest = {
        "schema_version": "s7-arctic-ko-shards-v1",
        "model": MODEL,
        "revision": REVISION,
        "dim": DIM,
        "normalized": True,
        "doc_prefix": "",
        "query_prefix": "query: ",
        "max_seq_length": MAX_SEQ_LENGTH,
        "chunk_budget": CHUNK_BUDGET,
        "overlap": OVERLAP,
        "eligible_contents": len(eligible),
        "chunks": total,
        "shards": shards,
        "shard_counts": counts,
        "clauses_sha256": sha256_file(clauses),
        "chunks_sha256": sha256_file(chunks),
        "candidate_facts_included": False,
    }
    atomic_json(output_dir / "manifest.json", manifest)
    return manifest


def run_shard(*, shard: Path, output: Path, metadata: Path, batch_size: int, cache_dir: Path) -> dict[str, Any]:
    if output.exists() or metadata.exists():
        raise EmbedError(f"refusing existing shard output: {output} / {metadata}")
    import numpy as np
    import torch
    import transformers
    from sentence_transformers import SentenceTransformer, __version__ as st_version

    if not torch.cuda.is_available():
        raise EmbedError("CUDA is required; refusing CPU fallback")
    rows = [json.loads(line) for line in shard.open(encoding="utf-8")]
    indices = np.asarray([int(row["global_index"]) for row in rows], dtype=np.int64)
    if len(set(indices.tolist())) != len(indices):
        raise EmbedError("duplicate global indices in shard")
    texts = [str(row["text"]) for row in rows]
    started = time.time()
    torch.cuda.reset_peak_memory_stats()
    model = SentenceTransformer(
        MODEL,
        revision=REVISION,
        device="cuda",
        cache_folder=str(cache_dir),
        model_kwargs={"dtype": torch.float16},
    )
    model.max_seq_length = MAX_SEQ_LENGTH
    vectors32 = model.encode(
        texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    if vectors32.shape != (len(rows), DIM):
        raise EmbedError(f"unexpected vector shape: {vectors32.shape}")
    if not np.isfinite(vectors32).all():
        raise EmbedError("non-finite embedding values")
    norms = np.linalg.norm(vectors32, axis=1)
    if len(norms) and (float(norms.min()) < 0.999 or float(norms.max()) > 1.001):
        raise EmbedError(f"normalization gate failed: {norms.min()}..{norms.max()}")
    vectors = vectors32.astype(np.float16)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        output,
        vectors=vectors,
        global_index=indices,
        content_hash=np.asarray([row["content_hash"] for row in rows], dtype="U64"),
        seq=np.asarray([int(row["seq"]) for row in rows], dtype=np.int32),
        n_chunks=np.asarray([int(row["n_chunks"]) for row in rows], dtype=np.int32),
    )
    result = {
        "schema_version": "s7-arctic-ko-shard-result-v1",
        "model": MODEL,
        "revision": REVISION,
        "dim": DIM,
        "normalized": True,
        "dtype": "float16",
        "rows": len(rows),
        "batch_size": batch_size,
        "input": shard.name,
        "input_sha256": sha256_file(shard),
        "output": output.name,
        "output_sha256": sha256_file(output),
        "seconds": round(time.time() - started, 3),
        "gpu": torch.cuda.get_device_name(0),
        "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 2**20, 1),
        "packages": {
            "sentence_transformers": st_version,
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "numpy": np.__version__,
        },
        "norm_float32_min": float(norms.min()) if len(norms) else None,
        "norm_float32_max": float(norms.max()) if len(norms) else None,
    }
    atomic_json(metadata, result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def merge(*, manifest_path: Path, shards_dir: Path, source_chunks: Path, old_vectors: Path, output_dir: Path) -> dict[str, Any]:
    import numpy as np

    if output_dir.exists() and any(output_dir.iterdir()):
        raise EmbedError(f"refusing non-empty merge output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    total = int(manifest["chunks"])
    vectors = np.empty((total, DIM), dtype=np.float16)
    hashes = np.empty(total, dtype="U64")
    seqs = np.empty(total, dtype=np.int32)
    nchunks = np.empty(total, dtype=np.int32)
    seen = np.zeros(total, dtype=np.bool_)
    shard_meta = []
    for index in range(int(manifest["shards"])):
        npz_path = shards_dir / f"shard-{index:02d}.npz"
        meta_path = shards_dir / f"shard-{index:02d}.meta.json"
        if not npz_path.is_file() or not meta_path.is_file():
            raise EmbedError(f"missing shard result: {npz_path} / {meta_path}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("revision") != REVISION or meta.get("model") != MODEL:
            raise EmbedError(f"model provenance mismatch: {meta_path}")
        if sha256_file(npz_path) != meta.get("output_sha256"):
            raise EmbedError(f"shard output SHA mismatch: {npz_path}")
        with np.load(npz_path, allow_pickle=False) as data:
            ids = data["global_index"].astype(np.int64)
            if np.any(ids < 0) or np.any(ids >= total) or np.any(seen[ids]):
                raise EmbedError(f"invalid or repeated global indices: {npz_path}")
            seen[ids] = True
            vectors[ids] = data["vectors"]
            hashes[ids] = data["content_hash"]
            seqs[ids] = data["seq"]
            nchunks[ids] = data["n_chunks"]
        shard_meta.append(meta)
    if not seen.all():
        raise EmbedError(f"missing merged rows: {int((~seen).sum())}")

    with np.load(old_vectors, allow_pickle=False) as old:
        for name, values in (("content_hash", hashes), ("seq", seqs), ("n_chunks", nchunks)):
            if not np.array_equal(old[name], values):
                raise EmbedError(f"old/new key order mismatch: {name}")
        old32 = old["vectors"].astype(np.float32)
        new32 = vectors.astype(np.float32)
        old_norm = np.linalg.norm(old32, axis=1)
        new_norm = np.linalg.norm(new32, axis=1)
        cosine = np.sum(old32 * new32, axis=1) / np.maximum(old_norm * new_norm, 1e-12)
        comparison = {
            "old_vectors_sha256": sha256_file(old_vectors),
            "cosine_mean": float(cosine.mean()),
            "cosine_p01": float(np.quantile(cosine, 0.01)),
            "cosine_min": float(cosine.min()),
            "exact_float16_rows": int(np.all(old["vectors"] == vectors, axis=1).sum()),
        }

    vectors_path = output_dir / "vectors.npz"
    np.savez(
        vectors_path,
        vectors=vectors,
        content_hash=hashes,
        seq=seqs,
        n_chunks=nchunks,
    )
    chunks_path = output_dir / "chunks.jsonl"
    try:
        os.link(source_chunks.resolve(), chunks_path)
        chunk_materialization = "hardlink"
    except OSError:
        shutil.copy2(source_chunks, chunks_path)
        chunk_materialization = "copy"
    result = {
        **manifest,
        "schema_version": "s7-arctic-ko-merged-v1",
        "dtype": "float16",
        "rows": total,
        "vectors_sha256": sha256_file(vectors_path),
        "chunks_output_sha256": sha256_file(chunks_path),
        "chunk_materialization": chunk_materialization,
        "shard_results": shard_meta,
        "old_vector_comparison": comparison,
        "serving_eligible": False,
        "release_state": "shadow",
    }
    atomic_json(output_dir / "meta.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--clauses", required=True, type=Path)
    prep.add_argument("--chunks", required=True, type=Path)
    prep.add_argument("--output-dir", required=True, type=Path)
    prep.add_argument("--shards", required=True, type=int)
    run = sub.add_parser("run")
    run.add_argument("--shard", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--metadata", required=True, type=Path)
    run.add_argument("--batch-size", type=int, default=64)
    run.add_argument("--cache-dir", required=True, type=Path)
    merge_parser = sub.add_parser("merge")
    merge_parser.add_argument("--manifest", required=True, type=Path)
    merge_parser.add_argument("--shards-dir", required=True, type=Path)
    merge_parser.add_argument("--source-chunks", required=True, type=Path)
    merge_parser.add_argument("--old-vectors", required=True, type=Path)
    merge_parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(clauses=args.clauses, chunks=args.chunks, output_dir=args.output_dir, shards=args.shards)
    elif args.command == "run":
        result = run_shard(
            shard=args.shard,
            output=args.output,
            metadata=args.metadata,
            batch_size=args.batch_size,
            cache_dir=args.cache_dir,
        )
    else:
        result = merge(
            manifest_path=args.manifest,
            shards_dir=args.shards_dir,
            source_chunks=args.source_chunks,
            old_vectors=args.old_vectors,
            output_dir=args.output_dir,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
