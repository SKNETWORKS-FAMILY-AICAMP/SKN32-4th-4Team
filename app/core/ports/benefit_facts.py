"""승인 수치 사실 저장소 포트."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.domain.benefit_facts import DisabilityRateFact, SelfPayFact


@runtime_checkable
class SelfPayFactSourcePort(Protocol):
    def load_for_policy(self, policy_version_sha: str) -> list[SelfPayFact]: ...


@runtime_checkable
class DisabilityRateSourcePort(Protocol):
    def load_for_policy(self, policy_version_sha: str) -> list[DisabilityRateFact]: ...


__all__ = ["DisabilityRateSourcePort", "SelfPayFactSourcePort"]
