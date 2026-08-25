"""확정 약관의 승인된 자기부담금 사실을 축별로 찾고 계산한다."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.domain.benefit_facts import SelfPayCalculation, SelfPayFact, calculate_self_pay
from app.core.errors import ValidationErr
from app.core.ports.benefit_facts import SelfPayFactSourcePort


@dataclass(frozen=True)
class SelfPayLookupV1:
    policy_version_sha: str
    matches: tuple[SelfPayFact, ...]
    ambiguous: bool
    reason: str


def _norm(value: str) -> str:
    return "".join((value or "").split()).lower()


def lookup(
    *,
    policy_version_sha: str,
    plan: str,
    service: str,
    source: SelfPayFactSourcePort,
    institution: str | None = None,
    coverage: str | None = None,
) -> SelfPayLookupV1:
    """필수 축을 정확히 좁힌다. 후보가 여럿이면 하나를 추측하지 않는다."""

    if len(policy_version_sha) != 64 or not plan.strip() or not service.strip():
        raise ValidationErr("64자리 약관 SHA, 가입유형(plan), 의료서비스(service)가 필요합니다.")
    facts = source.load_for_policy(policy_version_sha.lower())
    matches = [
        fact for fact in facts
        if _norm(fact.plan) == _norm(plan)
        and _norm(service) in {_norm(value) for value in fact.services}
    ]
    if institution:
        needle = _norm(institution)
        matches = [fact for fact in matches if needle in _norm(fact.institution)]
    if coverage:
        matches = [
            fact for fact in matches
            if _norm(coverage) in {_norm(value) for value in fact.coverage}
        ]
    distinct = {
        (fact.formula, fact.institution, fact.coverage) for fact in matches
    }
    ambiguous = len(distinct) > 1
    if not matches:
        reason = "승인된 자기부담금 사실을 찾지 못했습니다. 다른 약관으로 대신하지 않습니다."
    elif ambiguous:
        reason = "조건에 맞는 공식이 여러 개라 의료기관·급여구분을 더 확인해야 합니다."
    else:
        reason = "사람이 승인한 약관 표 사실을 정확히 하나 찾았습니다."
    return SelfPayLookupV1(
        policy_version_sha=policy_version_sha.lower(),
        matches=tuple(matches),
        ambiguous=ambiguous,
        reason=reason,
    )


def calculate(result: SelfPayLookupV1, *, eligible_expense_won: int) -> SelfPayCalculation:
    if result.ambiguous or len(result.matches) != 1:
        raise ValidationErr("자기부담금 공식을 정확히 하나로 확정한 뒤 계산해야 합니다.")
    return calculate_self_pay(result.matches[0], eligible_expense_won=eligible_expense_won)


__all__ = ["SelfPayLookupV1", "calculate", "lookup"]
