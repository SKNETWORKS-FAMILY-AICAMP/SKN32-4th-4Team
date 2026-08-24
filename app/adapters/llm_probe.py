"""선택된 LLM provider의 실제 연결 상태를 비생성 요청으로 검사한다."""

from __future__ import annotations

from time import perf_counter

from app.core.config import Settings, get_settings
from app.core.config_validation import settings_readiness
from app.core.llm_clients import get_active_model


def probe_llm(settings: Settings | None = None) -> dict:
    """키를 노출하거나 답변 토큰을 생성하지 않고 모델 조회 API를 검사한다."""
    settings = settings or get_settings()
    provider = settings.LLM_PROVIDER
    model = get_active_model(settings)
    configured = settings_readiness(settings).get(provider, False)
    started = perf_counter()
    result = {
        "provider": provider,
        "model": model,
        "configured": configured,
        "ready": False,
        "latency_ms": None,
        "error": None,
    }
    if not configured:
        result["error"] = f"{provider} provider 설정 또는 API 키가 없습니다."
        return result

    try:
        if provider == "local":
            import httpx

            url = settings.LOCAL_BASE_URL.rstrip("/") + "/models"
            response = httpx.get(url, timeout=settings.LLM_HEALTH_TIMEOUT_SECONDS)
            response.raise_for_status()
            ids = {
                str(row.get("id"))
                for row in response.json().get("data", [])
                if isinstance(row, dict)
            }
            if model not in ids:
                raise RuntimeError(f"서버 모델 목록에 {model!r}가 없습니다.")
        elif provider == "openai":
            from openai import OpenAI

            client = OpenAI(
                api_key=settings.OPENAI_API_KEY,
                timeout=settings.LLM_HEALTH_TIMEOUT_SECONDS,
                max_retries=0,
            )
            client.models.retrieve(model)
        else:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=settings.GOOGLE_API_KEY,
                http_options=types.HttpOptions(
                    timeout=int(settings.LLM_HEALTH_TIMEOUT_SECONDS * 1000)
                ),
            )
            client.models.get(model=model)
        result["ready"] = True
    except Exception:  # 공급자 SDK 예외에는 URL·키·내부 경로가 포함될 수 있다.
        result["error"] = "모델 상태 확인에 실패했습니다."
    result["latency_ms"] = round((perf_counter() - started) * 1000, 1)
    return result
