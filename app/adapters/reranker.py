"""Reranker — 후보 Evidence를 LLM 관련도로 재정렬 + 재정렬 리트리버 래퍼(Phase 4).

LLM-as-reranker: 각 후보를 0~10 관련도로 채점해 재정렬(다운로드 없음). RerankedRetriever는
base RetrieverPort로 over-fetch 후 rerank해 top-k를 반환한다(같은 포트라 조합 가능).
무폴백: LLM이 점수를 못 주면 LLMOutputError.
"""

from __future__ import annotations

import math
import re
import threading

from app.application.ports import Evidence, ModelGateway, RetrieverPort

# 프롬프트가 "숫자 하나로만" 요구 → 응답 전체가 점수여야 함(선택적 "점"/"/10" 접미만 허용).
# "8 and 20", "1e2" 같은 모호·비정상 출력은 첫 숫자만 몰래 취하지 않고 거부한다(무폴백).
_SCORE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:점|/\s*10)?$")


class LlmReranker:
    """RerankerPort — LLM이 후보별 관련도(0~10)를 채점해 재정렬."""

    def __init__(self, model: ModelGateway) -> None:
        self._model = model

    def _score(self, query: str, content: str) -> float:
        from app.core.errors import LLMOutputError

        prompt = (
            "질문과 문서의 관련도를 0~10 정수 하나로만 답하라(다른 말 금지).\n"
            f"질문: {query}\n문서: {content}\n관련도(0-10):"
        )
        reply = self._model.complete(prompt, max_tokens=8, temperature=0.0)
        m = _SCORE_RE.match((reply or "").strip())
        if not m:
            # 점수를 못 뽑으면 임의 기본값으로 때우지 않는다(무폴백).
            raise LLMOutputError(f"reranker가 점수를 반환하지 않음: {reply!r}")
        val = float(m.group(1))
        if not 0.0 <= val <= 10.0:
            # 범위 밖(예: -3, 50)은 계약 위반 — 클램프(조용한 보정)하지 않고 거부.
            raise LLMOutputError(f"reranker 점수 범위 밖(0-10): {val}")
        return val

    def rerank(
        self, query: str, evidence: list[Evidence], top_n: int | None = None
    ) -> list[Evidence]:
        scored = [(self._score(query, e.content), e) for e in evidence]
        scored.sort(key=lambda se: se[0], reverse=True)
        # 재랭킹 점수를 [0,1]로 정규화해 Evidence.score에 반영 — 반환 순서와 score 의미 일치
        # (RetrieverPort 계약: score 내림차순=순위). backend는 원 출처를 유지(관측용).
        ordered = [
            Evidence(
                content=e.content,
                source=e.source,
                locator=e.locator,
                score=raw / 10.0,
                backend=e.backend,
            )
            for raw, e in scored
        ]
        return ordered[:top_n] if (top_n and top_n > 0) else ordered


class CrossEncoderReranker:
    """Local cross-encoder reranker with lazy, thread-safe model loading.

    The model is loaded on the first rerank request so importing the API does not
    download weights or reserve GPU memory.  A supplied ``model`` is supported
    for deterministic unit tests and offline serving wrappers.
    """

    def __init__(
        self,
        model_name: str,
        *,
        device: str = "auto",
        batch_size: int = 1,
        max_length: int = 768,
        dtype: str = "float16",
        trust_remote_code: bool = False,
        model=None,
    ) -> None:
        if not model_name.strip():
            raise ValueError("model_name must not be empty")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if max_length < 1:
            raise ValueError("max_length must be positive")
        if dtype not in {"auto", "float16", "bfloat16", "float32"}:
            raise ValueError(f"unsupported reranker dtype: {dtype!r}")
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._max_length = max_length
        self._dtype = dtype
        self._trust_remote_code = trust_remote_code
        self._model = model
        self._load_lock = threading.Lock()

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            import torch
            from sentence_transformers import CrossEncoder

            dtype_map = {
                "float16": torch.float16,
                "bfloat16": torch.bfloat16,
                "float32": torch.float32,
            }
            model_kwargs = {}
            if self._dtype != "auto":
                model_kwargs["dtype"] = dtype_map[self._dtype]
            self._model = CrossEncoder(
                self._model_name,
                device=None if self._device == "auto" else self._device,
                trust_remote_code=self._trust_remote_code,
                max_length=self._max_length,
                model_kwargs=model_kwargs,
            )
        return self._model

    @staticmethod
    def _unit_score(value: float) -> float:
        if 0.0 <= value <= 1.0:
            return value
        value = max(-60.0, min(60.0, value))
        return 1.0 / (1.0 + math.exp(-value))

    def rerank(
        self, query: str, evidence: list[Evidence], top_n: int | None = None
    ) -> list[Evidence]:
        if not evidence:
            return []

        import numpy as np

        scores = self._get_model().predict(
            [(query, item.content) for item in evidence],
            batch_size=self._batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        scores = np.asarray(scores).reshape(-1)
        if len(scores) != len(evidence):
            raise RuntimeError(
                f"reranker score count mismatch: {len(scores)} != {len(evidence)}"
            )
        if not np.isfinite(scores).all():
            raise RuntimeError("reranker returned non-finite scores")
        if len(scores) > 1 and float(np.ptp(scores)) <= 1e-8:
            raise RuntimeError(
                "reranker returned constant scores; checkpoint/scoring adapter mismatch"
            )

        ranked = sorted(
            zip(scores.tolist(), evidence, strict=True),
            key=lambda pair: pair[0],
            reverse=True,
        )
        ordered = [
            Evidence(
                content=item.content,
                source=item.source,
                locator=item.locator,
                score=self._unit_score(float(score)),
                backend=item.backend,
            )
            for score, item in ranked
        ]
        return ordered[:top_n] if (top_n and top_n > 0) else ordered


class RerankedRetriever:
    """RetrieverPort — base로 over-fetch 후 reranker로 재정렬해 top-k 반환."""

    backend = "reranked"

    def __init__(
        self, base: RetrieverPort, reranker, over_fetch: int = 10
    ) -> None:
        self._base = base
        self._reranker = reranker
        self._over_fetch = over_fetch

    def search(
        self, query: str, k: int | None = None, source: str | None = None
    ) -> list[Evidence]:
        # 요청 k가 over_fetch보다 크면 후보를 누락하지 않도록 넉넉히 조회한 뒤 재랭킹.
        fetch = max(self._over_fetch, k) if (k and k > 0) else self._over_fetch
        candidates = self._base.search(query, k=fetch, source=source)
        return self._reranker.rerank(query, candidates, top_n=k)
