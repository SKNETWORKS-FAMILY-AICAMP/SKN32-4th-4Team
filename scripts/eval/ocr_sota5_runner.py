"""Run one offline OCR candidate against the prepared page-image benchmark.

This script is intentionally self-contained so it can be copied to the shared
Windows GPU worker.  It never reads source PDFs or production manifests.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def load_standard_model(spec: dict[str, Any], *, quantized: bool = False):
    import torch
    from transformers import (
        AutoModelForImageTextToText,
        AutoModelForMultimodalLM,
        AutoProcessor,
    )

    model_id = spec["model_id"]
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    kwargs: dict[str, Any] = {
        "device_map": "auto",
        "trust_remote_code": True,
        "dtype": torch.bfloat16,
    }
    if quantized:
        from transformers import BitsAndBytesConfig

        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
    model_cls = (
        AutoModelForMultimodalLM
        if spec.get("model_class") == "multimodal"
        else AutoModelForImageTextToText
    )
    model = model_cls.from_pretrained(model_id, **kwargs).eval()
    return model, processor


def infer_chat(model, processor, image, prompt: str, max_new_tokens: int) -> str:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    template_kwargs = {
        "add_generation_prompt": True,
        "tokenize": True,
        "return_dict": True,
        "return_tensors": "pt",
    }
    inputs = processor.apply_chat_template(messages, **template_kwargs).to(model.device)
    inputs.pop("token_type_ids", None)
    input_length = inputs["input_ids"].shape[-1]
    outputs = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=False,
    )
    return processor.decode(outputs[0][input_length:], skip_special_tokens=True).strip()


def load_paddle(spec: dict[str, Any], *, quantized: bool = False):
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig

    kwargs: dict[str, Any] = {"dtype": torch.bfloat16}
    if quantized:
        kwargs.update(
            {
                "device_map": "auto",
                "quantization_config": BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                    bnb_4bit_use_double_quant=True,
                ),
            }
        )
    model = AutoModelForImageTextToText.from_pretrained(spec["model_id"], **kwargs)
    if not quantized:
        model = model.to("cuda")
    model = model.eval()
    processor = AutoProcessor.from_pretrained(spec["model_id"])
    return model, processor


def infer_paddle(model, processor, image, prompt: str, max_new_tokens: int) -> str:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    max_pixels = 1280 * 28 * 28
    min_pixels = getattr(processor.image_processor, "min_pixels", 112896)
    inputs = processor.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        images_kwargs={
            "size": {
                "shortest_edge": min_pixels,
                "longest_edge": max_pixels,
            }
        },
    ).to(model.device)
    outputs = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=False,
    )
    input_length = inputs["input_ids"].shape[-1]
    # The official example removes the final model-specific control token.
    return processor.decode(outputs[0][input_length:-1]).strip()


def load_mineru(spec: dict[str, Any]):
    from mineru_vl_utils import MinerUClient
    from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

    model = Qwen2VLForConditionalGeneration.from_pretrained(
        spec["model_id"], dtype="auto", device_map="auto"
    ).eval()
    processor = AutoProcessor.from_pretrained(spec["model_id"], use_fast=True)
    client = MinerUClient(
        backend="transformers",
        model=model,
        processor=processor,
        image_analysis=False,
    )
    return model, client


def infer_mineru(client, image) -> tuple[str, Any]:
    from mineru_vl_utils.post_process import json2md

    structured = client.two_step_extract(image)
    return json2md(structured), structured


def model_revision(model) -> str | None:
    return getattr(getattr(model, "config", None), "_commit_hash", None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-new-tokens", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.manifest:
        prepared = json.loads(args.manifest.read_text(encoding="utf-8"))
        prepared_by_id = {item["id"]: item for item in prepared["samples"]}
        config["samples"] = [
            {**item, **prepared_by_id.get(item["id"], {})}
            for item in config["samples"]
        ]
    if not args.manifest and any(not item.get("image_sha256") for item in config["samples"]):
        raise SystemExit("manifest is required when config samples do not contain image_sha256")
    try:
        spec = next(item for item in config["models"] if item["slug"] == args.model)
    except StopIteration:
        raise SystemExit(f"unknown model slug: {args.model}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    samples = config["samples"] if args.limit <= 0 else config["samples"][: args.limit]

    import torch
    import transformers
    from PIL import Image

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    run_meta: dict[str, Any] = {
        "started_at": utc_now(),
        "model_slug": spec["slug"],
        "model_id": spec["model_id"],
        "adapter": spec["adapter"],
        "host": platform.node(),
        "python": sys.version,
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "packages": {
            "mineru-vl-utils": package_version("mineru-vl-utils"),
            "pillow": package_version("pillow"),
            "accelerate": package_version("accelerate"),
        },
        "seed": args.seed,
        "decode_contract": spec.get("decode", {"do_sample": False}),
        "resume": args.resume,
        "expected_samples": len(config["samples"]),
        "selected_samples": len(samples),
        "cuda_available": torch.cuda.is_available(),
        "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "results": [],
    }
    (args.output_dir / "run_started.json").write_text(
        json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    model = None
    helper = None
    load_started = time.perf_counter()
    try:
        if spec["adapter"] in {"paddle_element", "paddle_element_4bit"}:
            model, helper = load_paddle(
                spec, quantized=spec["adapter"] == "paddle_element_4bit"
            )
        elif spec["adapter"] == "mineru":
            model, helper = load_mineru(spec)
        else:
            model, helper = load_standard_model(
                spec, quantized=spec["adapter"] == "chat_4bit"
            )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        run_meta["load_seconds"] = round(time.perf_counter() - load_started, 3)
        run_meta["model_revision"] = model_revision(model)
    except Exception as exc:
        run_meta.update(
            {
                "status": "load_error",
                "load_seconds": round(time.perf_counter() - load_started, 3),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "finished_at": utc_now(),
            }
        )
        (args.output_dir / "run.json").write_text(
            json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(run_meta, ensure_ascii=False))
        return 2

    max_new_tokens = args.max_new_tokens or int(spec["max_new_tokens"])
    for sample in samples:
        image_path = args.input_dir / f"{sample['id']}.png"
        output_path = args.output_dir / f"{sample['id']}.json"
        if args.resume and output_path.is_file():
            existing = json.loads(output_path.read_text(encoding="utf-8"))
            if (
                existing.get("status") == "success"
                and existing.get("image_sha256") == sample.get("image_sha256")
            ):
                existing = {**existing, "reused_existing": True}
                run_meta["results"].append(existing)
                print(
                    json.dumps(
                        {
                            "sample_id": sample["id"],
                            "status": "reused_existing",
                            "image_sha256": existing.get("image_sha256"),
                        },
                        ensure_ascii=False,
                    )
                )
                continue
        result: dict[str, Any] = {
            "sample_id": sample["id"],
            "image": image_path.name,
            "expected_image_sha256": sample.get("image_sha256"),
            "model_slug": spec["slug"],
            "model_id": spec["model_id"],
            "model_revision": run_meta.get("model_revision"),
            "runner_environment": {
                "python": run_meta["python"],
                "torch": run_meta["torch"],
                "transformers": run_meta["transformers"],
                "mineru_vl_utils": run_meta["packages"].get("mineru-vl-utils"),
            },
            "started_at": utc_now(),
        }
        try:
            if not image_path.is_file():
                raise FileNotFoundError(image_path)
            result["image_sha256"] = sha256_file(image_path)
            if sample.get("image_sha256") and result["image_sha256"] != sample["image_sha256"]:
                raise ValueError("input image SHA256 mismatch")
            image = Image.open(image_path).convert("RGB")
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
            started = time.perf_counter()
            structured = None
            if spec["adapter"] in {"paddle_element", "paddle_element_4bit"}:
                output = infer_paddle(
                    model, helper, image, spec["prompt"], max_new_tokens
                )
            elif spec["adapter"] == "mineru":
                output, structured = infer_mineru(helper, image)
            else:
                output = infer_chat(
                    model, helper, image, spec["prompt"], max_new_tokens
                )
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            result.update(
                {
                    "status": "success",
                    "latency_seconds": round(time.perf_counter() - started, 3),
                    "peak_vram_mb": (
                        round(torch.cuda.max_memory_allocated() / 1024**2, 1)
                        if torch.cuda.is_available()
                        else None
                    ),
                    "output_chars": len(output),
                    "output": output,
                    "structured": structured,
                }
            )
        except Exception as exc:
            result.update(
                {
                    "status": "inference_error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
        result["finished_at"] = utc_now()
        run_meta["results"].append(result)
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({k: v for k, v in result.items() if k != "output"}, ensure_ascii=False))

    if not all(result["status"] == "success" for result in run_meta["results"]):
        run_meta["status"] = "partial_error"
    elif len(samples) < len(config["samples"]):
        run_meta["status"] = "success_partial"
    else:
        run_meta["status"] = "success"
    run_meta["finished_at"] = utc_now()
    (args.output_dir / "run.json").write_text(
        json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if run_meta["status"] in {"success", "success_partial"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
