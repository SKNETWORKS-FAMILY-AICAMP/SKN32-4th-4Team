"""합성 계약 골든 20건을 현행 규칙 판정·결정적 설명으로 실행한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.domain.precheck_result import PrecheckInput
from app.core.ports.precheck import ClauseRow
from app.core.usecases.assess import ASSESSOR_VERSION, assess, explain
from app.core.usecases.retrieval import EvidenceBundleV1


SHA = "a" * 64


def _row(text: str, *, sha: str = SHA, eligible: bool = True) -> ClauseRow:
    return ClauseRow(
        sha256=sha,
        qualified_no="보통약관/제1조",
        clause_no="제1조",
        section="보통약관",
        title="",
        text=text,
        page_from=1,
        page_to=1,
        content_hash="1" * 64,
        citation_eligible=eligible,
        chunk_type="clause",
        parse_status="ok",
    )


def _bundle(case: dict) -> EvidenceBundleV1:
    case_id = case["id"]
    code = (case.get("request", {}).get("kcd_codes") or [""])[0]
    if case_id.startswith("excluded"):
        rows = [_row(f"{code} 질병의 치료비는 보상하지 않습니다.")]
    elif case_id.startswith("exception"):
        rows = [_row(f"A00~Z99 질병은 보상하지 않습니다. 다만 {code} 치료비는 보상합니다.")]
    elif case_id == "unreliable-01":
        rows = [_row(f"{code} 질병은 보상하지 않습니다.", sha="b" * 64)]
    elif case_id == "unreliable-02":
        rows = [_row(f"{code} 질병은 보상하지 않습니다.", eligible=False)]
    else:
        rows = []
    return EvidenceBundleV1(policy_version_sha=SHA, clauses=rows)


def run_cases(golden: list[dict]) -> list[dict]:
    predictions = []
    for case in golden:
        request = PrecheckInput(
            insurer="합성보험사",
            enrolled_on="20200101",
            kcd_codes=tuple(case.get("request", {}).get("kcd_codes") or ()),
            product_name="계약 시험용",
        )
        bundle = _bundle(case)
        assessment = assess(bundle, request)
        result = explain(assessment, bundle)
        predictions.append({
            "id": case["id"],
            "model": f"{ASSESSOR_VERSION}+deterministic-explain",
            "verdict": result.verdict.value,
            "abstained": result.abstained,
            "cited_clauses": list(result.cited_clauses),
            "quotes": result.quotes,
            "reason": result.reason,
        })
    return predictions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=Path("tests/golden/precheck_ai2_v1.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    rows = run_cases(golden)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps({"cases": len(rows), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
