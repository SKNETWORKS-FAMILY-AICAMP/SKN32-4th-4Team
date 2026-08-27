"""Score the 48-document MinerU shadow run and build a local review page."""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts.eval.selfpay_axis_binder import valid_normalized_bbox


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 3)


def load_candidates(candidate_dir: Path) -> list[dict[str, Any]]:
    candidates = []
    for path in sorted(candidate_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        candidates.extend(payload.get("candidates") or [])
    return candidates


def result_metrics(manifest: dict[str, Any], results_root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    statuses = Counter()
    latencies: list[float] = []
    peak_vram: list[float] = []
    html_pages = 0
    structured_tables = 0
    bbox_tables = 0
    sha_mismatches = []
    cjk_chars = 0
    results: dict[str, dict[str, Any]] = {}

    for sample in manifest["samples"]:
        path = results_root / f"{sample['id']}.json"
        if not path.is_file():
            statuses["missing"] += 1
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        results[sample["id"]] = result
        status = result.get("status", "missing")
        statuses[status] += 1
        if result.get("image_sha256") != sample.get("image_sha256"):
            sha_mismatches.append(sample["id"])
        if isinstance(result.get("latency_seconds"), (int, float)):
            latencies.append(float(result["latency_seconds"]))
        if isinstance(result.get("peak_vram_mb"), (int, float)):
            peak_vram.append(float(result["peak_vram_mb"]))
        output = str(result.get("output") or "")
        if "<table" in output.lower():
            html_pages += 1
        cjk_chars += len(re.findall(r"[\u4e00-\u9fff]", output))
        for element in result.get("structured") or []:
            if element.get("type") == "table":
                structured_tables += 1
                if valid_normalized_bbox(element.get("bbox")):
                    bbox_tables += 1

    expected = len(manifest["samples"])
    success = statuses["success"]
    metrics = {
        "expected_pages": expected,
        "status_counts": dict(statuses),
        "process_success_rate": round(success / expected, 6) if expected else 0,
        "image_sha_mismatch_count": len(sha_mismatches),
        "image_sha_mismatch_samples": sha_mismatches,
        "latency_seconds": {
            "count": len(latencies),
            "total": round(sum(latencies), 3),
            "mean": round(statistics.mean(latencies), 3) if latencies else None,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": round(max(latencies), 3) if latencies else None,
        },
        "peak_vram_mb_max": round(max(peak_vram), 1) if peak_vram else None,
        "html_table_page_count": html_pages,
        "structured_table_count": structured_tables,
        "structured_table_bbox_count": bbox_tables,
        "table_bbox_preservation_rate": (
            round(bbox_tables / structured_tables, 6) if structured_tables else None
        ),
        "non_korean_cjk_chars": cjk_chars,
    }
    return metrics, results


def candidate_metrics(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    reasons = Counter()
    categories = Counter()
    for item in candidates:
        categories[str(item.get("category"))] += 1
        reasons.update(item.get("validation", {}).get("reasons") or [])
    count = len(candidates)
    amount_origin_groups = Counter(
        (
            item.get("document_sha12"),
            item.get("page_1based"),
            item.get("source", {}).get("amount_origin_group"),
        )
        for item in candidates
    )
    complete = sum(
        bool(item.get("plan"))
        and bool(item.get("service"))
        and bool(item.get("amount_tokens") or item.get("rate_tokens"))
        and bool(item.get("source", {}).get("table_bbox"))
        for item in candidates
    )
    return {
        "candidate_count": count,
        "candidate_ids_unique": len({item.get("candidate_id") for item in candidates}),
        "serving_eligible_count": sum(bool(item.get("serving_eligible")) for item in candidates),
        "inferred_count": sum(bool(item.get("inferred")) for item in candidates),
        "table_bbox_count": sum(bool(item.get("source", {}).get("table_bbox")) for item in candidates),
        "complete_plan_service_amount_source_count": complete,
        "complete_plan_service_amount_source_rate": round(complete / count, 6) if count else None,
        "review_required_count": sum(
            item.get("validation", {}).get("status") == "review_required"
            for item in candidates
        ),
        "reused_amount_origin_group_count": sum(value > 1 for value in amount_origin_groups.values()),
        "max_candidates_per_amount_origin": max(amount_origin_groups.values(), default=0),
        "validation_reason_counts": dict(reasons),
        "category_counts": dict(categories),
    }


def build_review_html(
    manifest: dict[str, Any],
    results: dict[str, dict[str, Any]],
    candidates: list[dict[str, Any]],
    output_path: Path,
) -> None:
    by_document: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_document[candidate["document_sha12"]].append(candidate)
    samples = {sample["id"]: sample for sample in manifest["samples"]}
    blocks = []
    documents = manifest.get("documents")
    if not documents:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for sample in manifest["samples"]:
            key = (sample["insurer"], sample["sha12"])
            document = grouped.setdefault(
                key,
                {
                    "insurer": sample["insurer"],
                    "sha12": sample["sha12"],
                    "category": "regression",
                    "sample_ids": [],
                },
            )
            document["sample_ids"].append(sample["id"])
        documents = list(grouped.values())
    for document in documents:
        image_tags = []
        for sample_id in document["sample_ids"]:
            sample = samples[sample_id]
            result = results.get(sample_id, {})
            image_name = Path(sample["image"]).name
            image_tags.append(
                f'<figure><img loading="lazy" src="images/{html.escape(image_name)}">'
                f'<figcaption>{html.escape(sample_id)} · {html.escape(str(result.get("status", "missing")))}</figcaption></figure>'
            )
        fact_rows = []
        for item in by_document.get(document["sha12"], []):
            fact_rows.append(
                "<tr>"
                f"<td>{item['page_1based']}</td>"
                f"<td>{html.escape(item.get('plan') or '∅')}</td>"
                f"<td>{html.escape(', '.join(item.get('service') or []) or '∅')}</td>"
                f"<td>{html.escape(item.get('institution') or '∅')}</td>"
                f"<td>{html.escape(item.get('amount_formula') or '')}</td>"
                f"<td>{html.escape(', '.join(item.get('validation', {}).get('reasons') or []) or 'shadow_pass')}</td>"
                "<td><select><option>미검수</option><option>정확</option><option>오결합</option><option>누락</option></select></td>"
                "</tr>"
            )
        blocks.append(
            f"<section><h2>{html.escape(document['insurer'])} · {document['sha12']} · {document['category']}</h2>"
            f"<div class='images'>{''.join(image_tags)}</div>"
            "<table><thead><tr><th>p</th><th>plan</th><th>service</th><th>institution</th><th>amount/formula</th><th>검수 사유</th><th>사람 판정</th></tr></thead>"
            f"<tbody>{''.join(fact_rows) or '<tr><td colspan=7>candidate 없음</td></tr>'}</tbody></table></section>"
        )
    body = f"""<!doctype html><html lang="ko"><meta charset="utf-8">
<title>OCR shadow48 검수</title>
<style>body{{font:14px sans-serif;margin:24px}}section{{border-top:3px solid #333;padding:16px 0}}
.images{{display:flex;gap:8px;overflow:auto}}figure{{margin:0;min-width:30%}}img{{width:100%;border:1px solid #bbb}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #bbb;padding:5px;vertical-align:top}}td:nth-child(5){{max-width:520px}}</style>
<h1>MinerU OCR shadow48 사람 검수 패킷</h1>
<p>미라벨 후보입니다. 셀 단위 bbox가 없으므로 자동 인용·serving에 사용할 수 없습니다.</p>
{''.join(blocks)}</html>"""
    output_path.write_text(body, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--candidate-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--review-html", required=True, type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    run, results = result_metrics(manifest, args.results_root)
    candidates = load_candidates(args.candidate_dir)
    summary = {
        "schema_version": "1",
        "notice": "Unlabeled shadow evaluation. This is not a precision/recall claim.",
        "input_set_sha256": manifest.get("input_set_sha256"),
        "run": run,
        "candidates": candidate_metrics(candidates),
        "gate": {
            "candidate_serving_flags_zero": not any(item.get("serving_eligible") for item in candidates),
            "process_success_98pct": run["process_success_rate"] >= 0.98,
            "table_bbox_100pct": (
                run["structured_table_count"] > 0
                and run["table_bbox_preservation_rate"] == 1.0
            ),
            "gold_join_error_zero": None,
            "expansion_allowed": False,
            "blocked_reason": "Two-person adjudicated join gold is not complete.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    build_review_html(manifest, results, candidates, args.review_html)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
