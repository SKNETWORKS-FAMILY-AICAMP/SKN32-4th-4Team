"""확정 약관 SHA 이후의 AI1 검색 → AI2 판정 → 설명 검증 파이프라인."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.core.domain.precheck_result import PrecheckInput
from app.core.usecases.assess import (
    ExplainedResultV1,
    ExplanationGeneratorPort,
    RuleAssessmentV1,
    assess,
    explain,
    explain_generated,
)
from app.core.usecases.retrieval import EvidenceBundleV1, retrieve


@dataclass(frozen=True)
class EvidencePrecheckResultV1:
    evidence: EvidenceBundleV1
    assessment: RuleAssessmentV1
    explanation: ExplainedResultV1


def run(
    *,
    policy_version_sha: str,
    request: PrecheckInput,
    clauses,
    question: str | None = None,
    top_k: int = 8,
    semantic_search: Callable[..., object] | None = None,
    explanation_generator: ExplanationGeneratorPort | None = None,
) -> EvidencePrecheckResultV1:
    """이미 확정된 약관 한 벌 안에서만 검색·판정·설명을 수행한다."""

    bundle = retrieve(
        policy_version_sha=policy_version_sha,
        kcd_codes=list(request.kcd_codes),
        question=question,
        top_k=top_k,
        clauses=clauses,
        semantic_search=semantic_search,
    )
    assessment = assess(bundle, request)
    explanation = (
        explain_generated(assessment, bundle, generator=explanation_generator)
        if explanation_generator is not None and not assessment.abstained
        else explain(assessment, bundle)
    )
    return EvidencePrecheckResultV1(bundle, assessment, explanation)


__all__ = ["EvidencePrecheckResultV1", "run"]
