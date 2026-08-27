"""FakeRetriever — RetrieverPort의 테스트용 구현(고정 근거)."""

from __future__ import annotations

from app.application.ports import Evidence


class FakeRetriever:
    backend = "fake"

    def __init__(self, evidence: list[Evidence] | None = None) -> None:
        self._evidence = list(evidence or [])
        self.calls: list[tuple[str, int | None, str | None]] = []

    def search(
        self, query: str, k: int | None = None, source: str | None = None
    ) -> list[Evidence]:
        self.calls.append((query, k, source))
        return list(self._evidence)
