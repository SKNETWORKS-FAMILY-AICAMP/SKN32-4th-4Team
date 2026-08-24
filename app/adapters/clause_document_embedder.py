"""승인된 보험 조항 인덱스와 같은 프로필로 문서를 임베딩한다.

구형 커머스 RAG의 ``app.rag.embeddings`` 는 전역 설정의 과거
``ko-sroberta`` 모델을 사용했다. 보험 조항 색인은 승인 릴리스의 모델·revision·차원·
접두사까지 한 벌로 고정해야 하므로 이 어댑터에서 그 계약을 검증한다.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

from app.core.errors import InfraError

_REQUIRED_VALUES = (
    "model",
    "revision",
    "dim",
    "max_seq_length",
    "chunk_budget",
    "overlap",
)
_REQUIRED_KEYS = ("doc_prefix", "normalized")


def accepted_profile() -> dict:
    """현재 요청/작업에 고정된 승인 릴리스의 임베딩 프로필을 반환한다."""
    from app.core import release

    return dict((release.current().raw.get("embed_profile") or {}))


class ClauseDocumentEmbedder:
    """승인 프로필로 조항 조각을 인코딩하는 지연 로딩 어댑터."""

    def __init__(self, profile: dict, *, model=None) -> None:
        missing = [key for key in _REQUIRED_VALUES if profile.get(key) in (None, "", 0)]
        missing.extend(key for key in _REQUIRED_KEYS if key not in profile)
        if missing:
            raise InfraError(
                "승인 릴리스의 문서 임베딩 프로필이 불완전하다: "
                f"{sorted(set(missing))}. 색인 벡터를 추측해 만들지 않는다."
            )
        self.profile = dict(profile)
        self._model = model
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                model = SentenceTransformer(
                    self.profile["model"],
                    revision=self.profile["revision"],
                )
                model.max_seq_length = int(self.profile["max_seq_length"])
                self._model = model
        return self._model

    def embed_documents(self, texts: Sequence[str]):
        """문서 접두사·정규화·차원을 검증한 ``numpy`` 벡터를 반환한다."""
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise InfraError("빈 조항 조각은 임베딩하지 않는다")

        import numpy as np

        prefix = self.profile["doc_prefix"]
        vectors = self._get_model().encode(
            [prefix + text for text in texts],
            convert_to_numpy=True,
            normalize_embeddings=bool(self.profile["normalized"]),
            show_progress_bar=False,
        )
        vectors = np.asarray(vectors)
        expected = (len(texts), int(self.profile["dim"]))
        if vectors.shape != expected:
            raise InfraError(f"문서 벡터 모양이 승인 프로필과 다르다: {vectors.shape} != {expected}")
        if not np.isfinite(vectors).all():
            raise InfraError("문서 벡터에 유한하지 않은 값이 있다")
        return vectors


def build() -> ClauseDocumentEmbedder:
    """승인 릴리스에서 프로필을 읽어 문서 임베더를 만든다."""
    return ClauseDocumentEmbedder(accepted_profile())


__all__ = ["ClauseDocumentEmbedder", "accepted_profile", "build"]
