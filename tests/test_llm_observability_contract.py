"""Worker 1 LLM contract tests. No network, generation API, or database calls."""

from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from app.adapters.llm_gateway import LlmGateway, _normalized_usage
from app.core.config import Settings
from app.core.errors import ConfigError, InfraError, LLMOutputError, RateLimitErr
from app.obs import metrics


class FakeCompletions:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def create(self, **_kwargs):
        if self.error:
            raise self.error
        return self.result


def response(text="OK", usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=usage
    )


def install_openai(monkeypatch, result=None, error=None):
    from app.core import config, llm_clients

    settings = Settings(_env_file=None, LLM_PROVIDER="openai", OPENAI_API_KEY="test-key",
                        OPENAI_MODEL="test-model")
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(result, error)))
    monkeypatch.setattr(llm_clients, "get_chat_client", lambda _settings=None: client)


def metric_text():
    collector = metrics.Metrics()
    metrics._llm(collector)
    return collector.render()


@pytest.fixture(autouse=True)
def clear_metrics():
    metrics._reset_llm_metrics_for_tests()
    yield
    metrics._reset_llm_metrics_for_tests()


def test_provider_usages_are_normalized():
    assert _normalized_usage(SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=3, completion_tokens=5, total_tokens=8)), "openai") == {
            "input": 3, "output": 5, "total": 8}
    assert _normalized_usage(SimpleNamespace(usage_metadata=SimpleNamespace(
        prompt_token_count=7, candidates_token_count=11, total_token_count=18)), "gemini") == {
            "input": 7, "output": 11, "total": 18}
    assert _normalized_usage(SimpleNamespace(), "openai") is None


def test_success_records_bounded_labels_latency_and_tokens(monkeypatch):
    install_openai(monkeypatch, response(usage=SimpleNamespace(
        prompt_tokens=3, completion_tokens=5, total_tokens=8)))
    assert LlmGateway().complete("do-not-record-this-secret") == "OK"
    rendered = metric_text()
    assert 'provider="openai"' in rendered
    assert 'model="test-model"' in rendered
    assert 'purpose="term_explanation"' in rendered
    assert 'outcome="success"' in rendered
    assert 'type="input"' in rendered and " 3\n" in rendered
    assert "do-not-record-this-secret" not in rendered


def test_missing_usage_emits_no_token_metric(monkeypatch):
    install_openai(monkeypatch, response())
    assert LlmGateway().complete("hello", purpose="ai2_explanation") == "OK"
    assert "llm_tokens_total" not in metric_text()


def test_purpose_is_closed_before_provider_call(monkeypatch):
    install_openai(monkeypatch, response())
    with pytest.raises(ConfigError, match="허용되지 않은"):
        LlmGateway().complete("hello", purpose="arbitrary-user-label")
    assert metric_text() == "\n"


def test_429_preserves_retry_after_without_secret_leak(monkeypatch):
    request = httpx.Request("POST", "https://provider.invalid")
    provider_response = httpx.Response(429, request=request, headers={"Retry-After": "17"})
    error = RateLimitError("secret-key prompt-body", response=provider_response, body=None)
    install_openai(monkeypatch, error=error)
    with pytest.raises(RateLimitErr) as caught:
        LlmGateway().complete("another-secret")
    assert caught.value.headers == {"Retry-After": "17"}
    assert "secret" not in str(caught.value)
    assert 'outcome="rate_limited"' in metric_text()


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (APITimeoutError(request=httpx.Request("POST", "https://provider.invalid")), "timeout"),
        (APIConnectionError(request=httpx.Request("POST", "https://provider.invalid")),
         "connection_error"),
        (APIStatusError("sensitive-provider-body", response=httpx.Response(
            500, request=httpx.Request("POST", "https://provider.invalid")), body=None), "api_error"),
    ],
)
def test_provider_errors_are_classified_and_sanitized(monkeypatch, error, outcome):
    install_openai(monkeypatch, error=error)
    with pytest.raises(InfraError) as caught:
        LlmGateway().complete("sensitive-prompt")
    assert "sensitive" not in str(caught.value)
    assert f'outcome="{outcome}"' in metric_text()


def test_empty_response_is_explicit_and_observed(monkeypatch):
    install_openai(monkeypatch, response(text=" "))
    with pytest.raises(LLMOutputError):
        LlmGateway().complete("hello")
    assert 'outcome="empty_response"' in metric_text()


def test_gemini_429_is_explicit_and_preserves_retry_after(monkeypatch):
    from app.core import config, llm_clients

    settings = Settings(_env_file=None, LLM_PROVIDER="gemini", GOOGLE_API_KEY="test-key",
                        GEMINI_MODEL="test-gemini-model")
    monkeypatch.setattr(config, "get_settings", lambda: settings)

    class Provider429(Exception):
        status_code = 429
        response = SimpleNamespace(headers={"Retry-After": "23"})

    def fail(**_kwargs):
        raise Provider429("secret provider payload")

    client = SimpleNamespace(models=SimpleNamespace(generate_content=fail))
    monkeypatch.setattr(llm_clients, "get_gemini_client", lambda _settings=None: client)
    with pytest.raises(RateLimitErr) as caught:
        LlmGateway().complete("secret prompt")
    assert caught.value.headers["Retry-After"] == "23"
    assert "secret" not in str(caught.value)
    assert 'provider="gemini"' in metric_text()


def test_gemini_client_disables_sdk_retries(monkeypatch):
    from google import genai
    from app.core.llm_clients import get_gemini_client

    captured = {}
    monkeypatch.setattr(genai, "Client", lambda **kwargs: captured.update(kwargs) or object())
    settings = Settings(_env_file=None, LLM_PROVIDER="gemini", GOOGLE_API_KEY="test-key")
    get_gemini_client(settings)
    assert captured["http_options"].retry_options.attempts == 0
