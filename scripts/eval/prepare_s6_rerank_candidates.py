"""Build one fixed Arctic-ko top-k candidate set for reranker evaluation.

The S6 delivery contains document chunk vectors but not query vectors.  This
script embeds the existing, source-derived evaluation queries with the exact
model/prefix from the delivery metadata, retrieves top chunks, deduplicates by
clause content hash, and writes a compact artifact that every reranker shares.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import platform
import time
from collections import defaultdict

import numpy as np


ROOT = pathlib.Path(__file__).resolve().parents[2]


def _args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--delivery-dir",
        type=pathlib.Path,
        default=ROOT / "data" / "work" / "s6_chunks_delivery",
    )
    ap.add_argument("--meta-path", type=pathlib.Path)
    ap.add_argument("--vectors-path", type=pathlib.Path)
    ap.add_argument("--chunks-path", type=pathlib.Path)
    ap.add_argument("--query-vectors", type=pathlib.Path)
    ap.add_argument("--supplemental-vectors", type=pathlib.Path)
    ap.add_argument("--supplemental-chunks", type=pathlib.Path)
    ap.add_argument("--supplemental-occurrences", type=pathlib.Path)
    ap.add_argument(
        "--eval-set",
        type=pathlib.Path,
        default=ROOT / "data" / "eval" / "embed_bench.json",
    )
    ap.add_argument(
        "--retrieval-probes",
        type=pathlib.Path,
        default=ROOT / "data" / "eval" / "retrieval_probes.json",
    )
    ap.add_argument(
        "--output",
        type=pathlib.Path,
        default=ROOT / "data" / "eval" / "s6_arctic_ko_top20_rerank.json",
    )
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--raw-k", type=int, default=400)
    ap.add_argument("--query-batch", type=int, default=16)
    ap.add_argument("--encode-batch", type=int, default=32)
    ap.add_argument("--device", default="cpu")
    ap.add_argument(
        "--structured-tag",
        default="s6_pymupdf-1.28.0",
        help="tag used to restore content_hash -> document scope",
    )
    return ap.parse_args()


def _rank(golds: set[str], candidates: list[dict]) -> int | None:
    for pos, cand in enumerate(candidates, 1):
        if cand["content_hash"][:16] in golds:
            return pos
    return None


def _metrics(records: list[dict]) -> dict:
    ranks = [r["dense_gold_rank"] for r in records]
    n = len(ranks)
    if not n:
        return {"n": 0, "hit@1": 0.0, "hit@5": 0.0, "hit@10": 0.0,
                "hit@20": 0.0, "mrr@10": 0.0}
    return {
        "n": n,
        "hit@1": round(sum(x == 1 for x in ranks) / n, 6),
        "hit@5": round(sum(x is not None and x <= 5 for x in ranks) / n, 6),
        "hit@10": round(sum(x is not None and x <= 10 for x in ranks) / n, 6),
        "hit@20": round(sum(x is not None and x <= 20 for x in ranks) / n, 6),
        "mrr@10": round(sum(1 / x for x in ranks if x is not None and x <= 10) / n, 6),
    }


def main() -> int:
    args = _args()
    started = time.time()
    meta_path = args.meta_path or args.delivery_dir / "s6_chunks_arctic-ko.meta.json"
    npz_path = args.vectors_path or args.delivery_dir / "s6_chunks_arctic-ko_vectors.npz"
    jsonl_path = args.chunks_path or args.delivery_dir / "s6_chunks_arctic-ko.jsonl"
    for path in (meta_path, npz_path, jsonl_path, args.eval_set, args.retrieval_probes):
        if not path.is_file():
            raise SystemExit(f"required input missing: {path}")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    bench = json.loads(args.eval_set.read_text(encoding="utf-8"))
    retrieval_probes = json.loads(args.retrieval_probes.read_text(encoding="utf-8"))
    bench_scope = {
        row["id"]: row.get("sha12") or ""
        for row in (bench.get("corpus") or [])
    }
    query_rows: list[dict] = []
    for task, rows in (("title", bench.get("queries") or []),
                       ("tail", bench.get("proviso_queries") or [])):
        for ordinal, row in enumerate(rows):
            query_rows.append({
                "query_id": f"{task}-{ordinal:04d}",
                "task": task,
                "query": row["query"],
                "gold_ids": sorted(set(row.get("gold_ids") or [row["gold_id"]])),
                "is_exclusion": bool(row.get("is_exclusion", False)),
                "scope_sha12": bench_scope.get(row["gold_id"], ""),
            })
    for ordinal, row in enumerate(retrieval_probes.get("exclusion_queries") or []):
        kind = "exclusion_own" if row.get("kind") == "동일표현" else "exclusion_alt"
        golds = row.get("gold_eligible_ids") or row.get("gold_ids") or []
        query_rows.append({
            "query_id": f"{kind}-{ordinal:04d}",
            "task": kind,
            "query": row["query"],
            "gold_ids": sorted({g[:16] for g in golds}),
            "is_exclusion": True,
            "scope_sha12": row.get("sha12") or "",
        })

    model_id = meta["model"]
    prefix = meta.get("query_prefix") or ""
    query_texts = [row["query"] for row in query_rows]
    query_sha256 = hashlib.sha256(
        json.dumps(query_texts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if args.query_vectors:
        with np.load(args.query_vectors) as packed_queries:
            qvec = packed_queries["vectors"].astype(np.float32)
            packed_sha = str(packed_queries["query_sha256"])
            packed_model = str(packed_queries["model"])
            packed_revision = str(packed_queries["revision"])
            packed_prefix = str(packed_queries["prefix"])
        if packed_sha != query_sha256:
            raise SystemExit("query-vector input query digest mismatch")
        if packed_model != model_id or packed_revision != (meta.get("revision") or "") or packed_prefix != prefix:
            raise SystemExit("query-vector model provenance mismatch")
    else:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(model_id, revision=meta.get("revision"), device=args.device)
        qvec = model.encode(
            [prefix + query for query in query_texts],
            batch_size=args.encode_batch,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=True,
        ).astype(np.float32, copy=False)
    if qvec.shape != (len(query_rows), int(meta["dim"])):
        raise SystemExit(f"query-vector shape mismatch: {qvec.shape}")

    with np.load(npz_path) as packed:
        vectors = packed["vectors"].astype(np.float32)
        hashes = packed["content_hash"].astype(str)
        seqs = packed["seq"].astype(np.int32)
        n_chunks = packed["n_chunks"].astype(np.int32)
    if vectors.shape != (len(hashes), int(meta["dim"])):
        raise SystemExit(f"vector metadata mismatch: {vectors.shape} / {len(hashes)} / {meta['dim']}")
    base_rows = len(hashes)
    supplemental_enabled = any((args.supplemental_vectors, args.supplemental_chunks,
                                args.supplemental_occurrences))
    if supplemental_enabled and not all((args.supplemental_vectors, args.supplemental_chunks,
                                         args.supplemental_occurrences)):
        raise SystemExit("all supplemental inputs must be provided together")
    if supplemental_enabled:
        for path in (args.supplemental_vectors, args.supplemental_chunks,
                     args.supplemental_occurrences):
            if not path.is_file():
                raise SystemExit(f"supplemental input missing: {path}")
        with np.load(args.supplemental_vectors, allow_pickle=False) as packed:
            extra_vectors = packed["vectors"].astype(np.float32)
            extra_hashes = packed["content_hash"].astype(str)
            extra_seqs = packed["seq"].astype(np.int32)
            extra_n_chunks = packed["n_chunks"].astype(np.int32)
        if extra_vectors.shape != (len(extra_hashes), int(meta["dim"])):
            raise SystemExit(f"supplemental vector metadata mismatch: {extra_vectors.shape}")
        if set(hashes) & set(extra_hashes):
            raise SystemExit("base/supplemental content hash collision")
        vectors = np.concatenate((vectors, extra_vectors), axis=0)
        hashes = np.concatenate((hashes, extra_hashes), axis=0)
        seqs = np.concatenate((seqs, extra_seqs), axis=0)
        n_chunks = np.concatenate((n_chunks, extra_n_chunks), axis=0)
    available = {h[:16] for h in hashes}

    hash_indices: dict[str, list[int]] = defaultdict(list)
    for idx, content_hash in enumerate(hashes):
        hash_indices[content_hash].append(idx)
    doc_rows: dict[str, np.ndarray] = {}
    structured_files = sorted(
        (ROOT / "data" / "structured").glob(f"*/{args.structured_tag}/*.clauses.json")
    )
    if not structured_files:
        raise SystemExit(f"no structured files for tag: {args.structured_tag}")
    for path in structured_files:
        document = json.loads(path.read_text(encoding="utf-8"))
        row_ids: list[int] = []
        for clause in document.get("clauses") or []:
            row_ids.extend(hash_indices.get(clause.get("content_hash") or "", []))
        if row_ids:
            doc_rows[path.name.split(".")[0]] = np.asarray(sorted(set(row_ids)), dtype=np.int64)
    supplemental_occurrence_count = 0
    if supplemental_enabled:
        supplemental_by_doc: dict[str, set[int]] = defaultdict(set)
        with args.supplemental_occurrences.open(encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, 1):
                row = json.loads(line)
                content_hash = str(row.get("content_hash") or "")
                sha12 = str(row.get("sha12") or "")
                indices = hash_indices.get(content_hash) or []
                if not sha12 or not indices:
                    raise SystemExit(f"invalid supplemental occurrence at line {line_number}")
                supplemental_by_doc[sha12].update(indices)
                supplemental_occurrence_count += 1
        for sha12, indices in supplemental_by_doc.items():
            existing = set(doc_rows.get(sha12, np.asarray([], dtype=np.int64)).tolist())
            doc_rows[sha12] = np.asarray(sorted(existing | indices), dtype=np.int64)

    selected_by_query: list[list[dict]] = [[] for _ in query_rows]
    selected_indices: set[int] = set()
    scope_fallbacks = 0
    for qidx, query_row in enumerate(query_rows):
        pool = doc_rows.get(query_row["scope_sha12"])
        if pool is None or not len(pool):
            pool = np.arange(len(hashes), dtype=np.int64)
            query_row["scope_mode"] = "global_fallback"
            scope_fallbacks += 1
        else:
            query_row["scope_mode"] = "document"
        scoped_scores = vectors[pool] @ qvec[qidx]
        raw_k = min(max(args.raw_k, args.top_k * 10), len(pool))
        local_idx = np.argpartition(scoped_scores, -raw_k)[-raw_k:]
        local_idx = local_idx[np.argsort(scoped_scores[local_idx])[::-1]]
        seen: set[str] = set()
        out: list[dict] = []
        for local_i in local_idx.tolist():
            i = int(pool[local_i])
            h = hashes[i]
            if h in seen:
                continue
            seen.add(h)
            out.append({
                "row_index": i,
                "content_hash": h,
                "seq": int(seqs[i]),
                "n_chunks": int(n_chunks[i]),
                "dense_score": round(float(scoped_scores[local_i]), 8),
            })
            selected_indices.add(i)
            if len(out) == args.top_k:
                break
        if not out:
            raise SystemExit(f"no candidates for query {qidx}")
        selected_by_query[qidx] = out

    texts: dict[int, str] = {}
    text_sources = [(jsonl_path, 0)]
    if supplemental_enabled:
        text_sources.append((args.supplemental_chunks, base_rows))
    for source_path, offset in text_sources:
        with source_path.open("r", encoding="utf-8") as fh:
            for local_idx, line in enumerate(fh):
                idx = offset + local_idx
                if idx not in selected_indices:
                    continue
                row = json.loads(line)
                if row["content_hash"] != hashes[idx] or int(row["seq"]) != int(seqs[idx]):
                    raise SystemExit(f"JSONL/NPZ row mismatch at {idx}")
                texts[idx] = row["text"]
    missing_text = selected_indices - texts.keys()
    if missing_text:
        raise SystemExit(f"candidate texts missing: {len(missing_text)}")

    records: list[dict] = []
    missing_gold = 0
    for row, candidates in zip(query_rows, selected_by_query, strict=True):
        for rank, cand in enumerate(candidates, 1):
            cand["dense_rank"] = rank
            cand["text"] = texts[cand.pop("row_index")]
        golds = set(row["gold_ids"])
        gold_available = sorted(golds & available)
        if not gold_available:
            missing_gold += 1
        record = dict(row)
        record["gold_available_in_delivery"] = gold_available
        record["candidates"] = candidates
        record["dense_gold_rank"] = _rank(golds, candidates)
        records.append(record)

    by_task = defaultdict(list)
    for record in records:
        by_task["all"].append(record)
        by_task[record["task"]].append(record)
        if record["is_exclusion"]:
            by_task["exclusion"].append(record)
        if record["dense_gold_rank"] is not None:
            by_task["retrievable"].append(record)
            by_task[f"{record['task']}_retrievable"].append(record)

    output = {
        "schema_version": "rerank-candidates-v2",
        "source": {
            "meta": str(meta_path),
            "vectors": str(npz_path),
            "chunks": str(jsonl_path),
            "supplemental_vectors": str(args.supplemental_vectors) if supplemental_enabled else None,
            "supplemental_chunks": str(args.supplemental_chunks) if supplemental_enabled else None,
            "supplemental_occurrences": str(args.supplemental_occurrences) if supplemental_enabled else None,
        },
        "eval_set": str(args.eval_set),
        "retrieval_probes": str(args.retrieval_probes),
        "retriever_model": model_id,
        "query_prefix": prefix,
        "top_k": args.top_k,
        "queries": len(records),
        "missing_gold_queries": missing_gold,
        "scope": {
            "structured_tag": args.structured_tag,
            "documents": len(doc_rows),
            "global_fallback_queries": scope_fallbacks,
            "supplemental_occurrences": supplemental_occurrence_count,
        },
        "candidate_facts_included": supplemental_enabled,
        "dense_metrics": {name: _metrics(rows) for name, rows in by_task.items()},
        "provenance": {
            "device": args.device,
            "python": platform.python_version(),
            "numpy": np.__version__,
            "query_sha256": query_sha256,
            "query_vectors": str(args.query_vectors) if args.query_vectors else None,
            "seconds": round(time.time() - started, 3),
        },
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    tmp.replace(args.output)
    print(json.dumps({
        "output": str(args.output),
        "queries": len(records),
        "missing_gold_queries": missing_gold,
        "scope": output["scope"],
        "dense_metrics": output["dense_metrics"],
        "seconds": output["provenance"]["seconds"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
