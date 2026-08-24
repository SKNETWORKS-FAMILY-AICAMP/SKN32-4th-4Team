"""조항 의미검색 — **범위를 안 정하면 열리지 않는다.**

★여기서 고정하는 것 (코덱스 검토 목록, 2026-08-04)
  · `query_prefix` 가 없으면 **멈춘다**(기본값 '' 로 때우면 색인과 다른 공간에서 찾는다)
  · `scope_sha256s=None` 은 `allow_global` 없이는 거절 — 「관리자니까 전역」은 안전성 근거가 아니다
  · `sha256s=None`(전역)과 `[]`(쓸 약관 없음)은 **다른 뜻**이다
  · 리랭커 실패는 벡터 순서로 되돌리지 않고 올라온다
  · `full_text` 없는 조각은 재정렬에 넣지 않고, **뺀 수를 센다**
  · 후보 수(candidate_k)와 최종 수(final_k)는 분리된다

DB·모델을 띄우지 않는다. `ix.search` 와 임베더를 가짜로 바꿔 계약만 본다.
"""

from __future__ import annotations

import pytest

from app.adapters.clause_query_embedder import ClauseQueryEmbedder
from app.adapters.pgvector_clause_index import ClauseHit
from app.core.errors import InfraError, ValidationErr
from app.core.usecases import clause_search

PROFILE = {
    "model": "m", "revision": "r" * 40, "dim": 3,
    "max_seq_length": 512, "query_prefix": "query: ", "doc_prefix": "", "normalized": True,
}


class _FakeST:
    def __init__(self): self.seen: list[str] = []
    max_seq_length = 512

    def encode(self, texts, **_kw):
        self.seen.extend(texts)
        return [[0.1, 0.2, 0.3]]


def _hit(no: str, full: str = "조 전체") -> ClauseHit:
    return ClauseHit(
        content_hash=f"h{no}", chunk_ix=0, text="조각", distance=0.4,
        sha256="a" * 64, insurer="삼성화재", qualified_no=no, section="보통약관",
        title="보상하지 않는 사항", page_from=1, page_to=2, full_text=full,
    )


@pytest.fixture
def patched():
    """가짜 색인을 주입한다.

    ★유스케이스는 어댑터를 직접 import 하지 않는다(ARCH-002). 그래서
      monkeypatch 로 모듈을 갈아끼우지 않고 **인자로 넣는다** — 테스트가 더 정직해진다.
    """
    calls: dict = {}

    class _Index:
        @staticmethod
        def search(conn, vec, *, sha256s, limit, max_distance=None):
            calls.update(sha256s=sha256s, limit=limit)
            return calls.get("hits", [])

        current_generation = staticmethod(lambda: "s6")
        current_embed_model = staticmethod(lambda: "arctic")

    calls["index"] = _Index
    return calls


def _deps(patched):
    from app.adapters.clause_rerank import rerank_hits

    return {"index": patched["index"], "rerank_fn": rerank_hits}


def _embedder():
    return ClauseQueryEmbedder(PROFILE, model=_FakeST())


# ── 질의 임베더 ─────────────────────────────────────────────────────────

def test_질의에_접두사를_붙여_인코딩한다():
    st = _FakeST()
    ClauseQueryEmbedder(PROFILE, model=st).encode("치과치료 보철료")
    assert st.seen == ["query: 치과치료 보철료"]


def test_접두사가_없으면_만들_때_멈춘다():
    """★기본값 '' 로 때우면 오류 없이 틀린 조항이 올라온다."""
    prof = {k: v for k, v in PROFILE.items() if k != "query_prefix"}
    with pytest.raises(InfraError, match="query_prefix"):
        ClauseQueryEmbedder(prof)


def test_revision_이_비면_멈춘다():
    with pytest.raises(InfraError, match="revision"):
        ClauseQueryEmbedder({**PROFILE, "revision": ""})


def test_차원이_다르면_멈춘다():
    """차원이 다르면 **다른 색인**이다."""
    with pytest.raises(InfraError, match="차원"):
        ClauseQueryEmbedder({**PROFILE, "dim": 1024}, model=_FakeST()).encode("질의")


def test_빈_질의와_너무_긴_질의를_막는다():
    e = _embedder()
    with pytest.raises(InfraError):
        e.encode("   ")
    with pytest.raises(InfraError, match="너무 길다"):
        e.encode("가" * 513)


# ── 범위(scope) ────────────────────────────────────────────────────────

def test_범위를_안_주면_거절한다(patched):
    """★전역은 기본값이 될 수 없다 — 다른 상품·세대가 섞인다."""
    with pytest.raises(ValidationErr, match="allow_global"):
        clause_search.search(**_deps(patched), conn=None, embedder=_embedder(), query="질의",
                             scope_sha256s=None)


