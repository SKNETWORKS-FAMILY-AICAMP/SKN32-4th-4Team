# -*- coding: utf-8 -*-
"""Prepare a deadline-bounded, SHA-verifiable S7 OCR production batch.

The selector uses the already frozen self-pay scan.  It prioritizes pages where
both native and verified line-grid structures are absent, then unverified
borderless candidates.  It never sends PDFs or production manifests remotely.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import fitz
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCAN = ROOT / "scan_selfpay_pages.json"
DEFAULT_OUT = ROOT / "data" / "work" / "s7" / "ocr_batch"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def source_pdf(insurer: str, sha12: str) -> Path:
    matches = sorted((ROOT / "data" / "raw" / "insurance_terms" / insurer).glob(f"{sha12}_*.pdf"))
    if not matches:
        raise FileNotFoundError(f"source PDF not found: {insurer}/{sha12}")
    digests = {sha256_file(path) for path in matches}
    if len(digests) != 1:
        raise RuntimeError(f"ambiguous source PDF contents: {insurer}/{sha12}")
    digest = next(iter(digests))
    if not digest.startswith(sha12):
        raise RuntimeError(f"source PDF SHA mismatch: {insurer}/{sha12}")
    return matches[0]


def classify(hit: dict[str, Any]) -> tuple[str, int]:
    methods = hit.get("methods") or []
    has_verified = any(str(item[0]) == "선" for item in methods if item)
    native_count = int(hit.get("ntab") or 0)
    if not methods and native_count == 0:
        return "missed", 0
    if has_verified:
        return "accepted", 3
    if methods:
        return "withheld", 1
    return "native_only", 2


def rank_hits(scan: list[dict[str, Any]], excluded: set[str]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for raw in scan:
        path = Path(str(raw["file"]).replace("\\", "/"))
        sha12 = path.stem
        page = int(raw["page"])
        sample_id = f"{sha12}_p{page:04d}"
        if sample_id in excluded:
            continue
        category, priority = classify(raw)
        methods = raw.get("methods") or []
        max_rows = max((int(item[2]) for item in methods if len(item) >= 3), default=0)
        candidate = {
            **raw,
            "sha12": sha12,
            "page_1based": page,
            "id": sample_id,
            "category": category,
            "priority": priority,
            "max_candidate_rows": max_rows,
        }
        previous = unique.get(sample_id)
        score = (priority, -max_rows, -int(raw.get("len") or 0), str(raw.get("ins") or ""), sha12, page)
        if previous is None:
            unique[sample_id] = candidate
        else:
            old_score = (
                previous["priority"],
                -previous["max_candidate_rows"],
                -int(previous.get("len") or 0),
                str(previous.get("ins") or ""),
                previous["sha12"],
                previous["page_1based"],
            )
            if score < old_score:
                unique[sample_id] = candidate
    return sorted(
        unique.values(),
        key=lambda item: (
            item["priority"],
            -item["max_candidate_rows"],
            -int(item.get("len") or 0),
            str(item.get("ins") or ""),
            item["sha12"],
            item["page_1based"],
        ),
    )


def excluded_ids(manifests: list[Path]) -> set[str]:
    result: set[str] = set()
    for path in manifests:
        payload = json.loads(path.read_text(encoding="utf-8"))
        result.update(str(item["id"]) for item in payload.get("samples") or [])
    return result


def build(out: Path, selected: list[dict[str, Any]], dpi: int, make_zip: bool) -> dict[str, Any]:
    out = out.resolve()
    images = out / "images"
    images.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    documents: dict[tuple[str, str], dict[str, Any]] = {}
    pdf_cache: dict[tuple[str, str], tuple[Path, str]] = {}

    for index, item in enumerate(selected, 1):
        insurer = str(item["ins"])
        sha12 = str(item["sha12"])
        key = (insurer, sha12)
        if key not in pdf_cache:
            pdf = source_pdf(insurer, sha12)
            pdf_cache[key] = (pdf, sha256_file(pdf))
        pdf, pdf_sha = pdf_cache[key]
        image_path = images / f"{item['id']}.png"
        temporary_path = images / f".{item['id']}.tmp.png"
        with fitz.open(pdf) as opened:
            page_number = int(item["page_1based"])
            if not 1 <= page_number <= len(opened):
                raise RuntimeError(f"page outside PDF: {item['id']}")
            pix = opened[page_number - 1].get_pixmap(dpi=dpi, alpha=False)
            pix.save(temporary_path)
        with Image.open(temporary_path) as check:
            check.verify()
        if temporary_path.stat().st_size <= 0:
            raise RuntimeError(f"empty rendered image: {item['id']}")
        os.replace(temporary_path, image_path)
        sample = {
            "id": item["id"],
            "insurer": insurer,
            "sha12": sha12,
            "page_1based": int(item["page_1based"]),
            "category": item["category"],
            "kind": "s7_selfpay_production_candidate",
            "image": image_path.relative_to(ROOT).as_posix(),
            "image_sha256": sha256_file(image_path),
            "source_pdf_sha256": pdf_sha,
            "render_dpi": dpi,
            "width": pix.width,
            "height": pix.height,
            "selection": {
                "rule_version": "s7-ocr-priority/1",
                "priority": int(item["priority"]),
                "native_table_count": int(item.get("ntab") or 0),
                "coordinate_methods": item.get("methods") or [],
                "max_candidate_rows": int(item["max_candidate_rows"]),
                "page_text_length": int(item.get("len") or 0),
            },
        }
        samples.append(sample)
        group = documents.setdefault(
            key,
            {
                "insurer": insurer,
                "sha12": sha12,
                "category": "s7_priority",
                "sample_ids": [],
            },
        )
        group["sample_ids"].append(item["id"])
        if index % 50 == 0 or index == len(selected):
            print(f"render {index:,}/{len(selected):,}", flush=True)

    manifest = {
        "schema_version": "1",
        "notice": "S7 production candidates. Human approval required; no serving/citation eligibility.",
        "selector": "prepare_s7_ocr_batch.py:v1",
        "render_dpi": dpi,
        "documents": sorted(documents.values(), key=lambda item: (item["insurer"], item["sha12"])),
        "samples": samples,
    }
    manifest["input_set_sha256"] = hashlib.sha256(
        json.dumps(
            [{"id": item["id"], "image_sha256": item["image_sha256"]} for item in samples],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    config = {
        "created_for": "s7_selfpay_production_candidate",
        "models": [
            {
                "slug": "mineru_2_5_pro_2605",
                "model_id": "opendatalab/MinerU2.5-Pro-2605-1.2B",
                "adapter": "mineru",
                "model_class": "qwen2_vl",
                "prompt": "",
                "max_new_tokens": 8192,
                "decode": {"do_sample": False, "temperature": 0.0},
            }
        ],
        "samples": [
            {
                key: item[key]
                for key in ("id", "insurer", "sha12", "page_1based", "category", "kind", "image_sha256")
            }
            for item in samples
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "ocr_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if make_zip:
        with zipfile.ZipFile(out / "transfer_packet.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(out / "manifest.json", "manifest.json")
            archive.write(out / "ocr_config.json", "ocr_config.json")
            for item in samples:
                archive.write(images / f"{item['id']}.png", f"images/{item['id']}.png")
    return {
        "samples": len(samples),
        "documents": len(documents),
        "categories": {
            category: sum(item["category"] == category for item in samples)
            for category in sorted({item["category"] for item in samples})
        },
        "input_set_sha256": manifest["input_set_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", type=Path, default=DEFAULT_SCAN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--exclude-manifest", type=Path, action="append", default=[])
    parser.add_argument("--zip", action="store_true")
    args = parser.parse_args()
    scan = json.loads(args.scan.read_text(encoding="utf-8"))
    ranked = rank_hits(scan, excluded_ids(args.exclude_manifest))
    if args.offset < 0:
        raise SystemExit("--offset must be non-negative")
    selected = ranked[args.offset : args.offset + args.limit] if args.limit else ranked[args.offset :]
    summary = build(args.out, selected, args.dpi, args.zip)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
