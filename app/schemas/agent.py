"""등록 외부 에이전트 전용 strict HTTP 계약."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentPrecheckRequest(_StrictModel):
    insurer: str = Field(min_length=1, max_length=100)
    enrolled_on: str = Field(pattern=r"^[0-9]{8}$")
    incident_on: str | None = Field(default=None, pattern=r"^[0-9]{8}$")
    kcd_codes: list[str] = Field(min_length=1, max_length=20)
    product_name: str | None = Field(default=None, max_length=200)
    consent_purpose: str | None = Field(default=None, min_length=1, max_length=100)


class AgentTermRequest(_StrictModel):
    message: str = Field(min_length=1, max_length=500)
    insurer: str | None = Field(default=None, max_length=100)


class AgentObservationRequest(_StrictModel):
    insurer: str = Field(min_length=1, max_length=100)
    enrolled_on: str = Field(default="", pattern=r"^$|^[0-9]{8}$")
    kcd_codes: list[str] = Field(min_length=1, max_length=20)
    product_id: str = Field(default="", max_length=200)
    age_band: str | None = Field(default=None, max_length=40)
    outcome: Literal["paid", "denied", "partial", "pending"]
    outcome_reason: str = Field(default="", max_length=1000)
    precheck_trace_id: str | None = Field(default=None, max_length=128)
    precheck_idempotency_key: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]{7,127}$",
    )
    claimed_on: str | None = Field(default=None, pattern=r"^[0-9]{8}$")
    decided_on: str | None = Field(default=None, pattern=r"^[0-9]{8}$")
    claimed_amount: Decimal | None = Field(default=None, ge=0)
    paid_amount: Decimal | None = Field(default=None, ge=0)
    evidence_doc_type: str | None = Field(default=None, max_length=100)
    evidence_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-fA-F]{64}$"
    )
    evidence_stored_ref: str | None = Field(default=None, max_length=2048)


class ObservationReceipt(_StrictModel):
    schema_version: Literal["v1"] = "v1"
    accepted: Literal[True] = True
    stored: bool
    duplicate: bool
    replayed: bool
    submission_id: str
    verification: Literal["unverified"] = "unverified"
    trace_id: str
    note: str


class AgentCohortResponse(_StrictModel):
    schema_version: str
    data_source: Literal["verified_real"]
    n: int
    approved_n: int
    denied_n: int
    min_sample: int
    min_sample_met: bool
    approval_rate: float | None
    approval_ci: list[float] | None
    headline: str
    by_verification: dict[str, int]
    warnings: list[str]


class AgentSupportInsurer(_StrictModel):
    versions: int
    generations: list[int]
    product_lines: list[str]
    sale_start_range: list[str]


class AgentSupportManifestResponse(_StrictModel):
    schema_version: str
    rule_engine_version: str
    require_confirmed_documents: bool
    identification_mode: dict[str, Any]
    total_policy_versions: int
    confirmation: dict[str, Any]
    insurers: dict[str, AgentSupportInsurer]
    notes: list[str]


class AgentTermQuote(_StrictModel):
    quote: str
    kind: str
    insurer: str
    title: str
    locator: str


class AgentLlmStatus(_StrictModel):
    used: bool
    provider: str | None
    model: str | None


class AgentTermResponse(_StrictModel):
    schema_version: str
    intent: str
    message: str
    next_action: str
    term: str | None
    found: bool
    quotes: list[AgentTermQuote]
    total_passages: int
    insurers: list[str]
    warnings: list[str]
    llm: AgentLlmStatus


class AgentErrorResponse(_StrictModel):
    ok: Literal[False] = False
    error_code: str
    message: str


__all__ = [
    "AgentCohortResponse",
    "AgentObservationRequest",
    "AgentPrecheckRequest",
    "AgentErrorResponse",
    "AgentSupportManifestResponse",
    "AgentTermRequest",
    "AgentTermResponse",
    "ObservationReceipt",
]
