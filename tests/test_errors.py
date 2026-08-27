"""errors taxonomy + 핸들러 테스트."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import (
    ConfigError,
    InfraError,
    LLMOutputError,
    ValidationErr,
    register_exception_handlers,
)


def test_error_status_and_code_mapping():
    assert ConfigError("x").http_status == 503
    assert ConfigError("x").error_code == "config_error"
    assert InfraError("x").http_status == 503
    assert LLMOutputError("x").http_status == 502
    assert ValidationErr("x").http_status == 422


def _app_that_raises(exc: Exception) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    def boom():
        raise exc

    return TestClient(app)


def test_handler_returns_structured_body():
    leaked = "postgresql://admin:secret@internal/db C:/private/config.env"
    client = _app_that_raises(ConfigError(leaked))
    resp = client.get("/boom")
    assert resp.status_code == 503
    body = resp.json()
    assert body == {
        "ok": False,
        "error_code": "config_error",
        "message": "서비스 설정이 준비되지 않았습니다.",
    }
    assert leaked not in resp.text


def test_validation_message_remains_actionable():
    client = _app_that_raises(ValidationErr("용어는 2자 이상이어야 합니다."))
    resp = client.get("/boom")
    assert resp.status_code == 422
    assert resp.json()["message"] == "용어는 2자 이상이어야 합니다."


def test_openai_missing_key_maps_to_503_end_to_end():
    """DoD 통합 검증: provider=openai + 키 없음 → ConfigError → 503 (한 경로에서)."""
    from app.core.config import Settings
    from app.core.llm_clients import get_chat_client

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/use-llm")
    def use_llm():
        s = Settings(_env_file=None, LLM_PROVIDER="openai", OPENAI_API_KEY=None)
        get_chat_client(s)  # ConfigError 발생 지점
        return {"ok": True}

    resp = TestClient(app).get("/use-llm")
    assert resp.status_code == 503
    assert resp.json()["error_code"] == "config_error"
