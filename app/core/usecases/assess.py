"""AI2 규칙 판정과 근거 검증을 한 경계에서 제공한다.

검색(`retrieval`)은 근거만 고르고, 이 모듈이 결론을 소유한다. 설명 초안은 사람이
쓰거나 LLM이 만들 수 있지만 판정을 바꿀 수 없고, 인용 검증을 통과해야만 반환된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from app.core.domain import citation_guard as cg
from app.core.domain import kcd_ranges as kcd
from app.core.domain.insurance import Verdict
from app.core.domain.precheck_result import PrecheckInput, ReasonCode
from app.core.ports.precheck import ClauseRow
from app.core.usecases.retrieval import EvidenceBundleV1


ASSESSOR_VERSION = "rule-assessor-v1"
_POSITIVE_PAYMENT = re.compile(r"(?:보상|보장|지급)[\s\d]{0,12}(?:합니다|하여\s*드립니다)")
_NEGATIVE_PAYMENT = re.compile(r"(?:보상|보장|지급)하지[\s\d]{0,12}(?:않|아니)")


@dataclass(frozen=True)
class RuleCodeAssessmentV1:
    code: str
    verdict: Verdict
    reason_code: ReasonCode
    status: str
    cited_clause_ids: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class RuleAssessmentV1:
    policy_version_sha: str
    verdict: Verdict
    abstained: bool
    reason_code: ReasonCode
    per_code: tuple[RuleCodeAssessmentV1, ...] = ()
    cited_clause_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    assessor_version: str = ASSESSOR_VERSION


@dataclass(frozen=True)
class ExplanationDraftV1:
    """외부 설명 생성기가 내놓을 수 있는 제한된 초안."""

    verdict: Verdict
    cited_clauses: tuple[str, ...]
    quotes: dict[str, str | list[str]]
    reason: str
    abstained: bool = False


@dataclass(frozen=True)
class ExplainedResultV1:
    verdict: Verdict
    abstained: bool
    reason_code: ReasonCode
    cited_clauses: tuple[str, ...] = ()
    quotes: dict[str, str | list[str]] = field(default_factory=dict)
    reason: str = ""
    citation_reason: str = ""


def _distinct(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _abstained(
    *,
    sha: str,
    code: str,
    reason_code: ReasonCode,
    note: str,
) -> RuleAssessmentV1:
    row = RuleCodeAssessmentV1(
        code=code,
        verdict=Verdict.NEEDS_EXPERT,
        reason_code=reason_code,
        status="not_mentioned",
        note=note,
    )
    return RuleAssessmentV1(
        policy_version_sha=sha,
        verdict=Verdict.NEEDS_EXPERT,
        abstained=True,
        reason_code=reason_code,
        per_code=(row,) if code else (),
        warnings=(note,),
    )


def _explicit_coverage_rows(
    code: kcd.CodeRef,
    clauses: list[ClauseRow],
) -> list[ClauseRow]:
    """같은 문장에 코드와 명시적 지급 선언이 함께 있을 때만 긍정 근거로 본다."""

    matched: list[ClauseRow] = []
    for row in clauses:
        # KCD 세분류 `N39.3`의 점은 문장 경계가 아니다.
        for sentence in re.split(r"(?:(?<!\d)\.|\.(?!\d)|。|\n)", row.text or ""):
            if not sentence.strip() or _NEGATIVE_PAYMENT.search(sentence):
                continue
            mentions = kcd.scan_clause(sentence)
            if (_POSITIVE_PAYMENT.search(sentence)
                    and any(m.range.contains(code) and m.kind == "mention" for m in mentions)):
                matched.append(row)
                break
    return matched


def assess(bundle: EvidenceBundleV1, req: PrecheckInput) -> RuleAssessmentV1:
    """근거 묶음으로 KCD 규칙을 판정한다. LLM은 호출하지 않는다.

    검색 결과에 실수로 다른 문서나 인용 불가 조항이 섞이면 해당 행만 믿고 진행하지
    않고 전체 판정을 기권한다. ``not_mentioned``도 보장으로 올리지 않는다.
    """

    sha = bundle.policy_version_sha
    contaminated = [
        row for row in bundle.clauses
        if row.sha256 != sha
        or not row.usable
        or row.parse_status != "ok"
        or row.citation_eligible is not True
        or row.chunk_type == "page_fallback"
    ]
    if contaminated:
        return _abstained(
            sha=sha,
            code=req.kcd_codes[0] if req.kcd_codes else "",
            reason_code=ReasonCode.DOCUMENT_NOT_RELIABLE,
            note="근거 묶음에 다른 문서 또는 인용할 수 없는 조항이 섞여 판정을 중단했습니다.",
        )
    if not req.kcd_codes:
        return _abstained(
            sha=sha,
            code="",
            reason_code=ReasonCode.INVALID_CODE,
            note="판정할 질병기호가 없습니다.",
        )

    scanned: list[tuple[kcd.CodeMention, ClauseRow]] = []
    for row in bundle.clauses:
        scanned.extend((mention, row) for mention in kcd.scan_clause(row.text))

    per_code: list[RuleCodeAssessmentV1] = []
    for raw_code in req.kcd_codes:
        parsed = kcd.CodeRef.parse(raw_code)
        if parsed is None:
            per_code.append(RuleCodeAssessmentV1(
                code=raw_code,
                verdict=Verdict.NEEDS_EXPERT,
                reason_code=ReasonCode.INVALID_CODE,
                status="invalid_code",
                note="질병기호 형식이 아닙니다.",
            ))
            continue

        hits = [(mention, row) for mention, row in scanned if mention.range.contains(parsed)]
        judged = kcd.judge(str(parsed), [mention for mention, _ in hits])
        status = judged["status"]
        if status == "excluded":
            verdict = Verdict.UNLIKELY
            reason = ReasonCode.EXCLUDED_BY_CLAUSE
            kinds = {"exclude"}
            note = "약관의 면책 규칙에 해당합니다."
        elif status == "exception":
            verdict = Verdict.NEEDS_DOCUMENTS
            reason = ReasonCode.EXCEPTION_APPLIES
            kinds = {"exclude", "exception"}
            note = "면책 예외 조건이 있어 급여 여부 등 추가 서류가 필요합니다."
        else:
            coverage_rows = _explicit_coverage_rows(parsed, bundle.clauses)
            if coverage_rows:
                verdict = Verdict.LIKELY_COVERED
                reason = ReasonCode.COVERED_BY_CLAUSE
                kinds = set()
                note = "같은 문장에 질병기호와 명시적인 지급 선언이 있습니다."
            else:
                verdict = Verdict.NEEDS_EXPERT
                reason = ReasonCode.NO_EVIDENCE
                kinds = set()
                note = "면책 목록에 없다는 사실만으로 보장된다고 판단할 수 없습니다."
        cited = (
            _distinct(row.clause_id for row in coverage_rows)
            if status == "not_mentioned" and verdict is Verdict.LIKELY_COVERED
            else _distinct(row.clause_id for mention, row in hits if mention.kind in kinds)
        )
        # 결론을 냈는데 그 결론을 가리키는 조항 ID가 없으면 결론도 버린다.
        if verdict in (Verdict.LIKELY_COVERED, Verdict.UNLIKELY, Verdict.NEEDS_DOCUMENTS) and not cited:
            verdict = Verdict.NEEDS_EXPERT
            reason = ReasonCode.NO_EVIDENCE
            status = "not_mentioned"
            note = "판정 규칙은 찾았지만 정확한 근거 조항을 특정하지 못했습니다."
        per_code.append(RuleCodeAssessmentV1(
            code=str(parsed),
            verdict=verdict,
            reason_code=reason,
            status=status,
            cited_clause_ids=cited,
            note=note,
        ))

    verdicts = {row.verdict for row in per_code}
    if Verdict.UNLIKELY in verdicts:
        overall, reason = Verdict.UNLIKELY, ReasonCode.EXCLUDED_BY_CLAUSE
    elif Verdict.NEEDS_DOCUMENTS in verdicts:
        overall, reason = Verdict.NEEDS_DOCUMENTS, ReasonCode.EXCEPTION_APPLIES
    elif per_code and all(row.verdict is Verdict.LIKELY_COVERED for row in per_code):
        overall, reason = Verdict.LIKELY_COVERED, ReasonCode.COVERED_BY_CLAUSE
    else:
        overall = Verdict.NEEDS_EXPERT
        reason = (
            ReasonCode.INVALID_CODE
            if per_code and all(row.reason_code is ReasonCode.INVALID_CODE for row in per_code)
            else ReasonCode.NO_EVIDENCE
        )
    cited = _distinct(
        clause_id for row in per_code for clause_id in row.cited_clause_ids
    )
    return RuleAssessmentV1(
        policy_version_sha=sha,
        verdict=overall,
        abstained=overall is Verdict.NEEDS_EXPERT,
        reason_code=reason,
        per_code=tuple(per_code),
        cited_clause_ids=cited,
        warnings=tuple(bundle.unresolved_references) + tuple(bundle.truncated),
    )


def _default_draft(
    assessment: RuleAssessmentV1,
    handles: list[cg.EvidenceClause],
) -> ExplanationDraftV1:
    cited = tuple(handle.handle for handle in handles)
    quotes = {handle.handle: _quote_for_row(handle.text) for handle in handles}
    if assessment.verdict is Verdict.UNLIKELY:
        lead = "약관의 면책 규칙과 일치해 지급이 어려울 가능성이 있습니다."
    elif assessment.verdict is Verdict.NEEDS_DOCUMENTS:
        lead = "면책 예외 조건이 확인되어 추가 서류로 조건을 확인해야 합니다."
    elif assessment.verdict is Verdict.LIKELY_COVERED:
        lead = "약관에 질병기호와 지급 선언이 함께 있어 보장 가능성이 있습니다."
    else:
        lead = "현재 근거만으로 보장 여부를 확정할 수 없어 전문가 확인이 필요합니다."
    excerpts = " ".join(f"근거 {handle}: “{quotes[handle]}”" for handle in cited)
    return ExplanationDraftV1(
        verdict=assessment.verdict,
        cited_clauses=cited,
        quotes=quotes,
        reason=(f"{lead} {excerpts}" if excerpts else lead),
    )


def _quote_for_row(text: str) -> str:
    return " ".join((text or "").split())[:300]


def explain(
    assessment: RuleAssessmentV1,
    bundle: EvidenceBundleV1,
    *,
    draft: ExplanationDraftV1 | None = None,
) -> ExplainedResultV1:
    """설명 초안을 검증해 반환한다. 실패한 초안은 버리고 기권한다."""

    if assessment.abstained or not assessment.cited_clause_ids:
        return ExplainedResultV1(
            verdict=Verdict.NEEDS_EXPERT,
            abstained=True,
            reason_code=assessment.reason_code,
            reason="현재 근거만으로 보장 여부를 확정할 수 없어 전문가 확인이 필요합니다.",
        )

    by_id: dict[str, list[ClauseRow]] = {}
    for row in bundle.clauses:
        by_id.setdefault(row.clause_id, []).append(row)
    selected: list[ClauseRow] = []
    for clause_id in assessment.cited_clause_ids:
        candidates = by_id.get(clause_id, [])
        if len(candidates) != 1:
            return ExplainedResultV1(
                verdict=Verdict.NEEDS_EXPERT,
                abstained=True,
                reason_code=ReasonCode.CITATION_UNVERIFIED,
                reason="근거 조항을 정확히 하나로 특정할 수 없어 설명을 폐기했습니다.",
                citation_reason=f"{clause_id}: 후보 {len(candidates)}개",
            )
        selected.append(candidates[0])

    evidence = cg.make_handles([
        cg.EvidenceClause(
            qualified_no=row.qualified_no,
            text=row.text,
            clause_id=row.clause_id,
        )
        for row in selected
    ])
    candidate = draft or _default_draft(assessment, evidence)
    if candidate.verdict is not assessment.verdict or candidate.abstained:
        return ExplainedResultV1(
            verdict=Verdict.NEEDS_EXPERT,
            abstained=True,
            reason_code=ReasonCode.CITATION_UNVERIFIED,
            reason="설명 초안이 규칙 판정을 바꾸려 해 폐기했습니다.",
            citation_reason=("draft_abstained" if candidate.abstained else "verdict_mismatch"),
        )
    result = cg.verify(
        cited_clauses=list(candidate.cited_clauses),
        evidence=evidence,
        answer_text=candidate.reason,
        quotes=candidate.quotes,
    )
    if not result.ok:
        reason = (
            ReasonCode.AMBIGUOUS_CITATION
            if result.reason_code == "ambiguous_citation"
            else ReasonCode.CITATION_UNVERIFIED
        )
        return ExplainedResultV1(
            verdict=Verdict.NEEDS_EXPERT,
            abstained=True,
            reason_code=reason,
            reason="설명의 인용을 확인할 수 없어 초안을 폐기했습니다.",
            citation_reason=result.reason,
        )
    return ExplainedResultV1(
        verdict=assessment.verdict,
        abstained=False,
        reason_code=assessment.reason_code,
        cited_clauses=candidate.cited_clauses,
        quotes=candidate.quotes,
        reason=candidate.reason,
    )


class ExplanationGeneratorPort(Protocol):
    def generate(
        self,
        assessment: RuleAssessmentV1,
        bundle: EvidenceBundleV1,
    ) -> ExplanationDraftV1: ...


def explain_generated(
    assessment: RuleAssessmentV1,
    bundle: EvidenceBundleV1,
    *,
    generator: ExplanationGeneratorPort,
) -> ExplainedResultV1:
    """주입된 설명 생성기를 호출한 뒤 같은 fail-closed 검증을 적용한다.

    생성기 장애나 형식 오류를 고정 문구로 숨기지 않는다. 호출자가 장애로 처리할 수
    있도록 예외를 그대로 올리고, 정상 초안만 :func:`explain`에 넘긴다.
    """

    draft = generator.generate(assessment, bundle)
    return explain(assessment, bundle, draft=draft)


__all__ = [
    "ASSESSOR_VERSION",
    "ExplainedResultV1",
    "ExplanationGeneratorPort",
    "ExplanationDraftV1",
    "RuleAssessmentV1",
    "RuleCodeAssessmentV1",
    "assess",
    "explain",
    "explain_generated",
]
