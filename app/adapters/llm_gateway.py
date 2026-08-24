"""LlmGateway — ModelGateway의 OpenAI 호환 구현.

모델 ID는 소스가 아니라 **레지스트리**(get_active_profile)에서 해석한다(REQ-LLM-REG-01).
접속 인프라(base_url/key)는 기존 `app.core.llm_clients`를 재사용한다. 무폴백: 연결/HTTP
실패는 InfraError로 전파(빈/가짜 답변 반환 금지).
"""

from __future__ import annotations

from app.core.model_registry import ModelProfile


class LlmGateway:
    """ModelGateway 구현. 프로필의 provider_model_id로 완성 요청."""

    def __init__(self, profile: ModelProfile | None = None) -> None:
        self._profile = profile

    def complete(
        self, prompt: str, *, max_tokens: int | None = None, temperature: float = 0.0
    ) -> str:
        from openai import APIConnectionError, APIError, APITimeoutError

        from app.core.config import get_settings
        from app.core.errors import ConfigError, InfraError, LLMOutputError
        from app.core.llm_clients import get_active_model, get_chat_client, get_gemini_client

        settings = get_settings()
        if self._profile is not None and self._profile.provider != settings.LLM_PROVIDER:
            raise ConfigError(
                f"모델 프로필 provider({self._profile.provider})와 "
                f"LLM_PROVIDER({settings.LLM_PROVIDER})가 다릅니다."
            )
        model_id = self._profile.provider_model_id if self._profile else get_active_model(settings)

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
            except Exception as exc:  # provider SDK 예외를 인프라 오류로 경계 변환
                raise InfraError(f"Gemini LLM 호출 오류: {type(exc).__name__}") from exc
            text = str(response.text or "").strip()
            if not text:
                raise LLMOutputError("Gemini가 빈 응답을 반환했습니다.")
            return text

        client = get_chat_client(settings)
        try:
            resp = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens or 256,
            )
        except (APIConnectionError, APITimeoutError) as exc:
            raise InfraError("LLM 서버에 연결할 수 없습니다.") from exc
        except APIError as exc:
            raise InfraError(f"LLM 호출 오류: {exc}") from exc
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise LLMOutputError("LLM이 빈 응답을 반환했습니다.")
        return text
