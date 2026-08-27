"""사람이 승인한 보험 수치 사실과 보수적인 계산 규칙."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.errors import ValidationErr


@dataclass(frozen=True)
class SelfPayFact:
    policy_version_sha: str
    candidate_id: str
    plan: str
    services: tuple[str, ...]
    institution: str
    coverage: tuple[str, ...]
    formula: str
    amount_tokens: tuple[str, ...]
    rate_tokens: tuple[str, ...]
    page: int
    content_hash: str
    approval: str


@dataclass(frozen=True)
class SelfPayCalculation:
    eligible_expense_won: int
    deductible_won: int
    fixed_amount_won: int | None
    rate_percent: float | None
    formula: str


@dataclass(frozen=True)
class DisabilityRateFact:
    """사람 승인 여부와 출처 경계를 함께 가진 장해지급률 사실."""

    policy_version_sha: str
    candidate_id: str
    classification: str
    payment_rate_percent: float
    ordinal: int
    page: int
    content_hash: str
    approval: str
    serving_eligible: bool
    citation_eligible: bool


_MONEY = re.compile(r"^(\d+)(?:만(\d+)천|만|천)?원$")
_RATE = re.compile(r"^(\d+(?:\.\d+)?)%$")


def parse_won(token: str) -> int:
    text = re.sub(r"\s+", "", token or "")
    match = _MONEY.fullmatch(text)
    if not match:
        raise ValidationErr(f"지원하지 않는 금액 표기입니다: {token}")
    first = int(match.group(1))
    if "만" in text:
        return first * 10_000 + int(match.group(2) or 0) * 1_000
    if "천" in text:
        return first * 1_000
    return first


def calculate_self_pay(fact: SelfPayFact, *, eligible_expense_won: int) -> SelfPayCalculation:
    """승인 사실의 명시적 공식만 계산한다. 모르는 공식은 추측하지 않는다."""

    if eligible_expense_won < 0:
        raise ValidationErr("보상대상 의료비는 0원 이상이어야 합니다.")
    amounts = [parse_won(token) for token in fact.amount_tokens]
    rates = []
    for token in fact.rate_tokens:
        match = _RATE.fullmatch(re.sub(r"\s+", "", token))
        if not match:
            raise ValidationErr(f"지원하지 않는 비율 표기입니다: {token}")
        rates.append(float(match.group(1)))
    if len(amounts) > 1 or len(rates) > 1:
        raise ValidationErr("한 사실에 금액이나 비율이 여러 개라 자동 계산하지 않습니다.")

    fixed = amounts[0] if amounts else None
    rate = rates[0] if rates else None
    proportional = round(eligible_expense_won * rate / 100) if rate is not None else None
    compact = re.sub(r"\s+", "", fact.formula)
    if fixed is not None and proportional is not None and "큰금액" in compact:
        deductible = max(fixed, proportional)
    elif fixed is not None and proportional is not None and "작은금액" in compact:
        deductible = min(fixed, proportional)
    elif fixed is not None and rate is None:
        deductible = fixed
    elif proportional is not None and fixed is None:
        deductible = proportional
    else:
        raise ValidationErr("승인 사실에 계산 가능한 자기부담금 공식이 없습니다.")
    return SelfPayCalculation(
        eligible_expense_won=eligible_expense_won,
        deductible_won=min(deductible, eligible_expense_won),
        fixed_amount_won=fixed,
        rate_percent=rate,
        formula=fact.formula,
    )


__all__ = [
    "DisabilityRateFact",
    "SelfPayCalculation",
    "SelfPayFact",
    "calculate_self_pay",
    "parse_won",
]
