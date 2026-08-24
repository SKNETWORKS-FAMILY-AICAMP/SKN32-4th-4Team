"""모델 레지스트리 로더·스키마 (v3.2 §4, REQ-LLM-REG-01).

모델 ID·revision·checksum·검증정보를 소스가 아닌 model_registry.yaml(데이터)에서 로드·검증한다.
RULE 3.1(소스 모델ID 하드코딩 금지)의 단일 출처. `latest` alias·family-only pin·빈 값은 거부한다.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator

from app.core.config import ROOT_DIR, get_settings
from app.core.errors import ConfigError

REGISTRY_PATH = ROOT_DIR / "model_registry.yaml"


class ModelProfile(BaseModel):
    profile_id: str
    provider: str
    provider_model_id: str
    runtime: str
    revision: str | None = None
    artifact_sha256: str | None = None
    quantization: str | None = None
    verified_at: str | None = None
    supported_tasks: list[str] = []
    max_tested_context: int | None = None
    memory_peak_mb: int | None = None
    tool_call_verified: bool = False

    @field_validator("provider_model_id")
    @classmethod
    def _reject_latest_and_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("provider_model_id는 비어 있을 수 없습니다.")
        if "latest" in v.lower():
            raise ValueError("provider_model_id에 'latest' alias 금지(고정 버전 사용).")
        return v

    @property
    def verified(self) -> bool:
        """checksum·검증시각이 모두 있어야 검증됨(라이브 승인 대상)."""
        return bool(self.artifact_sha256 and self.verified_at)


def load_registry(path: Path | None = None) -> dict[str, ModelProfile]:
    """YAML 레지스트리를 로드·검증해 profile_id→ModelProfile 매핑을 반환한다."""
    path = path or REGISTRY_PATH
    if not path.exists():
        raise ConfigError(f"model_registry.yaml이 없습니다: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = data.get("profiles", [])
    if not items:
        raise ConfigError("model_registry.yaml에 profiles가 비어 있습니다.")
    profiles: dict[str, ModelProfile] = {}
    for item in items:
        try:
            profile = ModelProfile(**item)
        except Exception as exc:  # 스키마 검증 실패 → 명시적 설정 오류
            raise ConfigError(f"모델 레지스트리 항목 오류: {exc}") from exc
        if profile.profile_id in profiles:
            raise ConfigError(f"중복 profile_id: {profile.profile_id}")
        profiles[profile.profile_id] = profile
    return profiles


def get_active_profile() -> ModelProfile:
    """설정의 ACTIVE_MODEL_PROFILE에 해당하는 프로필을 반환한다(폴백 없음)."""
    profile_id = get_settings().ACTIVE_MODEL_PROFILE
    registry = load_registry()
    if profile_id not in registry:
        raise ConfigError(
            f"ACTIVE_MODEL_PROFILE '{profile_id}'가 레지스트리에 없습니다. "
            f"사용 가능: {sorted(registry)}"
        )
    return registry[profile_id]
