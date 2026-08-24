"""Evaluate one cross-encoder reranker on a fixed S6 Arctic-ko top-k set."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import tempfile
import time
from collections import defaultdict

import numpy as np


ROOT = pathlib.Path(__file__).resolve().parents[2]


def _args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--input",
        type=pathlib.Path,
        default=ROOT / "data" / "eval" / "s6_arctic_ko_top20_rerank.json",
    )
    ap.add_argument("--model", required=True)
    ap.add_argument("--output", type=pathlib.Path, required=True)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--max-length", type=int, default=1024)
    ap.add_argument("--memory-fraction", type=float, default=0.35)
    ap.add_argument(
        "--dtype",
        choices=("auto", "float16", "bfloat16", "float32"),
        default="auto",
        help="Model weight dtype. Use float16/bfloat16 for 4B models on 16-20 GB GPUs.",
    )
    ap.add_argument("--trust-remote-code", action="store_true")
    return ap.parse_args()


def _rank(golds: set[str], candidates: list[dict]) -> int | None:
    for pos, cand in enumerate(candidates, 1):
        if cand["content_hash"][:16] in golds:
            return pos
    return None


def _metrics(records: list[dict], key: str) -> dict:
    ranks = [r[key] for r in records]
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
    source = json.loads(args.input.read_text(encoding="utf-8"))
    records = source["records"]

    import torch
    from sentence_transformers import CrossEncoder, __version__ as st_version

    if args.device.startswith("cuda"):
        if not torch.cuda.is_available():
            raise SystemExit("CUDA requested but unavailable")
        if not 0 < args.memory_fraction <= 1:
            raise SystemExit("--memory-fraction must be in (0, 1]")
        torch.cuda.set_per_process_memory_fraction(args.memory_fraction, device=0)

    dtype_map = {
        "float16": torch.float16,
        "bfloat16": torch.bfloat16,
        "float32": torch.float32,
    }
    model_kwargs = {}
    if args.dtype != "auto":
        model_kwargs["dtype"] = dtype_map[args.dtype]

    model = CrossEncoder(
        args.model,
        device=args.device,
        trust_remote_code=args.trust_remote_code,
        max_length=args.max_length,
        model_kwargs=model_kwargs,
    )
    pairs: list[tuple[str, str]] = []
    offsets: list[tuple[int, int]] = []
    for record in records:
        begin = len(pairs)
        pairs.extend((record["query"], cand["text"]) for cand in record["candidates"])
        offsets.append((begin, len(pairs)))

    infer_started = time.time()
    scores = model.predict(
        pairs,
        batch_size=args.batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
    )
    scores = np.asarray(scores).reshape(-1)
    infer_seconds = time.time() - infer_started
    if len(scores) != len(pairs):
        raise SystemExit(f"score count mismatch: {len(scores)} != {len(pairs)}")
    if not np.isfinite(scores).all():
        raise SystemExit("reranker returned non-finite scores")
    unique_scores = int(np.unique(scores).size)
    score_span = float(np.ptp(scores))
    if len(scores) > 1 and score_span <= 1e-8:
        raise SystemExit(
            "reranker returned constant or near-constant scores; "
            "the checkpoint or scoring adapter is incompatible"
        )

    scored_records: list[dict] = []
    for record, (begin, end) in zip(records, offsets, strict=True):
        candidates = []
        for cand, score in zip(record["candidates"], scores[begin:end], strict=True):
            item = {
                "content_hash": cand["content_hash"],
                "seq": cand["seq"],
                "dense_rank": cand["dense_rank"],
                "dense_score": cand["dense_score"],
                "rerank_score": float(score),
            }
            candidates.append(item)
        candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
        out = {
            "query_id": record["query_id"],
            "task": record["task"],
            "is_exclusion": record["is_exclusion"],
            "gold_ids": record["gold_ids"],
            "dense_gold_rank": record["dense_gold_rank"],
            "rerank_gold_rank": _rank(set(record["gold_ids"]), candidates),
            "candidates": candidates,
        }
        scored_records.append(out)

    by_task = defaultdict(list)
    for record in scored_records:
        by_task["all"].append(record)
        by_task[record["task"]].append(record)
        if record["is_exclusion"]:
            by_task["exclusion"].append(record)
        if record["dense_gold_rank"] is not None:
            by_task["retrievable"].append(record)
            by_task[f"{record['task']}_retrievable"].append(record)

    result = {
        "schema_version": "s6-reranker-result-v1",
        "input": str(args.input),
        "retriever_model": source["retriever_model"],
        "reranker_model": args.model,
        "top_k": source["top_k"],
        "metrics": {
            name: {
                "dense": _metrics(rows, "dense_gold_rank"),
                "reranked": _metrics(rows, "rerank_gold_rank"),
            }
            for name, rows in by_task.items()
        },
        "provenance": {
            "device": args.device,
            "gpu": torch.cuda.get_device_name(0) if args.device.startswith("cuda") else None,
            "python": platform.python_version(),
            "torch": torch.__version__,
            "sentence_transformers": st_version,
            "batch_size": args.batch_size,
            "max_length": args.max_length,
            "memory_fraction": args.memory_fraction,
            "dtype": args.dtype,
            "pairs": len(pairs),
            "score_min": float(scores.min()),
            "score_max": float(scores.max()),
            "score_std": float(scores.std()),
            "score_span": score_span,
            "unique_scores": unique_scores,
            "tie_fraction": float(1.0 - unique_scores / len(scores)),
            "inference_seconds": round(infer_seconds, 3),
            "pairs_per_second": round(len(pairs) / infer_seconds, 3),
            "total_seconds": round(time.time() - started, 3),
        },
        "records": scored_records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=args.output.name + ".", dir=args.output.parent)
    os.close(fd)
    tmp = pathlib.Path(tmp_name)
    try:
        tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        tmp.replace(args.output)
    finally:
        if tmp.exists():
            tmp.unlink()
    print(json.dumps({
        "output": str(args.output),
        "model": args.model,
        "metrics": result["metrics"],
        "provenance": result["provenance"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
