"""AI2 설명 모델의 구조·인용·기권 행동을 골든 기대값과 비교한다.

골든 `.yaml`은 외부 YAML 의존성을 피하려고 YAML 1.2에서 유효한 JSON 문법으로
저장한다. 이 스크립트는 모델을 호출하지 않고, 별도 실행기가 만든 JSONL 예측을 평가한다.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


VERDICTS = {"likely_covered", "unlikely", "needs_documents", "needs_expert"}


def _valid_prediction(row: dict) -> bool:
    return bool(
        isinstance(row.get("id"), str)
        and isinstance(row.get("model"), str)
        and row.get("verdict") in VERDICTS
        and isinstance(row.get("abstained"), bool)
        and isinstance(row.get("cited_clauses"), list)
        and all(isinstance(value, str) for value in row["cited_clauses"])
        and isinstance(row.get("quotes"), dict)
        and isinstance(row.get("reason"), str)
    )


def evaluate(golden: list[dict], predictions: list[dict]) -> dict[str, dict]:
    by_id = {row["id"]: row for row in golden}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in predictions:
        grouped[str(row.get("model") or "<missing>")].append(row)

    results = {}
    for model, rows in sorted(grouped.items()):
        pred_by_id = {str(row.get("id")): row for row in rows}
        schema_ok = sum(_valid_prediction(row) for row in rows)
        verdict_ok = citation_ok = overreach = appropriate_abstain = over_abstain = 0
        actionable = abstain_expected = 0
        missing = []
        for case_id, gold in by_id.items():
            pred = pred_by_id.get(case_id)
            if pred is None or not _valid_prediction(pred):
                missing.append(case_id)
                continue
            expected = gold["expect"]
            verdict_ok += pred["verdict"] == expected["verdict"]
            allowed = set(expected.get("must_cite") or ())
            cited = set(pred["cited_clauses"])
            citation_ok += cited == allowed
            if expected["abstained"]:
                abstain_expected += 1
                appropriate_abstain += pred["abstained"] is True
                overreach += pred["verdict"] in {"likely_covered", "unlikely"}
            else:
                actionable += 1
                over_abstain += pred["abstained"] is True
        total = len(golden)
        results[model] = {
            "cases": total,
            "predictions": len(rows),
            "schema_compliance": schema_ok / total if total else 0.0,
            "verdict_accuracy": verdict_ok / total if total else 0.0,
            "citation_alignment": citation_ok / total if total else 0.0,
            "grounding_overreach_rate": overreach / abstain_expected if abstain_expected else 0.0,
            "appropriate_abstention_rate": appropriate_abstain / abstain_expected if abstain_expected else 0.0,
            "over_abstention_rate": over_abstain / actionable if actionable else 0.0,
            "missing_or_invalid_ids": missing,
        }
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=Path("tests/golden/precheck_ai2_v1.yaml"))
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    predictions = [
        json.loads(line) for line in args.predictions.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    result = evaluate(golden, predictions)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result and all(not row["missing_or_invalid_ids"] for row in result.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
