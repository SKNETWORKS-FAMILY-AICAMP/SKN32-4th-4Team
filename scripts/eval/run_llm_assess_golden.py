"""합성 AI2 골든을 현재 설정의 설명 LLM에 보내 원시 구조 출력을 수집한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.adapters.llm_assessment_explainer import build_active_explainer
from app.core.config import get_settings
from app.core.domain.precheck_result import PrecheckInput
from app.core.usecases.assess import assess, explain
from scripts.eval.run_assess_golden import _bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=Path("tests/golden/precheck_ai2_v1.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    settings = get_settings()
    generator = build_active_explainer(settings)
    model_label = f"{settings.LLM_PROVIDER}:{generator.model}"
    rows = []
    for case in golden:
        request = PrecheckInput(
            insurer="합성보험사",
            enrolled_on="20200101",
            kcd_codes=tuple(case.get("request", {}).get("kcd_codes") or ()),
            product_name="계약 시험용",
        )
        bundle = _bundle(case)
        assessment = assess(bundle, request)
        try:
            draft = generator.generate(assessment, bundle)
            guarded = explain(assessment, bundle, draft=draft)
            row = {
                "id": case["id"],
                "model": model_label,
                "verdict": draft.verdict.value,
                "abstained": draft.abstained,
                "cited_clauses": list(draft.cited_clauses),
                "quotes": draft.quotes,
                "reason": draft.reason,
                "guard_accepted": (
                    guarded.verdict is assessment.verdict
                    and guarded.abstained == assessment.abstained
                ),
                "guard_reason_code": guarded.reason_code.value,
            }
        except Exception as exc:  # 평가 수집: 한 건 실패가 나머지 19건을 지우지 않게 기록한다.
            row = {
                "id": case["id"],
                "model": model_label,
                "generation_error": type(exc).__name__,
            }
        rows.append(row)
        print(json.dumps({"done": len(rows), "total": len(golden), "id": case["id"]}, ensure_ascii=False))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
