"""등록 외부 에이전트의 기계 인증·감사 값 객체.

원문 API 키와 최종 사용자 식별자는 저장 계층으로 넘기기 전에 각각 검증·해시한다.
이 모듈은 FastAPI나 PostgreSQL을 import하지 않는다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any, Final


AGENT_SCOPES: Final[frozenset[str]] = frozenset(
    {"precheck:read", "terms:read", "observations:write", "cohort:read"}
)
AGENT_OPERATIONS: Final[frozenset[str]] = frozenset(
    {"support_manifest", "precheck", "terms_explain", "cohort", "observation"}
)

_CLIENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
_SECRET = re.compile(r"^[A-Za-z0-9_-]{40,80}$")
_OPAQUE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{7,127}$")
_KEY_PREFIX = "oib1"


@dataclass(frozen=True)
class AgentPrincipal:
    """인증된 기계 클라이언트. 원문 키는 절대 보관하지 않는다."""

    client_id: str
    display_name: str
    scopes: frozenset[str]
    rate_limit_per_minute: int
    key_fingerprint: str


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


@dataclass(frozen=True)
class IdempotencyReservation:
    replayed: bool
    submission_id: str = ""
    lease_token: str = ""


@dataclass(frozen=True)
class AgentAuditRecord:
    client_id: str
    operation: str
    required_scope: str
    subject_hash: str
    request_hash: str
    response_hash: str | None
    trace_hash: str
    source_event_hash: str | None
    http_status: int
    latency_ms: int
    verdict: str | None = None
    abstained: bool | None = None
    reason_code: str | None = None
    rule_engine_version: str | None = None
    model_profile: str | None = None
    policy_version_ref: str | None = None
    citation_refs: tuple[str, ...] = ()


def validate_client_id(client_id: str) -> str:
    value = (client_id or "").strip()
    if not _CLIENT_ID.fullmatch(value):
        raise ValueError("client_id는 영숫자로 시작하는 3~64자의 영숫자·_·- 값이어야 합니다.")
    return value


def generate_api_key(client_id: str) -> str:
    """256-bit 난수 키를 만든다. 호출자는 생성 시 한 번만 사용자에게 보여줘야 한다."""

    client = validate_client_id(client_id)
    return f"{_KEY_PREFIX}.{client}.{secrets.token_urlsafe(32)}"


def parse_api_key(raw_key: str) -> str:
    """키 형식을 검증하고 공개 lookup 부분인 client_id만 돌려준다."""

    parts = (raw_key or "").split(".")
    if len(parts) != 3 or parts[0] != _KEY_PREFIX:
        raise ValueError("등록 에이전트 키 형식이 아닙니다.")
    client = validate_client_id(parts[1])
    if not _SECRET.fullmatch(parts[2]):
        raise ValueError("등록 에이전트 키 형식이 아닙니다.")
    return client


def hash_api_key(raw_key: str) -> str:
    """서버가 만든 고엔트로피 키의 SHA-256 지문."""

    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def key_fingerprint(raw_key: str) -> str:
    """로그·목록 표시용 비가역 짧은 지문. 키 원문/접두 비밀을 기록하지 않는다."""

    return hash_api_key(raw_key)[:16]


def validate_scopes(scopes: set[str] | frozenset[str] | list[str]) -> frozenset[str]:
    normalized = frozenset(str(scope).strip() for scope in scopes if str(scope).strip())
    unknown = sorted(normalized - AGENT_SCOPES)
    if unknown:
        raise ValueError(f"알 수 없는 agent scope입니다: {unknown}")
    if not normalized:
        raise ValueError("agent scope를 하나 이상 지정해야 합니다.")
    return normalized


def validate_opaque_ref(value: str, *, label: str) -> str:
    """원문 개인정보가 아닌 opaque 참조만 허용한다."""

    normalized = (value or "").strip()
    if not _OPAQUE_REF.fullmatch(normalized):
        raise ValueError(
            f"{label}은 영숫자로 시작하는 8~128자의 opaque 값이어야 합니다. "
            "이름·주민번호·질병명 같은 원문 개인정보를 보내지 마세요."
        )
    return normalized


def hmac_hex(secret: str, value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sensitive_payload_hash(secret: str, value: Any) -> str:
    """저엔트로피 질병기호도 사전대입으로 복원하기 어렵게 keyed hash를 쓴다."""

    return hmac_hex(secret, canonical_json(value))


__all__ = [
    "AGENT_OPERATIONS",
    "AGENT_SCOPES",
    "AgentAuditRecord",
    "AgentPrincipal",
    "IdempotencyReservation",
    "RateLimitDecision",
    "canonical_json",
    "generate_api_key",
    "hash_api_key",
    "hmac_hex",
    "key_fingerprint",
    "parse_api_key",
    "sensitive_payload_hash",
    "validate_client_id",
    "validate_opaque_ref",
    "validate_scopes",
]
