"""헬스 체크 라우터.

/api/health 는 LLM/네트워크 실호출 없이 '설정·키·경로 존재 여부'만 boolean으로
보고한다 (Codex 합의).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.config_validation import settings_readiness
from app.core.llm_clients import get_active_model

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    configured = settings_readiness(settings)
    return {
        "status": "ok",
        "provider": settings.LLM_PROVIDER,
        "model": get_active_model(settings),
        # 하위 호환 키. 값은 실제 연결이 아니라 설정 여부다.
        "readiness": configured,
        "configured": configured,
        "llm_live_check": "/api/health/llm",
    }


@router.get("/health/llm")
def llm_health() -> dict:
    """선택된 provider의 모델 조회 API를 실제로 검사한다(답변 토큰 생성 없음)."""
    from app.adapters.llm_probe import probe_llm

    return probe_llm()


@router.get("/health/ready")
def ready() -> dict:
    """민감한 DB 구성은 숨기고 구성요소별 준비 여부만 공개한다."""
    from app.obs.readiness import public_readiness

    return public_readiness()
