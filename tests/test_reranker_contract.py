"""리랭커 계약 — **조용히 순서를 지어내지 않는다.**

★왜 이 테스트가 먼저인가 (2026-08-04)
  리랭커 구현(`CrossEncoderReranker`·`rerank_hits`)은 있는데 **테스트가 0건**이었다.
  그런데 이 코드가 지키기로 한 것은 전부 «실패할 때의 행동»이다 —
  점수가 안 나오면 멈추고, 후보에 없던 조항이 나오면 멈춘다.
  그 행동은 **정상 경로 테스트로는 절대 드러나지 않는다.**

★리랭커는 근거를 만들지 않는다. 순서만 바꾼다.
  후보에 없던 것이 결과에 나타나면 결함이고, 그때는 판정을 멈춰야 한다
  (CLAUDE.md §0 — 폴백으로 조용히 메우지 않는다).

★여기서는 모델을 내려받지 않는다. `CrossEncoderReranker(model=...)` 로
  가짜 채점기를 주입한다. 무게추를 받아야만 도는 테스트는 CI 에서 죽는다.
"""

from __future__ import annotations

import pytest

from app.adapters.clause_rerank import rerank_hits
from app.adapters.pgvector_clause_index import ClauseHit
from app.adapters.reranker import CrossEncoderReranker
from app.application.ports import Evidence


class _FakeCross:
    """`CrossEncoder.predict` 흉내. 돌려줄 점수를 그대로 준다."""

    def __init__(self, scores):
        self._scores = scores
        self.seen: list[tuple[str, str]] = []

    def predict(self, pairs, **_kw):
        self.seen = list(pairs)
        return list(self._scores)


def _ev(content: str, locator: str) -> Evidence:
    return Evidence(content=content, source="doc", locator=locator, score=0.0, backend="x")


def _hit(clause_no: str, text: str, full: str = "") -> ClauseHit:
    return ClauseHit(
        content_hash=f"h-{clause_no}",
        chunk_ix=0,
        text=text,
        distance=0.5,
        sha256="a" * 64,
        insurer="삼성화재",
        qualified_no=clause_no,
        section="보통약관",
        title="보상하지 않는 사항",
        page_from=1,
        page_to=2,
        full_text=full,
    )


# ── CrossEncoderReranker ────────────────────────────────────────────────

def test_점수_내림차순으로_다시_줄세운다():
    r = CrossEncoderReranker("fake", model=_FakeCross([0.1, 0.9, 0.5]))
    out = r.rerank("질의", [_ev("a", "1"), _ev("b", "2"), _ev("c", "3")])
    assert [e.locator for e in out] == ["2", "3", "1"]
    #: 반환 순서와 score 가 같은 뜻이어야 한다(RetrieverPort 계약).
    assert [e.score for e in out] == sorted((e.score for e in out), reverse=True)


def test_후보가_없으면_모델을_부르지_않는다():
    fake = _FakeCross([])
    assert CrossEncoderReranker("fake", model=fake).rerank("질의", []) == []
    assert fake.seen == []


def test_top_n_으로_자른다():
    r = CrossEncoderReranker("fake", model=_FakeCross([0.1, 0.9, 0.5]))
    out = r.rerank("질의", [_ev("a", "1"), _ev("b", "2"), _ev("c", "3")], top_n=2)
    assert [e.locator for e in out] == ["2", "3"]


def test_점수_개수가_다르면_멈춘다():
    """★조용히 zip 으로 잘라내면 **엉뚱한 조항에 점수가 붙는다.**"""
    r = CrossEncoderReranker("fake", model=_FakeCross([0.5]))
    with pytest.raises(RuntimeError, match="score count mismatch"):
        r.rerank("질의", [_ev("a", "1"), _ev("b", "2")])


def test_점수가_전부_같으면_멈춘다():
    """체크포인트·채점 어댑터가 어긋나면 상수를 뱉는다. 그건 재정렬이 아니다."""
    r = CrossEncoderReranker("fake", model=_FakeCross([0.3, 0.3, 0.3]))
    with pytest.raises(RuntimeError, match="constant scores"):
        r.rerank("질의", [_ev("a", "1"), _ev("b", "2"), _ev("c", "3")])


