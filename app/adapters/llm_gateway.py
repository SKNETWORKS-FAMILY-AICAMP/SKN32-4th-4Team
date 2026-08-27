"""LlmGateway — ModelGateway의 OpenAI 호환 구현.

모델 ID는 소스가 아니라 **레지스트리**(get_active_profile)에서 해석한다(REQ-LLM-REG-01).
접속 인프라(base_url/key)는 기존 `app.core.llm_clients`를 재사용한다. 무폴백: 연결/HTTP
실패는 InfraError로 전파(빈/가짜 답변 반환 금지).
"""

from __future__ import annotations

from time import monotonic
from typing import Any

from app.core.model_registry import ModelProfile


class LlmGateway:
    """ModelGateway 구현. 프로필의 provider_model_id로 완성 요청."""

    def __init__(self, profile: ModelProfile | None = None) -> None:
        self._profile = profile

    def complete(
        self, prompt: str, *, max_tokens: int | None = None, temperature: float = 0.0,
        purpose: str = "term_explanation",
    ) -> str:
        from openai import APIConnectionError, APIError, APITimeoutError, RateLimitError

        from app.core.config import get_settings
        from app.core.errors import ConfigError, InfraError, LLMOutputError, RateLimitErr
        from app.core.llm_clients import get_active_model, get_chat_client, get_gemini_client
        from app.obs.metrics import observe_llm_call

        settings = get_settings()
        if self._profile is not None and self._profile.provider != settings.LLM_PROVIDER:
            raise ConfigError(
                f"모델 프로필 provider({self._profile.provider})와 "
                f"LLM_PROVIDER({settings.LLM_PROVIDER})가 다릅니다."
            )
        model_id = self._profile.provider_model_id if self._profile else get_active_model(settings)
        if purpose not in ("term_explanation", "ai2_explanation"):
            raise ConfigError("허용되지 않은 LLM 호출 목적입니다.")
        started = monotonic()

        def observed(outcome: str, response: Any = None) -> None:
            usage = _normalized_usage(response, settings.LLM_PROVIDER) if response is not None else None
            observe_llm_call(provider=settings.LLM_PROVIDER, model=model_id, purpose=purpose,
                             outcome=outcome, latency_seconds=monotonic() - started, usage=usage)

        if settings.LLM_PROVIDER == "gemini":
            try:
                from google.genai import types

                # 클라이언트를 지역 변수로 붙든다. 임시 객체의 `.models`만 꺼내면
                # Client가 먼저 정리돼 내부 httpx가 요청 전에 닫힐 수 있다.
                gemini_client = get_gemini_client(settings)
                response = gemini_client.models.generate_content(
                    model=model_id,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        max_output_tokens=max_tokens or 256,
                        temperature=temperature,
                    ),
                )
            except ConfigError:
                raise
            except Exception as exc:  # provider SDK 예외를 비밀 안전한 경계 오류로 변환
                if _status_code(exc) == 429:
                    observed("rate_limited")
                    raise RateLimitErr("LLM 공급자 요청 한도 초과.",
                                       retry_after_seconds=_retry_after(exc)) from exc
                outcome = "timeout" if isinstance(exc, TimeoutError) else "api_error"
                observed(outcome)
                raise InfraError("Gemini LLM 호출에 실패했습니다.") from exc
            text = str(response.text or "").strip()
            if not text:
                observed("empty_response", response)
                raise LLMOutputError("Gemini가 빈 응답을 반환했습니다.")
            observed("success", response)
            return text

        client = get_chat_client(settings)
        #: 로컬 thinking 모델은 답변 전에 reasoning 토큰을 먼저 써서, 짧은
        #: max_tokens 예산을 모두 소비하고 본문을 비운 사례가 있었다. OpenAI와
        #: Gemini에는 알 수 없는 옵션이므로 local 공급자에만 전달한다.
        local_options = (
            {"extra_body": {"think": False}}
            if settings.LLM_PROVIDER == "local"
            else {}
        )
        try:
            resp = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens or 256,
                **local_options,
            )
        except RateLimitError as exc:
            observed("rate_limited")
            raise RateLimitErr("LLM 공급자 요청 한도 초과.",
                               retry_after_seconds=_retry_after(exc)) from exc
        except APITimeoutError as exc:
            observed("timeout")
            raise InfraError("LLM 서버 응답 시간이 초과되었습니다.") from exc
        except APIConnectionError as exc:
            observed("connection_error")
            raise InfraError("LLM 서버에 연결할 수 없습니다.") from exc
        except APIError as exc:
            observed("api_error")
            raise InfraError("LLM 호출에 실패했습니다.") from exc
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            observed("empty_response", resp)
            raise LLMOutputError("LLM이 빈 응답을 반환했습니다.")
        observed("success", resp)
        return text


def _normalized_usage(response: Any, provider: str) -> dict[str, int] | None:
    usage = getattr(response, "usage", None) or getattr(response, "usage_metadata", None)
    if usage is None:
        return None

    def value(*names: str) -> int:
        for name in names:
            raw = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if raw is not None:
                return max(0, int(raw))
        return 0

    if provider == "gemini":
        input_tokens = value("prompt_token_count", "prompt_tokens", "input_tokens")
        output_tokens = value("candidates_token_count", "completion_tokens", "output_tokens")
        total_tokens = value("total_token_count", "total_tokens")
    else:
        input_tokens = value("prompt_tokens", "input_tokens")
        output_tokens = value("completion_tokens", "output_tokens")
        total_tokens = value("total_tokens")
    return {"input": input_tokens, "output": output_tokens,
            "total": total_tokens or input_tokens + output_tokens}


def _status_code(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None) or getattr(exc, "code", None)


def _retry_after(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None) or {}
    value = headers.get("retry-after") or headers.get("Retry-After")
    return str(value).strip() if value is not None else "60"
