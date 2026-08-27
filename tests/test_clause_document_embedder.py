from __future__ import annotations

import numpy as np
import pytest

from app.adapters.clause_document_embedder import ClauseDocumentEmbedder
from app.core.errors import InfraError


def _profile(**overrides):
    profile = {
        "model": "approved/model",
        "revision": "abc123",
        "dim": 3,
        "max_seq_length": 512,
        "chunk_budget": 448,
        "overlap": 80,
        "doc_prefix": "passage: ",
        "normalized": True,
    }
    profile.update(overrides)
    return profile


class _Model:
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append((texts, kwargs))
        return self.vectors


def test_document_embedder_uses_approved_prefix_and_normalization():
    model = _Model(np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32))
    embedder = ClauseDocumentEmbedder(_profile(), model=model)

    vectors = embedder.embed_documents(["첫 조각", "둘째 조각"])

    assert vectors.shape == (2, 3)
    texts, kwargs = model.calls[0]
    assert texts == ["passage: 첫 조각", "passage: 둘째 조각"]
    assert kwargs["normalize_embeddings"] is True
    assert kwargs["convert_to_numpy"] is True


def test_document_embedder_rejects_incomplete_profile():
    profile = {key: value for key, value in _profile().items() if key != "doc_prefix"}
    with pytest.raises(InfraError, match="doc_prefix"):
        ClauseDocumentEmbedder(profile)


def test_document_embedder_rejects_wrong_vector_dimension():
    model = _Model(np.array([[1.0, 0.0]], dtype=np.float32))
    embedder = ClauseDocumentEmbedder(_profile(dim=3), model=model)

    with pytest.raises(InfraError, match="벡터 모양"):
        embedder.embed_documents(["조각"])


def test_document_embedder_rejects_empty_chunk():
    model = _Model(np.array([[1.0, 0.0, 0.0]], dtype=np.float32))
    embedder = ClauseDocumentEmbedder(_profile(), model=model)

    with pytest.raises(InfraError, match="빈 조항"):
        embedder.embed_documents(["  "])
