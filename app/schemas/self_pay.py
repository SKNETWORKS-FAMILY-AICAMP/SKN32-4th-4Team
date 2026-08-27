"""자기부담금 조회 API 계약 (v1).

★사람이 승인한 표 사실만 계산한다(`app/core/usecases/self_pay.py`). 후보가
  여럿이면 하나를 추측하지 않고 `ambiguous=true`로 정직하게 답한다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SelfPayRequest(BaseModel):
    """자기부담금 조회·계산 요청."""

    policy_version_sha: str = Field(min_length=64, max_length=64, description="약관 판본 SHA-256(64자)")
    plan: str = Field(min_length=1, description="가입유형. 예: 표준형")
    service: str = Field(min_length=1, description="의료서비스. 예: 외래")
    institution: str | None = Field(default=None, description="의료기관 구분(있으면 좁힌다)")
    coverage: str | None = Field(default=None, description="급여구분(있으면 좁힌다)")
    eligible_expense_won: int = Field(ge=0, description="보상대상 의료비(원)")


class SelfPayCandidate(BaseModel):
    """조회로 찾은 승인 사실 하나(값을 확정하지 못했을 때 후보로 보여준다)."""

    candidate_id: str
    institution: str
    coverage: tuple[str, ...]
    formula: str
    page: int


class SelfPayResponse(BaseModel):
    """자기부담금 조회·계산 결과.

    ★`found=false`거나 `ambiguous=true`면 `deductible_won`은 `null`이다 —
      모르면 모른다고 한다(CLAUDE.md §0). 후보가 있으면 `candidates`에 담아
      좁혀 재조회할 실마리만 준다.
    """

    policy_version_sha: str
    found: bool
    ambiguous: bool
    reason: str
    candidates: list[SelfPayCandidate] = Field(default_factory=list)
    eligible_expense_won: int | None = None
    deductible_won: int | None = None
    formula: str | None = None


__all__ = ["SelfPayCandidate", "SelfPayRequest", "SelfPayResponse"]