def test_점수가_유한하지_않으면_멈춘다():
    r = CrossEncoderReranker("fake", model=_FakeCross([float("nan"), 0.2]))
    with pytest.raises(RuntimeError, match="non-finite"):
        r.rerank("질의", [_ev("a", "1"), _ev("b", "2")])


@pytest.mark.parametrize("bad", [{"batch_size": 0}, {"max_length": 0}, {"dtype": "int8"}])
def test_설정이_틀리면_생성_시점에_막는다(bad):
    """첫 질의까지 미루면 **운영 중에** 터진다."""
    with pytest.raises(ValueError):
        CrossEncoderReranker("fake", model=_FakeCross([]), **bad)


def test_모델을_안_주면_생성만으로는_안_받아온다():
    """지연 로딩 — import·기동만으로 4B 무게추를 내려받으면 안 된다."""
    r = CrossEncoderReranker("존재하지-않는/모델")
    assert r._model is None


# ── rerank_hits (조항 경로) ─────────────────────────────────────────────

class _ByLocator:
    """지정한 순서대로 돌려주는 리랭커."""

    def __init__(self, order): self._order = order

    def rerank(self, query, evidence, top_n=None):
        rank = {loc: i for i, loc in enumerate(self._order)}
        out = sorted(evidence, key=lambda e: rank.get(e.locator, 999))
        return out[:top_n] if top_n else out


def test_조항을_다시_줄세운다():
    hits = [_hit("제1조", "가"), _hit("제2조", "나"), _hit("제3조", "다")]
    ids = [h.clause_id for h in hits]
    out = rerank_hits(_ByLocator([ids[2], ids[0], ids[1]]), "질의", hits)
    assert [h.clause_id for h in out] == [ids[2], ids[0], ids[1]]


def test_후보가_비면_리랭커를_부르지_않는다():
    class _Boom:
        def rerank(self, *a, **k): raise AssertionError("불리면 안 된다")

    assert rerank_hits(_Boom(), "질의", []) == []


def test_같은_조항이_둘이면_멈춘다():
    """★중복률 66% 라 `clause_id` 가 겹치면 되돌릴 때 실제로 섞인다."""
    h = _hit("제1조", "가")
    with pytest.raises(ValueError, match="clause_id 가 겹칩니다"):
        rerank_hits(_ByLocator([]), "질의", [h, h])


def test_후보에_없던_조항을_돌려주면_멈춘다():
    """★리랭커가 **근거를 만들어 냈다.** 판정에 쓸 수 없다."""

    class _Fabricate:
        def rerank(self, query, evidence, top_n=None):
            return [Evidence(content="지어낸 것", source="s", locator="없는/조항#1",
                             score=1.0, backend="x")]

    with pytest.raises(ValueError, match="후보에 없던 조항"):
        rerank_hits(_Fabricate(), "질의", [_hit("제1조", "가")])


class _Capture:
    """채점에 실제로 들어간 본문을 잡아 둔다."""

    def __init__(self): self.seen: list[str] = []

    def rerank(self, query, evidence, top_n=None):
        self.seen = [e.content for e in evidence]
        return evidence


def test_기본은_조각으로_채점한다():
    """★실측으로 뒤집힌 자리다(2026-08-05).

    여기 「조 전체를 넣는다」가 못박혀 있었다. 이유(예외가 뒤에 온다)는 맞지만
    그건 **최종 답**에 관한 것이지 **순위**에 관한 것이 아니었다.
    조각이 hit@1 을 5.04%p 더 낸다 — 면책을 다른 말로 물으면 +19.81%p.
    """
    cap = _Capture()
    rerank_hits(cap, "질의", [_hit("제1조", "조각만", full="조 전체입니다. 다만 예외가 있습니다")])
    assert cap.seen == ["조각만"]


