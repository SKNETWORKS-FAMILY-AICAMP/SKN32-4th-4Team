"""Measure production-shaped top-k reranker latency on a fixed candidate set."""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dtype", choices=("auto", "float16", "bfloat16", "float32"), default="float16")
    parser.add_argument("--max-length", type=int, default=768)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--requests", type=int, default=32)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--trust-remote-code", action="store_true")
    args = parser.parse_args()

    import torch
    from sentence_transformers import CrossEncoder, __version__ as st_version

    source = json.loads(args.input.read_text(encoding="utf-8"))
    records = [record for record in source["records"] if record.get("candidates")][: args.requests]
    if len(records) < args.requests:
        raise SystemExit(f"only {len(records)} records available")

    dtype_map = {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}
    model_kwargs = {} if args.dtype == "auto" else {"dtype": dtype_map[args.dtype]}
    load_started = time.perf_counter()
    model = CrossEncoder(
        args.model,
        device="cuda",
        trust_remote_code=args.trust_remote_code,
        max_length=args.max_length,
        model_kwargs=model_kwargs,
    )
    load_seconds = time.perf_counter() - load_started

    def pairs(record: dict) -> list[tuple[str, str]]:
        return [(record["query"], candidate["text"]) for candidate in record["candidates"]]

    for record in records[: args.warmup]:
        model.predict(pairs(record), batch_size=args.batch_size, show_progress_bar=False, convert_to_numpy=True)
    torch.cuda.synchronize()

    profiles = {}
    for group_size in (1, 2, 4):
        latencies: list[float] = []
        request_count = 0
        pair_count = 0
        for start in range(0, len(records), group_size):
            group = records[start : start + group_size]
            if len(group) != group_size:
                break
            batch_pairs = [pair for record in group for pair in pairs(record)]
            torch.cuda.synchronize()
            began = time.perf_counter()
            scores = np.asarray(model.predict(
                batch_pairs,
                batch_size=args.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
            )).reshape(-1)
            torch.cuda.synchronize()
            elapsed = time.perf_counter() - began
            if len(scores) != len(batch_pairs) or not np.isfinite(scores).all():
                raise SystemExit("invalid reranker output")
            latencies.append(elapsed)
            request_count += len(group)
            pair_count += len(batch_pairs)
        total = sum(latencies)
        profiles[str(group_size)] = {
            "microbatch_requests": group_size,
            "samples": len(latencies),
            "requests": request_count,
            "pairs": pair_count,
            "latency_seconds_p50": round(float(np.percentile(latencies, 50)), 4),
            "latency_seconds_p95": round(float(np.percentile(latencies, 95)), 4),
            "latency_seconds_max": round(max(latencies), 4),
            "effective_requests_per_second": round(request_count / total, 4),
            "effective_pairs_per_second": round(pair_count / total, 4),
        }

    result = {
        "schema_version": "reranker-latency-v1",
        "model": args.model,
        "input": str(args.input),
        "top_k": source.get("top_k"),
        "profiles": profiles,
        "provenance": {
            "gpu": torch.cuda.get_device_name(0),
            "python": platform.python_version(),
            "torch": torch.__version__,
            "sentence_transformers": st_version,
            "dtype": args.dtype,
            "max_length": args.max_length,
            "pair_batch_size": args.batch_size,
            "load_seconds": round(load_seconds, 3),
            "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 1024 / 1024, 1),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
