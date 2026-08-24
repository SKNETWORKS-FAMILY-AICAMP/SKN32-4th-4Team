"""증빙 검수 상태 전이 — 이 프로젝트의 핵심 불변식.

★ 왜 상태를 나누는가

    초안은 "형식·금액·중복 검사 통과 → 지급결과 확정"이었다. **이는 잘못이다.**
    그 검사가 확인하는 것은 **제출 문서의 내부 정합성**일 뿐,
    **실제로 보험사가 그렇게 결정했는지**가 아니다. 정합성이 맞는 위조 통지서는 통과한다.

    정합성 검증 ≠ 사실성 검증.

이 규칙은 문서가 아니라 코드로 강제한다. 담당자 ①이 소유하며,
전이 규칙 변경은 ①의 승인 없이 하지 않는다.
"""

from __future__ import annotations

from enum import Enum

from app.core.errors import ValidationErr


class EvidenceStatus(str, Enum):
    """증빙 상태. 오직 ``VERIFIED`` 만 코호트 집계에 반영된다."""

    #: 제출만 됨. 아무것도 확인되지 않았다.
    SUBMITTED = "submitted"
    #: 문서 내부 정합성(형식·금액 일치·중복 아님)만 통과. **사실 확인이 아니다.**
    CONSISTENT = "consistent"
    #: 발행처 확인·원본성 검증 또는 관리자 교차검증까지 마침.
    VERIFIED = "verified"
    #: 정합성 실패 또는 진위 확인 실패.
    REJECTED = "rejected"


#: 집계에 반영되는 유일한 상태.
COUNTABLE: frozenset[EvidenceStatus] = frozenset({EvidenceStatus.VERIFIED})

#: 더 이상 움직이지 않는 상태.
TERMINAL: frozenset[EvidenceStatus] = frozenset(
    {EvidenceStatus.VERIFIED, EvidenceStatus.REJECTED}
)

#: 허용된 전이. 여기 없는 전이는 전부 오류다.
#: ★ SUBMITTED -> VERIFIED 직행이 없다는 점이 핵심이다.
_ALLOWED: frozenset[tuple[EvidenceStatus, EvidenceStatus]] = frozenset(
    {
        (EvidenceStatus.SUBMITTED, EvidenceStatus.CONSISTENT),
        (EvidenceStatus.SUBMITTED, EvidenceStatus.REJECTED),
        (EvidenceStatus.CONSISTENT, EvidenceStatus.VERIFIED),
        (EvidenceStatus.CONSISTENT, EvidenceStatus.REJECTED),
    }
)


def counts_toward_statistics(status: EvidenceStatus) -> bool:
    """이 상태의 증빙이 통계에 반영되는가."""
    return status in COUNTABLE


def transition(
    current: EvidenceStatus,
    target: EvidenceStatus,
    *,
    verification_method: str | None = None,
    reviewer_id: str | None = None,
) -> EvidenceStatus:
    """상태를 옮긴다. 허용되지 않으면 조용히 무시하지 않고 실패시킨다.

    Args:
        verification_method: ``VERIFIED``로 갈 때 **필수**. 무엇으로 검증했는지
            남기지 않고 "검증했다"고 주장할 수 없다(예: ``admin_review``).
        reviewer_id: ``VERIFIED``로 갈 때 **필수**. 누가 승격시켰는지가 감사로그의 핵심이다.

    Raises:
        ValidationErr: 허용되지 않은 전이이거나, 검증 근거가 빠진 경우.
    """
    if current in TERMINAL:
        raise ValidationErr(f"종료 상태에서는 전이할 수 없습니다: {current.value} -> {target.value}")

    if (current, target) not in _ALLOWED:
        raise ValidationErr(
            f"허용되지 않은 상태 전이입니다: {current.value} -> {target.value}. "
            "정합성 검사만으로 verified 로 갈 수 없습니다."
        )

    if target is EvidenceStatus.VERIFIED:
        if not verification_method:
            raise ValidationErr(
                "verified 로 승격하려면 verification_method 가 필요합니다. "
                "무엇으로 검증했는지 밝히지 않고 검증했다고 할 수 없습니다."
            )
        if not reviewer_id:
            raise ValidationErr("verified 로 승격하려면 reviewer_id 가 필요합니다.")

    return target
