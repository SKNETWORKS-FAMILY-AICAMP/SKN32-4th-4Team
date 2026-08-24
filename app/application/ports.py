"""Application 포트 — 검색·모델 게이트웨이 인터페이스와 중립 Evidence.

v3.2 ADR-005'(RetrieverPort 중립성): 포트는 백엔드 세부(FAISS distance 등)를 노출하지
않는다. 점수는 정규화 [0,1](1=가장 관련)로 통일해 FAISS/pgvector/hybrid/graph 결과를
같은 척도로 비교 가능하게 한다. 정규화 책임은 어댑터에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Evidence:
    """검색 근거 1건(백엔드 중립).

    - content: 근거 본문
    - source: 원천 문서 식별자(파일명 등)
    - locator: 문서 내 위치(예: 페이지 문자열). 없으면 None
    - score: 정규화 관련도 [0,1], 1=가장 관련
    - backend: 이 근거를 낸 검색 백엔드("faiss" 등) — 비교·관측용
    """

    content: str
    source: str
    locator: str | None
    score: float
    backend: str


@runtime_checkable
class RetrieverPort(Protocol):
    """의미 검색 포트. 구현: FaissRetriever(Phase 1), PgVectorRetriever(Phase 3) 등."""

    def search(
        self, query: str, k: int | None = None, source: str | None = None
    ) -> list[Evidence]:
        ...


@runtime_checkable
class ModelGateway(Protocol):
    """LLM 텍스트 완성 포트. 구현: LlmGateway(Phase 1), FakeModelGateway(테스트)."""

    def complete(
        self, prompt: str, *, max_tokens: int | None = None, temperature: float = 0.0
    ) -> str:
        ...


@runtime_checkable
class RerankerPort(Protocol):
    """후보 Evidence를 질의 관련도로 재정렬(Phase 4). 구현: LlmReranker, FakeReranker(테스트)."""

    def rerank(
        self, query: str, evidence: list[Evidence], top_n: int | None = None
    ) -> list[Evidence]:
        ...
