"""llm_clients 단위 테스트 (실제 네트워크 호출 없음)."""

import pytest

from app.core.config import Settings
from app.core.errors import ConfigError
from app.core.llm_clients import get_active_model, get_chat_client


def test_local_client_created_without_key():
    s = Settings(_env_file=None, LLM_PROVIDER="local")
    client = get_chat_client(s)  # 생성만, 호출하지 않음
    assert str(client.base_url).rstrip("/").endswith("/v1")
    assert get_active_model(s) == s.LOCAL_MODEL


def test_openai_without_key_raises_config_error():
    s = Settings(_env_file=None, LLM_PROVIDER="openai", OPENAI_API_KEY=None)
    with pytest.raises(ConfigError):
        get_chat_client(s)


def test_openai_with_key_creates_client():
    s = Settings(_env_file=None, LLM_PROVIDER="openai", OPENAI_API_KEY="sk-test")
    client = get_chat_client(s)
    assert client is not None
    assert get_active_model(s) == s.OPENAI_MODEL


def test_gemini_raises_config_error_until_phase35():
    s = Settings(_env_file=None, LLM_PROVIDER="gemini")
    with pytest.raises(ConfigError):
        get_chat_client(s)


def test_local_empty_base_url_raises_config_error():
    s = Settings(_env_file=None, LLM_PROVIDER="local", LOCAL_BASE_URL="")
    with pytest.raises(ConfigError):
        get_chat_client(s)


def test_local_empty_model_raises_config_error():
    s = Settings(_env_file=None, LLM_PROVIDER="local", LOCAL_MODEL="")
    with pytest.raises(ConfigError):
        get_chat_client(s)
