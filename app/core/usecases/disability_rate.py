"""사람이 승인한 장해분류만 정확히 일치시켜 지급률을 찾는다."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.domain.benefit_facts import DisabilityRateFact
from app.core.errors import ValidationErr
from app.core.ports.benefit_facts import DisabilityRateSourcePort


_APPROVED = {"accepted", "approved", "human_pattern_approved"}


def _norm(value: str) -> str:
    return re.sub(r"[\s·ㆍ]+", "", value or "").lower()


@dataclass(frozen=True)
class DisabilityRateLookupV1:
    policy_version_sha: str
    classification: str
    matches: tuple[DisabilityRateFact, ...]
    payment_rate_percent: float | None
    ambiguous: bool
    blocked_candidates: int
    reason: str


def lookup(
    *,
    policy_version_sha: str,
    classification: str,
    source: DisabilityRateSourcePort,
) -> DisabilityRateLookupV1:
    """같은 약관의 승인된 정확 일치만 반환하고 후보·충돌은 추측하지 않는다."""

    sha = (policy_version_sha or "").strip().lower()
    label = (classification or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", sha) or not label:
        raise ValidationErr("64자리 약관 SHA와 장해분류 문구가 필요합니다.")

    loaded = source.load_for_policy(sha)
    target = _norm(label)
    exact = [fact for fact in loaded if _norm(fact.classification) == target]
    approved = [
        fact
        for fact in exact
        if fact.approval.lower() in _APPROVED
        and fact.serving_eligible is True
        and fact.citation_eligible is True
        and 0 < fact.payment_rate_percent <= 100
    ]
    blocked = len(exact) - len(approved)
    rates = {fact.payment_rate_percent for fact in approved}
    ambiguous = len(rates) > 1
    rate = next(iter(rates)) if len(rates) == 1 else None

    if ambiguous:
        reason = "같은 장해분류에 서로 다른 승인 지급률이 있어 전문가 확인이 필요합니다."
    elif rate is not None:
        reason = "같은 약관에서 사람이 승인한 장해분류와 지급률을 찾았습니다."
    elif blocked:
        reason = "추출 후보는 있지만 사람 승인 전이라 지급률로 사용하지 않습니다."
    else:
        reason = "승인된 장해분류를 정확히 찾지 못했습니다. 비슷한 문구로 추측하지 않습니다."
    return DisabilityRateLookupV1(
        policy_version_sha=sha,
        classification=label,
        matches=tuple(approved),
        payment_rate_percent=rate,
        ambiguous=ambiguous,
        blocked_candidates=blocked,
        reason=reason,
    )


__all__ = ["DisabilityRateLookupV1", "lookup"]
