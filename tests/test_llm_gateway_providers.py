from __future__ import annotations

from types import SimpleNamespace

from app.adapters.llm_gateway import LlmGateway
from app.core.config import Settings


class _Completions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
        )


class _OpenAIClient:
    def __init__(self):
        self.completions = _Completions()
        self.chat = SimpleNamespace(completions=self.completions)


def _patch_settings(monkeypatch, settings):
    from app.core import config, llm_clients

    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_clients, "get_settings", lambda: settings)


def test_openai_provider_uses_openai_model_not_local_profile(monkeypatch):
    from app.core import llm_clients

    settings = Settings(
        _env_file=None,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="sk-test",
        OPENAI_MODEL="configured-openai-model",
        ACTIVE_MODEL_PROFILE="local_gemma4_e4b",
    )
    _patch_settings(monkeypatch, settings)
    client = _OpenAIClient()
    monkeypatch.setattr(llm_clients, "get_chat_client", lambda _settings=None: client)

    assert LlmGateway().complete("hello") == "OK"
    assert client.completions.kwargs["model"] == "configured-openai-model"


def test_local_provider_uses_configured_local_model(monkeypatch):
    from app.core import llm_clients

    settings = Settings(
        _env_file=None,
        LLM_PROVIDER="local",
        LOCAL_MODEL="configured-local-model",
    )
    _patch_settings(monkeypatch, settings)
    client = _OpenAIClient()
    monkeypatch.setattr(llm_clients, "get_chat_client", lambda _settings=None: client)

    assert LlmGateway().complete("hello") == "OK"
    assert client.completions.kwargs["model"] == "configured-local-model"


def test_gemini_provider_uses_google_key_path(monkeypatch):
    from app.core import llm_clients

    settings = Settings(
        _env_file=None,
        LLM_PROVIDER="gemini",
        GOOGLE_API_KEY="google-test",
        GEMINI_MODEL="configured-gemini-model",
    )
    _patch_settings(monkeypatch, settings)

    class _Models:
        kwargs = None

        def generate_content(self, **kwargs):
            self.kwargs = kwargs
            assert kwargs["contents"] == "hello"
            return SimpleNamespace(text="GEMINI_OK")

    models = _Models()
    client = SimpleNamespace(models=models)
    monkeypatch.setattr(llm_clients, "get_gemini_client", lambda _settings=None: client)
    assert LlmGateway().complete("hello") == "GEMINI_OK"
    assert models.kwargs["config"].max_output_tokens == 256


def test_server_runners_do_not_override_env_provider():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("run_customer_server.py", "run_admin_server.py", "run_dev_server.py"):
        source = (root / "scripts" / name).read_text(encoding="utf-8")
        assert 'setdefault("LLM_PROVIDER"' not in source