def test_설정으로_조_전체_채점을_되살릴_수_있다():
    """옛 동작은 지우지 않고 남긴다 — 비교·회귀용이다."""
    cap = _Capture()
    rerank_hits(cap, "질의", [_hit("제1조", "조각만", full="조 전체입니다. 다만 예외가 있습니다")],
                score_body="full_clause")
    assert cap.seen == ["조 전체입니다. 다만 예외가 있습니다"]


def test_조각이_비면_조_전체로_대신_채점하지_않는다():
    """★조용한 폴백이었다(코덱스 지적 2026-08-05).

    응답은 `score_body=chunk` 라고 말하는데 실제로는 조 전체로 채점한 상태가 되고,
    그 질의만 다른 기준으로 줄 세워진다. 어느 본문으로 쟀는지가 결과를 5%p 가른다.
    """
    with pytest.raises(ValueError, match="채점할 본문이 비었습니다"):
        rerank_hits(_Capture(), "질의", [_hit("제1조", "", full="조 전체입니다")])


def test_모르는_score_body_는_거절한다():
    """★오타를 조용히 기본값으로 흘리면 **어느 쪽으로 쟀는지 모르게 된다.**"""
    with pytest.raises(ValueError, match="chunk|full_clause"):
        rerank_hits(_Capture(), "질의", [_hit("제1조", "가")], score_body="full")


def test_채점_본문_길이를_자른다():
    """조 전체는 3만 자까지 있다. 그대로 넣으면 프롬프트 예산을 넘는다."""
    cap = _Capture()
    rerank_hits(cap, "질의", [_hit("제1조", "가" * 5000)], score_chars=100)
    assert cap.seen == ["가" * 100]


# ── RerankedRetriever (커머스 RAG 배선) ─────────────────────────────────

class _Base:
    """base RetrieverPort 흉내 — 몇 개를 달라고 했는지 기록한다."""

    backend = "fake"

    def __init__(self, items): self._items, self.asked = items, None

    def search(self, query, k=None, source=None):
        self.asked = k
        return self._items[: k or len(self._items)]


def test_리랭커_래퍼는_요청보다_넉넉히_가져온다():
    """★k 개만 가져와 재정렬하면 벡터가 놓친 것을 되살릴 수 없다."""
    from app.adapters.reranker import RerankedRetriever

    base = _Base([_ev(str(i), str(i)) for i in range(30)])
    r = CrossEncoderReranker("fake", model=_FakeCross([i / 30 for i in range(20)]))
    RerankedRetriever(base, r, over_fetch=20).search("질의", k=3)
    assert base.asked == 20


def test_요청_k_가_over_fetch_보다_크면_그만큼_가져온다():
    from app.adapters.reranker import RerankedRetriever

    base = _Base([_ev(str(i), str(i)) for i in range(40)])
    r = CrossEncoderReranker("fake", model=_FakeCross([i / 40 for i in range(35)]))
    RerankedRetriever(base, r, over_fetch=10).search("질의", k=35)
    assert base.asked == 35


# ── LlmReranker (provider=llm 배선) ────────────────────────────────────

class _Model:
    def __init__(self, replies): self._r = list(replies)

    def complete(self, prompt, **_kw): return self._r.pop(0)


def test_llm_점수로_다시_줄세운다():
    from app.adapters.reranker import LlmReranker

    out = LlmReranker(_Model(["2", "9", "5"])).rerank(
        "질의", [_ev("a", "1"), _ev("b", "2"), _ev("c", "3")])
    assert [e.locator for e in out] == ["2", "3", "1"]
    assert out[0].score == pytest.approx(0.9)


@pytest.mark.parametrize("reply", ["잘 모르겠습니다", "8 and 20", "1e2", "", "-3", "50"])
def test_llm_이_점수를_못_주면_멈춘다(reply):
    """★첫 숫자만 몰래 취하거나 기본값으로 때우지 않는다(무폴백)."""
    from app.adapters.reranker import LlmReranker
    from app.core.errors import LLMOutputError

    with pytest.raises(LLMOutputError):
        LlmReranker(_Model([reply])).rerank("질의", [_ev("a", "1")])
