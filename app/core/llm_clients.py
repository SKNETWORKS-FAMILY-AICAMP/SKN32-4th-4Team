"""LLM 프로바이더 추상화 (폴백 금지).

RULE.md 3.2에 따라 키/설정이 없으면 조용한 대체 없이 ConfigError를 발생시킨다.
로컬(Gemma)·OpenAI는 OpenAI 호환 클라이언트로 통일한다. Gemini는 LangChain을
경유하므로 Phase 3.5 전까지 명시적 ConfigError로 미구현을 알린다.
"""

from __future__ import annotations

from openai import OpenAI

from app.core.config import Settings, get_settings
from app.core.config_validation import has_google_key, has_openai_key
from app.core.errors import ConfigError


def get_chat_client(settings: Settings | None = None) -> OpenAI:
    """현재 LLM_PROVIDER에 맞는 OpenAI 호환 채팅 클라이언트를 반환한다.

    - local: llama-cpp-python OpenAI 호환 서버로 접속 (외부 토큰 0)
    - openai: 키 없으면 ConfigError (데모/폴백 없음)
    - gemini: OpenAI 호환 클라이언트가 아니므로 `get_langchain_chat`을 사용
    """
    settings = settings or get_settings()
    provider = settings.LLM_PROVIDER

    if provider == "local":
        if not (settings.LOCAL_BASE_URL and settings.LOCAL_BASE_URL.strip()):
            raise ConfigError("LOCAL_BASE_URL이 비어 있습니다. 로컬 Gemma 서버 주소를 설정하세요.")
        if not (settings.LOCAL_MODEL and settings.LOCAL_MODEL.strip()):
            raise ConfigError("LOCAL_MODEL이 비어 있습니다.")
        return OpenAI(
            base_url=settings.LOCAL_BASE_URL,
            api_key=settings.LOCAL_API_KEY,
            timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )

    if provider == "openai":
        if not has_openai_key(settings):
            raise ConfigError("OPENAI_API_KEY가 설정되지 않았습니다. .env에 키를 넣으세요.")
        return OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )

    if provider == "gemini":
        raise ConfigError("Gemini는 OpenAI 호환 클라이언트가 아닙니다. get_langchain_chat()을 사용하세요.")

    raise ConfigError(f"알 수 없는 LLM_PROVIDER: {provider}")


def get_langchain_chat(settings: Settings | None = None):
    """현재 프로바이더에 맞는 LangChain ChatModel 반환 (Phase 3.5).

    local/openai → ChatOpenAI(호환 endpoint), gemini → ChatGoogleGenerativeAI.
    키 없으면 ConfigError (폴백 없음).
    """
    settings = settings or get_settings()
    provider = settings.LLM_PROVIDER

    def _require(value: str | None, name: str) -> str:
        if not (value and value.strip()):
            raise ConfigError(f"{name}이(가) 비어 있습니다.")
        return value

    if provider in ("local", "openai"):
        from langchain_openai import ChatOpenAI

        if provider == "local":
            return ChatOpenAI(
                base_url=_require(settings.LOCAL_BASE_URL, "LOCAL_BASE_URL"),
                api_key=settings.LOCAL_API_KEY,
                model=_require(settings.LOCAL_MODEL, "LOCAL_MODEL"),
                temperature=0,
            )
        if not has_openai_key(settings):
            raise ConfigError("OPENAI_API_KEY가 설정되지 않았습니다.")
        return ChatOpenAI(
            api_key=settings.OPENAI_API_KEY,
            model=_require(settings.OPENAI_MODEL, "OPENAI_MODEL"),
            temperature=0,
        )

    if provider == "gemini":
        if not has_google_key(settings):
            raise ConfigError("GOOGLE_API_KEY가 설정되지 않았습니다.")
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=_require(settings.GEMINI_MODEL, "GEMINI_MODEL"),
            api_key=settings.GOOGLE_API_KEY,
            temperature=0,
            request_timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
            retries=0,
        )

    raise ConfigError(f"알 수 없는 LLM_PROVIDER: {provider}")


def get_gemini_client(settings: Settings | None = None):
    """공식 google-genai 동기 클라이언트.

    MCP stdio 자식 프로세스에서 LangChain 채팅 클라이언트의 백그라운드 런타임이
    종료를 붙드는 문제가 있어 단순 생성 경로는 공식 동기 SDK를 사용한다.
    """
    settings = settings or get_settings()
    if not has_google_key(settings):
        raise ConfigError("GOOGLE_API_KEY가 설정되지 않았습니다.")
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=settings.GOOGLE_API_KEY,
        http_options=types.HttpOptions(
            timeout=int(settings.LLM_REQUEST_TIMEOUT_SECONDS * 1000)
        ),
    )


def get_active_model(settings: Settings | None = None) -> str:
    """현재 프로바이더의 채팅 모델명을 반환한다."""
    settings = settings or get_settings()
    provider = settings.LLM_PROVIDER
    if provider == "local":
        return settings.LOCAL_MODEL
    if provider == "openai":
        return settings.OPENAI_MODEL
    if provider == "gemini":
        return settings.GEMINI_MODEL
    raise ConfigError(f"알 수 없는 LLM_PROVIDER: {provider}")
