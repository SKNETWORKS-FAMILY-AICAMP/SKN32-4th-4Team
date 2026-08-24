# -*- coding: utf-8 -*-
"""Reduce rendered S7 pages to cases that genuinely require OCR table recovery."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import orjson


ROOT = Path(__file__).resolve().parents[2]
SELF_PAY = ("자기부담", "공제금액", "공제기준금액", "본인부담", "보상대상의료비")
AMOUNT = re.compile(r"(?<!\d)\d+(?:[.,]\d+)?\s*(?:억|천만|백만|십만|만|천)?\s*(?:원|%)")
AXES = {
    "plan": ("표준형", "기본형", "선택형", "특약형"),
    "service": ("입원", "통원", "외래", "처방조제", "처방·조제", "약제비"),
    "institution": ("의원", "병원", "약국", "보건소", "상급종합", "종합병원", "의료기관"),
    "coverage": ("급여", "비급여", "3대 비급여", "3대비급여"),
}
TABLE_HEADERS = ("구분", "항목", "공제금액", "보상금액", "보상대상", "의료기관")


def page_document(insurer: str, sha12: str) -> Path:
    hits = sorted((ROOT / "data" / "extracted" / insurer).glob(f"s5_pymupdf-*/{sha12}.json"))
    if len(hits) != 1:
        raise RuntimeError(f"expected one s5 page document for {insurer}/{sha12}, got {len(hits)}")
    return hits[0]


def hard_signals(text: str, sample: dict[str, Any]) -> dict[str, Any]:
    self_pay = sorted({token for token in SELF_PAY if token in text})
    amounts = AMOUNT.findall(text)
    axes = sorted(name for name, tokens in AXES.items() if any(token in text for token in tokens))
    headers = sorted({token for token in TABLE_HEADERS if token in text})
    selection = sample.get("selection") or {}
    methods = selection.get("coordinate_methods") or []
    has_verified = any(str(item[0]) == "선" for item in methods if item)
    native = int(selection.get("native_table_count") or 0)
    category = str(sample.get("category") or "")

    # OCR is reserved for monetary multi-axis pages that have no accepted/native table.
    # Withheld borderless candidates need one amount; completely missed pages need two
    # amounts so ordinary explanatory prose does not consume GPU time.
    minimum_amounts = 1 if category == "withheld" else 2
    hard = bool(
        not has_verified
        and native == 0
        and self_pay
        and len(amounts) >= minimum_amounts
        and (len(axes) >= 2 or len(headers) >= 2)
    )
    return {
        "rule_version": "s7-ocr-hard/1",
        "hard": hard,
        "self_pay_markers": self_pay,
        "amount_tokens": amounts,
        "axes": axes,
        "table_headers": headers,
        "has_verified_line_grid": has_verified,
        "native_table_count": native,
    }


def filter_payload(manifest: dict[str, Any], config: dict[str, Any]) -> tuple[dict, dict, dict]:
    docs: dict[tuple[str, str], dict[str, Any]] = {}
    cache: dict[tuple[str, str], dict[int, str]] = {}
    kept: list[dict[str, Any]] = []
    rejected = Counter()

    for sample in manifest.get("samples") or []:
        insurer = str(sample["insurer"])
        sha12 = str(sample["sha12"])
        key = (insurer, sha12)
        if key not in cache:
            payload = orjson.loads(page_document(insurer, sha12).read_bytes())
            cache[key] = {int(page["page"]): str(page.get("text") or "") for page in payload.get("pages") or []}
        page = int(sample["page_1based"])
        if page not in cache[key]:
            raise RuntimeError(f"page missing from s5 artifact: {sample['id']}")
        signals = hard_signals(cache[key][page], sample)
        if not signals["hard"]:
            if signals["has_verified_line_grid"] or signals["native_table_count"]:
                rejected["existing_structure"] += 1
            elif not signals["self_pay_markers"]:
                rejected["no_self_pay_marker"] += 1
            elif not signals["amount_tokens"]:
                rejected["no_amount"] += 1
            elif len(signals["axes"]) < 2 and len(signals["table_headers"]) < 2:
                rejected["not_multi_axis"] += 1
            else:
                rejected["insufficient_amounts"] += 1
            continue
        enriched = {**sample, "hard_filter": signals}
        kept.append(enriched)
        group = docs.setdefault(
            key,
            {"insurer": insurer, "sha12": sha12, "category": "s7_ocr_hard", "sample_ids": []},
        )
        group["sample_ids"].append(sample["id"])

    kept_ids = {item["id"] for item in kept}
    filtered_manifest = {
        **manifest,
        "notice": "Strict OCR-hard S7 candidates only. Human approval required.",
        "selector": f"{manifest.get('selector', '')}+select_s7_ocr_hard.py:v1",
        "documents": sorted(docs.values(), key=lambda item: (item["insurer"], item["sha12"])),
        "samples": kept,
        "hard_filter_summary": {"input": len(manifest.get("samples") or []), "kept": len(kept), "rejected": dict(rejected)},
    }
    filtered_config = {
        **config,
        "created_for": "s7_ocr_hard",
        "samples": [item for item in config.get("samples") or [] if item["id"] in kept_ids],
    }
    summary = {
        "input": len(manifest.get("samples") or []),
        "kept": len(kept),
        "documents": len(docs),
        "categories": dict(Counter(item["category"] for item in kept)),
        "rejected": dict(rejected),
    }
    return filtered_manifest, filtered_config, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    config = json.loads(args.config.read_text(encoding="utf-8"))
    filtered_manifest, filtered_config, summary = filter_payload(manifest, config)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").write_text(
        json.dumps(filtered_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.out / "ocr_config.json").write_text(
        json.dumps(filtered_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
