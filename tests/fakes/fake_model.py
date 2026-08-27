"""FakeModelGateway — ModelGateway의 테스트용 구현(고정 응답 또는 예외)."""

from __future__ import annotations


class FakeModelGateway:
    def __init__(self, reply: str = "테스트 답변", raises: Exception | None = None) -> None:
        self._reply = reply
        self._raises = raises
        self.prompts: list[str] = []

    def complete(
        self, prompt: str, *, max_tokens: int | None = None, temperature: float = 0.0
    ) -> str:
        self.prompts.append(prompt)
        if self._raises is not None:
            raise self._raises
        return self._reply