def test_allow_global_을_주면_전역으로_찾는다(patched):
    patched["hits"] = [_hit("제1조")]
    r = clause_search.search(**_deps(patched), conn=None, embedder=_embedder(), query="질의",
                             scope_sha256s=None, allow_global=True)
    assert patched["sha256s"] is None
    assert r.provenance["scope"] == "global"


def test_빈_목록은_전역으로_바뀌지_않는다(patched):
    """`[]` 는 「쓸 수 있는 약관이 없다」이지 「전부 찾아라」가 아니다."""
    r = clause_search.search(**_deps(patched), conn=None, embedder=_embedder(), query="질의",
                             scope_sha256s=[])
    assert patched["sha256s"] == []
    assert r.provenance["scope"] == "0개 약관"


# ── 후보 수 · 본문 완결성 ───────────────────────────────────────────────

def test_후보를_final_k_보다_넉넉히_가져온다(patched):
    """★final_k 만 가져와 재정렬하면 벡터가 놓친 것을 되살릴 수 없다."""
    patched["hits"] = []
    clause_search.search(**_deps(patched), conn=None, embedder=_embedder(), query="질의",
                         scope_sha256s=["a"], final_k=5)
    assert patched["limit"] > 5


def test_후보_상한을_넘지_않는다(patched):
    patched["hits"] = []
    clause_search.search(**_deps(patched), conn=None, embedder=_embedder(), query="질의",
                         scope_sha256s=["a"], final_k=8, candidate_k=999,
                         max_candidates=40)
    assert patched["limit"] == 40


def test_본문_없는_조각은_빼고_센다(patched):
    """조각만 채점하면 예외가 뒤에 오는 법률문의 뜻이 반대로 읽힌다."""
    patched["hits"] = [_hit("제1조"), _hit("제2조", full=""), _hit("제3조")]
    r = clause_search.search(**_deps(patched), conn=None, embedder=_embedder(), query="질의",
                             scope_sha256s=["a"])
    assert r.dropped_incomplete == 1
    assert [h.qualified_no for h in r.hits] == ["제1조", "제3조"]


# ── 리랭킹 ─────────────────────────────────────────────────────────────

def test_리랭커가_없으면_재정렬했다고_말하지_않는다(patched):
    patched["hits"] = [_hit("제1조")]
    r = clause_search.search(**_deps(patched), conn=None, embedder=_embedder(), query="질의",
                             scope_sha256s=["a"])
    assert r.reranked is False


def test_리랭커가_실패하면_원래_순서로_되돌리지_않는다(patched):
    """★조용히 되돌리면 「재정렬했다」고 믿으면서 실제로는 안 한 상태가 된다."""
    patched["hits"] = [_hit("제1조"), _hit("제2조")]

    class _Broken:
        def rerank(self, *a, **k): raise RuntimeError("constant scores")

    with pytest.raises(clause_search.RerankUnavailable, match="constant scores"):
        clause_search.search(**_deps(patched), conn=None, embedder=_embedder(), query="질의",
                             scope_sha256s=["a"], reranker=_Broken())


def test_재정렬하면_그_사실을_남긴다(patched):
    patched["hits"] = [_hit("제1조"), _hit("제2조")]

    class _Reverse:
        def rerank(self, query, evidence, top_n=None):
            return list(reversed(evidence))[:top_n] if top_n else list(reversed(evidence))

    r = clause_search.search(**_deps(patched), conn=None, embedder=_embedder(), query="질의",
                             scope_sha256s=["a"], reranker=_Reverse())
    assert r.reranked is True
    assert [h.qualified_no for h in r.hits] == ["제2조", "제1조"]
    assert r.provenance["index_generation"] == "s6"
    assert r.provenance["query_embed_profile"].startswith("m@")


def test_채점할_본문이_빈_후보는_빼고_센다(patched):
    """★어댑터가 다른 본문으로 대신 채점하지 않도록 **여기서** 걸러 센다.

    한 건 때문에 요청 전체를 503 으로 떨어뜨리지도 않는다 — 빼고, 센다.
    """
    from app.adapters.pgvector_clause_index import ClauseHit

    def _h(no, chunk, full="조 전체"):
        return ClauseHit(content_hash=f"h{no}", chunk_ix=0, text=chunk, distance=0.4,
                         sha256="a" * 64, insurer="삼성화재", qualified_no=no,
                         section="보통약관", title="t", page_from=1, page_to=2, full_text=full)

    patched["hits"] = [_h("제1조", "조각"), _h("제2조", "  "), _h("제3조", "조각3")]

    class _Pass:
        def rerank(self, query, evidence, top_n=None): return evidence

    r = clause_search.search(**_deps(patched), conn=None, embedder=_embedder(), query="질의",
                             scope_sha256s=["a"], reranker=_Pass())
    assert r.dropped_unscorable == 1
    assert [h.qualified_no for h in r.hits] == ["제1조", "제3조"]
    assert r.provenance["candidates_scorable"] == 2
