# -*- coding: utf-8 -*-
"""Exact-image deduplication and six-GPU allocation for strict S7 OCR pages."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from scripts.eval.allocate_s7_ocr_hard import load, subset, write_device


def representatives(samples: list[dict]) -> tuple[list[dict], list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for sample in samples:
        groups[str(sample["image_sha256"])].append(sample)
    reps: list[dict] = []
    aliases: list[dict] = []
    for image_sha, members in sorted(groups.items()):
        members.sort(key=lambda item: item["id"])
        representative = members[0]
        reps.append(representative)
        aliases.append(
            {
                "image_sha256": image_sha,
                "representative_id": representative["id"],
                "member_ids": [item["id"] for item in members],
            }
        )
    return reps, aliases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hard0", required=True, type=Path)
    parser.add_argument("--hard1", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    m0, c0 = load(args.hard0)
    m1, c1 = load(args.hard1)
    reps0, aliases0 = representatives(list(m0["samples"]))
    reps1, aliases1 = representatives(list(m1["samples"]))
    if (len(reps0), len(reps1)) != (501, 331):
        raise RuntimeError(f"frozen exact-dedup counts changed: {len(reps0)}/{len(reps1)}")

    allocation = {
        "x600": reps0[:120],
        "runpod1": reps1[:139],
        "runpod2": reps0[120:243],
        "runpod3": reps0[243:365],
        "runpod4": reps1[139:296],
        "runpod5": reps0[365:501] + reps1[296:331],
    }
    allocated = [item for values in allocation.values() for item in values]
    if len(allocated) != 832 or len({item["id"] for item in allocated}) != 832:
        raise RuntimeError("dedup allocation overlap or omission")

    combined_manifest = {**m0, "samples": list(m0["samples"]) + list(m1["samples"])}
    combined_config = {**c0, "samples": list(c0["samples"]) + list(c1["samples"])}
    for name, items in allocation.items():
        manifest, config = subset(combined_manifest, combined_config, items)
        manifest["exact_image_dedup"] = {
            "rule": "identical PNG SHA-256 only",
            "total_occurrences": 1361,
            "unique_images": 832,
        }
        write_device(args.out, name, manifest, config, make_zip=name not in {"x600", "runpod1"})

    args.out.mkdir(parents=True, exist_ok=True)
    alias_map = {
        "schema_version": "1",
        "rule": "OCR output may be expanded only when rendered PNG SHA-256 is identical",
        "occurrences": 1361,
        "unique_images": 832,
        "saved_inferences": 529,
        "groups": sorted(aliases0 + aliases1, key=lambda item: item["image_sha256"]),
    }
    (args.out / "alias_map.json").write_text(
        json.dumps(alias_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {name: len(items) for name, items in allocation.items()}
    summary.update({"unique_images": 832, "occurrences": 1361, "saved_inferences": 529})
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
