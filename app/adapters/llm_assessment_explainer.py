"""OpenAI 호환 채팅 모델로 AI2 설명 초안만 만든다."""

from __future__ import annotations

import json

from app.core.domain.insurance import Verdict
from app.core.errors import InfraError, LLMOutputError
from app.core.llm_clients import get_active_model, get_chat_client
from app.core.usecases.assess import ExplanationDraftV1, RuleAssessmentV1
from app.core.usecases.retrieval import EvidenceBundleV1


_EXPLANATION_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": [
                "likely_covered",
                "unlikely",
                "needs_documents",
                "needs_expert",
            ],
        },
        "abstained": {"type": "boolean"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "handle": {"type": "string", "pattern": "^E[0-9]{3,4}$"},
                    "quote": {"type": "string"},
                },
                "required": ["handle", "quote"],
                "additionalProperties": False,
            },
        },
        "reason": {"type": "string"},
    },
    "required": ["verdict", "abstained", "citations", "reason"],
    "additionalProperties": False,
}


class OpenAICompatibleAssessmentExplainer:
    def __init__(self, *, client, model: str) -> None:
        if not model.strip():
            raise ValueError("model must not be empty")
        self.client = client
        self.model = model

    def generate(
        self,
        assessment: RuleAssessmentV1,
        bundle: EvidenceBundleV1,
    ) -> ExplanationDraftV1:
        by_id = {row.clause_id: row for row in bundle.clauses}
        evidence = []
        for index, clause_id in enumerate(assessment.cited_clause_ids, 1):
            row = by_id.get(clause_id)
            if row is None:
                raise InfraError(f"설명 생성용 근거가 사라졌습니다: {clause_id}")
            evidence.append({
                "handle": f"E{index:03d}",
                "qualified_no": row.qualified_no,
                "text": row.text,
            })
        payload = {
            "rule_verdict": assessment.verdict.value,
            "abstained": assessment.abstained,
            "per_code": [
                {"code": row.code, "status": row.status, "note": row.note}
                for row in assessment.per_code
            ],
            "evidence": evidence,
        }
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "insurance_assessment_explanation",
                    "strict": True,
                    "schema": _EXPLANATION_SCHEMA,
                },
            },
            messages=[
                {
                    "role": "system",
                    "content": (
                        "당신은 보험 약관 판정을 바꾸지 않고 쉬운 설명만 씁니다. "
                        "rule_verdict를 그대로 반환하고, evidence의 handle만 인용하세요. "
                        "각 citations 항목의 quote는 해당 evidence.text의 실제 일부여야 합니다. "
                        "제공된 evidence가 있으면 그 handle과 실제 문구를 citations에 넣으세요."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        try:
            content = response.choices[0].message.content
            parsed = json.loads(content or "")
            verdict = Verdict(parsed["verdict"])
            abstained = parsed["abstained"]
            citations = parsed["citations"]
            reason = parsed["reason"]
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMOutputError("설명 모델이 필수 JSON 스키마를 지키지 않았습니다.") from exc
        if (not isinstance(abstained, bool)
                or not isinstance(citations, list)
                or not all(
                    isinstance(value, dict)
                    and isinstance(value.get("handle"), str)
                    and isinstance(value.get("quote"), str)
                    for value in citations
                )
                or not isinstance(reason, str)):
            raise LLMOutputError("설명 모델의 JSON 필드 형식이 올바르지 않습니다.")
        cited = []
        quotes: dict[str, str | list[str]] = {}
        for citation in citations:
            handle = citation["handle"]
            quote = citation["quote"]
            cited.append(handle)
            previous = quotes.get(handle)
            if previous is None:
                quotes[handle] = quote
            elif isinstance(previous, list):
                previous.append(quote)
            else:
                quotes[handle] = [previous, quote]
        return ExplanationDraftV1(
            verdict=verdict,
            cited_clauses=tuple(cited),
            quotes=quotes,
            reason=reason,
            abstained=abstained,
        )


def build_active_explainer(settings=None) -> OpenAICompatibleAssessmentExplainer:
    return OpenAICompatibleAssessmentExplainer(
        client=get_chat_client(settings),
        model=get_active_model(settings),
    )


__all__ = ["OpenAICompatibleAssessmentExplainer", "build_active_explainer"]
