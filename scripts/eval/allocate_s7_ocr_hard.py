# -*- coding: utf-8 -*-
"""Allocate strict OCR-hard samples according to measured GPU throughput."""
from __future__ import annotations

import argparse
import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def load(directory: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        json.loads((directory / "manifest.json").read_text(encoding="utf-8")),
        json.loads((directory / "ocr_config.json").read_text(encoding="utf-8")),
    )


def subset(manifest: dict, config: dict, samples: list[dict]) -> tuple[dict, dict]:
    ids = {item["id"] for item in samples}
    docs: dict[tuple[str, str], dict[str, Any]] = {}
    for sample in samples:
        key = (sample["insurer"], sample["sha12"])
        group = docs.setdefault(
            key,
            {"insurer": sample["insurer"], "sha12": sample["sha12"], "category": "s7_ocr_hard", "sample_ids": []},
        )
        group["sample_ids"].append(sample["id"])
    return (
        {
            **manifest,
            "notice": "Throughput-balanced S7 OCR-hard allocation. Human approval required.",
            "documents": sorted(docs.values(), key=lambda item: (item["insurer"], item["sha12"])),
            "samples": samples,
            "allocation_count": len(samples),
        },
        {**config, "samples": [item for item in config["samples"] if item["id"] in ids]},
    )


def write_device(out: Path, name: str, manifest: dict, config: dict, make_zip: bool) -> None:
    target = out / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (target / "ocr_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if make_zip:
        with zipfile.ZipFile(target / "transfer_packet.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(target / "manifest.json", "manifest.json")
            archive.write(target / "ocr_config.json", "ocr_config.json")
            for sample in manifest["samples"]:
                image = ROOT / sample["image"]
                archive.write(image, f"images/{sample['id']}.png")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hard0", required=True, type=Path)
    parser.add_argument("--hard1", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--x600-count", type=int, default=388)
    parser.add_argument("--runpod1-count", type=int, default=449)
    parser.add_argument("--six-gpu", action="store_true")
    args = parser.parse_args()
    m0, c0 = load(args.hard0)
    m1, c1 = load(args.hard1)
    samples0 = list(m0["samples"])
    samples1 = list(m1["samples"])
    if not 0 <= args.x600_count <= len(samples0):
        raise SystemExit("invalid --x600-count")
    if not 0 <= args.runpod1_count <= len(samples1):
        raise SystemExit("invalid --runpod1-count")

    if args.six_gpu:
        if len(samples0) != 827 or len(samples1) != 534:
            raise RuntimeError(
                f"six-GPU frozen allocation expects hard0=827/hard1=534, got {len(samples0)}/{len(samples1)}"
            )
        allocation = {
            "x600": samples0[:164],
            "runpod1": samples1[:190],
            "runpod2": samples0[164:385],
            "runpod3": samples0[385:495] + samples1[190:362],
            "runpod4": samples0[495:605] + samples1[362:534],
            "runpod5": samples0[605:827],
        }
    else:
        allocation = {
            "x600": samples0[: args.x600_count],
            "runpod1": samples1[: args.runpod1_count],
            "runpod2": samples0[args.x600_count :] + samples1[args.runpod1_count :],
        }
    all_ids = [item["id"] for items in allocation.values() for item in items]
    expected = {item["id"] for item in samples0 + samples1}
    if len(all_ids) != len(set(all_ids)) or set(all_ids) != expected:
        raise RuntimeError("allocation overlap or omission")

    # x600 already has batch0 images; RunPod1 already has batch1 images.
    for name, items in allocation.items():
        base_manifest, base_config = (m0, c0) if name == "x600" else (m1, c1)
        if name not in {"x600", "runpod1"}:
            merged_config = {**c0, "samples": c0["samples"] + c1["samples"]}
            base_manifest = {**m0, "samples": samples0 + samples1}
            base_config = merged_config
        manifest, config = subset(base_manifest, base_config, items)
        write_device(args.out, name, manifest, config, make_zip=name not in {"x600", "runpod1"})

    summary = {name: len(items) for name, items in allocation.items()}
    summary["total"] = len(all_ids)
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
