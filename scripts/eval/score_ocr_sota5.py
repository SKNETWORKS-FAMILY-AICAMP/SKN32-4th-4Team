"""Score OCR SOTA-5 remote outputs without treating them as approved facts."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from html import unescape
from pathlib import Path


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("–", "~").replace("—", "~").replace("−", "~")
    return re.sub(r"\s+", "", value).upper()


def html_rows(value: str) -> list[str]:
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", value, flags=re.IGNORECASE | re.DOTALL)
    return [
        normalize(unescape(re.sub(r"<[^>]+>", " ", row)))
        for row in rows
    ]


def code_tokens(value: str) -> list[str]:
    return re.findall(r"[A-Z]\d+(?:\.\d+)?(?:~(?:[A-Z])?\d+(?:\.\d+)?)?", value.upper())


def non_korean_cjk_count(value: str) -> int:
    # Unified ideographs catch Chinese/Japanese substitutions without counting Hangul.
    return sum("\u4e00" <= char <= "\u9fff" for char in value)


def duplicate_line_fraction(value: str) -> float:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return 0.0
    return round(1 - len(set(lines)) / len(lines), 4)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--table-gold", type=Path)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    rows = []
    for model in config["models"]:
        model_dir = args.results_root / model["slug"]
        run_path = model_dir / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8")) if run_path.is_file() else {}
        for sample in config["samples"]:
            result_path = model_dir / f"{sample['id']}.json"
            result = (
                json.loads(result_path.read_text(encoding="utf-8"))
                if result_path.is_file()
                else {}
            )
            output = normalize(result.get("output", ""))
            expected = sample.get("expected_tokens", [])
            token_hits = {
                token: normalize(token) in output
                for token in expected
            }
            rows.append(
                {
                    "model": model["slug"],
                    "model_id": model["model_id"],
                    "run_status": run.get("status", "missing"),
                    "sample_id": sample["id"],
                    "status": result.get("status", "missing"),
                    "latency_seconds": result.get("latency_seconds"),
                    "peak_vram_mb": result.get("peak_vram_mb"),
                    "output_chars": result.get("output_chars", 0),
                    "has_html_table": "<table" in result.get("output", "").lower(),
                    "non_korean_cjk_count": non_korean_cjk_count(result.get("output", "")),
                    "duplicate_line_fraction": duplicate_line_fraction(result.get("output", "")),
                    "token_hits": token_hits,
                    "token_recall": (
                        sum(token_hits.values()) / len(token_hits) if token_hits else None
                    ),
                    "needs_human_structure_review": result.get("status") == "success",
                    "serving_eligible": False,
                }
            )

    kcd_pair_summary = []
    if args.table_gold:
        gold = json.loads(args.table_gold.read_text(encoding="utf-8"))
        records = next(table for table in gold["tables"] if "records" in table)["records"]
        sample_id = "kcd_gold_heungkukfire_p109"
        for model in config["models"]:
            result_path = args.results_root / model["slug"] / f"{sample_id}.json"
            result = (
                json.loads(result_path.read_text(encoding="utf-8"))
                if result_path.is_file()
                else {}
            )
            output = result.get("output", "")
            rows_normalized = html_rows(output)
            output_normalized = normalize(output)
            details = []
            for record in records:
                name = normalize(record["name"])
                codes = [normalize(token) for token in code_tokens(record["codes"])]
                matching_rows = [row for row in rows_normalized if name in row]
                name_hit = bool(matching_rows)
                code_hit = all(code in output_normalized for code in codes)
                pair_hit = any(all(code in row for code in codes) for row in matching_rows)
                details.append(
                    {
                        "no": record["no"],
                        "name": record["name"],
                        "name_hit": name_hit,
                        "codes_hit_anywhere": code_hit,
                        "pair_hit_same_html_row": pair_hit,
                    }
                )
            kcd_pair_summary.append(
                {
                    "model": model["slug"],
                    "status": result.get("status", "missing"),
                    "name_exact": sum(item["name_hit"] for item in details),
                    "codes_complete_anywhere": sum(item["codes_hit_anywhere"] for item in details),
                    "pair_exact_same_html_row": sum(item["pair_hit_same_html_row"] for item in details),
                    "denominator": len(details),
                    "details": details,
                }
            )

    summary = {
        "notice": "Smoke score only. No output is serving/citation eligible.",
        "rows": rows,
        "kcd_pair_summary": kcd_pair_summary,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {len(rows)} rows -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
