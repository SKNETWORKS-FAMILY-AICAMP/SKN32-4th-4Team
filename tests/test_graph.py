"""판정 흐름(LangGraph) — 계약 §1 의 경계가 지켜지는가.

★이 테스트가 지키는 것은 **흐름의 경계**다.
  "그래프가 판정을 바꾸지 않는다" · "재시도는 2회를 넘지 않는다" ·
  "상태에 질병기호 원문이 없다" · "두 실행기의 흐름이 같다".
"""

from __future__ import annotations

import pytest

from app.core.domain.precheck_result import (
    CitationRef,
    PrecheckInput,
    PrecheckOutcome,
    ReasonCode,
    Verdict,
)
from app.workflow.precheck_graph import MAX_RETRY, GraphState, PrecheckGraph


def _body(codes=("F32",)) -> PrecheckInput:
    return PrecheckInput(insurer="가보험", enrolled_on="20200101", kcd_codes=tuple(codes))


#: ★판정에는 근거가 붙어 있어야 한다. 인용 0건은 이제 fail-closed 다.
_CITE = CitationRef(
    clause_id="abc123def456/보통약관/제9조#0f0f0f0f",
    qualified_no="보통약관/제9조",
    section="보통약관",
    title="지급보험금의 계산",
    quote="회사는 …",
    page_from=41,
    page_to=41,
)


def _outcome(verdict=Verdict.LIKELY_COVERED, **kw) -> PrecheckOutcome:
    kw.setdefault("citations", [_CITE])
    return PrecheckOutcome(
        verdict=verdict,
        abstained=kw.pop("abstained", False),
        message=kw.pop("message", "보장 조항 근거가 있습니다."),
        **kw,
    )


def _graph(*, verify, retarget=None, run=None) -> PrecheckGraph:
    return PrecheckGraph(
        run_precheck=run or (lambda b: _outcome()),
        verify=verify,
        retarget=retarget,
    )


def test_통과하면_판정을_그대로_나른다():
    g = _graph(verify=lambda o: (True, None, ""))
    out, st = g.invoke(_body())
    #: ★그래프가 verdict 를 바꾸지 않는다. 규칙엔진이 정한 것 그대로다.
    assert out.verdict is Verdict.LIKELY_COVERED
    assert out.abstained is False
    assert st.done and st.retries == 0


def test_상태에_질병기호_원문이_없다():
    """★질병기호는 민감정보다. 상태는 로그·트레이스로 새어 나갈 수 있다."""
    g = _graph(verify=lambda o: (True, None, ""))
    _, st = g.invoke(_body(codes=("F32", "S82")))
    dumped = repr(st)
    assert "F32" not in dumped and "S82" not in dumped
    assert len(st.kcd_hashes) == 2
    assert all(len(h) == 16 for h in st.kcd_hashes)


def test_검증_실패하고_되돌아갈_수단이_없으면_기권한다():
    g = _graph(verify=lambda o: (False, ReasonCode.CITATION_UNVERIFIED, "인용 불일치"))
    out, st = g.invoke(_body())
    assert out.verdict is Verdict.NEEDS_EXPERT
    assert out.abstained is True
    assert out.reason_code is ReasonCode.CITATION_UNVERIFIED
    #: ★검증에 실패한 근거를 남기지 않는다. 남기면 클라이언트가 그걸 읽고 판단한다.
    assert out.citations == () and out.per_code == ()


def test_재시도는_2회를_넘지_않는다():
    """★계약 §1. 그때까지 안 되면 근거가 없는 것이다."""
    calls = {"n": 0}

    def verify(o):
        calls["n"] += 1
        #: 매번 **다른** 이유로 실패시켜 "같은 이유 차단"에 걸리지 않게 한다.
        code = (
            ReasonCode.CITATION_UNVERIFIED
            if calls["n"] % 2
            else ReasonCode.AMBIGUOUS_CITATION
        )
        return False, code, f"실패 {calls['n']}"

    g = _graph(verify=verify, retarget=lambda b, o: o)
    out, st = g.invoke(_body())
    assert st.retries <= MAX_RETRY
    assert out.abstained is True
    assert out.reason_code is ReasonCode.CITATION_UNVERIFIED


def test_같은_이유로는_다시_돌지_않는다():
    """돌아도 같은 결과가 나온다. 시간만 쓴다."""
    g = _graph(
        verify=lambda o: (False, ReasonCode.CITATION_UNVERIFIED, "같은 이유"),
        retarget=lambda b, o: o,
    )
    out, st = g.invoke(_body())
    assert st.retries == 1
    assert st.retry_reasons == ["citation_unverified"]
    assert out.abstained is True


def test_표적검색으로_근거를_보강하면_통과한다():
    state = {"tries": 0}

    def verify(o):
        state["tries"] += 1
        return (True, None, "") if state["tries"] > 1 else (
            False, ReasonCode.NO_EVIDENCE, "근거 부족"
        )

    def retarget(b, o):
        #: ★여기서도 verdict 를 바꾸지 않는다. 근거만 더 모은다.
        return o

    g = _graph(verify=verify, retarget=retarget)
    out, st = g.invoke(_body())
    assert out.verdict is Verdict.LIKELY_COVERED
    assert out.abstained is False
    assert st.retries == 1


def test_규칙엔진이_기권했으면_그대로_둔다():
    """★그래프가 기권을 뒤집지 않는다."""
    abstained = _outcome(
        verdict=Verdict.NEEDS_EXPERT,
        abstained=True,
        reason_code=ReasonCode.NO_VERSION_AT_DATE,
        message="해당 시점의 약관을 찾지 못했습니다.",
    )
    g = _graph(run=lambda b: abstained, verify=lambda o: (True, None, ""))
    out, _ = g.invoke(_body())
    assert out.verdict is Verdict.NEEDS_EXPERT
    assert out.reason_code is ReasonCode.NO_VERSION_AT_DATE


def test_흐름이_계약_순서를_따른다():
    g = _graph(verify=lambda o: (True, None, ""))
    _, st = g.invoke(_body())
    assert st.trail == [
        "normalize",
        "resolve_policy",
        "gate_document",
        "retrieve",
        "assess",
        "explain",
        "verify_citations",
    ]


def test_무한반복이_구조로_막힌다():
    """★흐름 버그로 끝나지 않으면 서비스가 멈춘다. 이중으로 잠근다."""

    class _NeverDone(PrecheckGraph):
        def verify_citations(self, st: GraphState, body):  # noqa: D102
            st.visit("verify_citations")
            return st  # done 을 영원히 세우지 않는다

    g = _NeverDone(
        run_precheck=lambda b: _outcome(),
        verify=lambda o: (False, ReasonCode.CITATION_UNVERIFIED, ""),
    )
    out, st = g.invoke(_body())
    assert st.done is True
    assert out.abstained is True


# ---------------------------------------------------------------- LangGraph


def test_두_실행기의_흐름이_같다():
    """★`invoke()` 와 LangGraph 가 갈라지면 어느 쪽이 진짜인지 알 수 없다."""
    pytest.importorskip("langgraph")

    g = _graph(verify=lambda o: (True, None, ""))
    body = _body()

    _, seq = g.invoke(body)

    st = GraphState()
    compiled = g.build_langgraph(body)
    #: ★상태에 body 를 넣지 않는다 — 체크포인트에 질병기호 원문이 남는다.
    compiled.invoke({"st": st})

    assert st.trail == seq.trail
    assert st.done == seq.done
    assert st.retries == seq.retries


def test_langgraph도_재시도_상한을_지킨다():
    pytest.importorskip("langgraph")

    calls = {"n": 0}

    def verify(o):
        calls["n"] += 1
        code = (
            ReasonCode.CITATION_UNVERIFIED
            if calls["n"] % 2
            else ReasonCode.AMBIGUOUS_CITATION
        )
        return False, code, "실패"

    g = _graph(verify=verify, retarget=lambda b, o: o)
    st = GraphState()
    g.build_langgraph(_body()).invoke({"st": st})
    assert st.retries <= MAX_RETRY
    assert st.done is True


# ── 오늘 수정분 회귀 (코덱스 3라운드) ──────────────────────────────


def test_근거_없는_양성판정은_기권으로_막힌다():
    """★전에는 인용 0건이 그대로 통과했다 — 근거 없는 "보장됩니다"가 나갔다."""
    naked = PrecheckOutcome(verdict=Verdict.LIKELY_COVERED, message="보장됩니다", citations=[])
    g = _graph(run=lambda b: naked, verify=lambda o: (True, None, ""))
    out, st = g.invoke(_body())
    assert out.abstained is True
    assert out.reason_code is ReasonCode.CITATION_UNVERIFIED
    assert out.citations == ()
    assert st.clauses == ()


def test_이미_기권한_결과의_사유를_덮지_않는다():
    """★규칙엔진이 정한 기권 사유를 citation_unverified 로 바꿔치면 안 된다."""
    already = PrecheckOutcome(
        verdict=Verdict.NEEDS_EXPERT,
        abstained=True,
        reason_code=ReasonCode.NO_EVIDENCE,
        message="근거를 찾지 못했습니다",
        citations=[],
    )
    g = _graph(run=lambda b: already, verify=lambda o: (True, None, ""))
    out, _ = g.invoke(_body())
    assert out.reason_code is ReasonCode.NO_EVIDENCE  # ★바뀌지 않았다
    assert out.abstained is True


def test_기권하면_상태의_인용도_비운다():
    """★응답만 비우고 상태에 남기면 감사와 응답이 갈라진다."""
    g = _graph(verify=lambda o: (False, ReasonCode.CITATION_UNVERIFIED, "불일치"))
    out, st = g.invoke(_body())
    assert out.abstained is True
    assert out.citations == ()
    assert st.clauses == ()


# ── ★자기 인증 제거 회귀 (코덱스 3라운드 치명) ──────────────────────


class _FakeStore:
    """조항 저장소. **판정 결과와 독립된 출처**여야 한다."""

    def __init__(self, rows):
        self._rows = rows

    def load_clauses(self, sha256, *, usable_only=True):
        return list(self._rows)


def _row(**kw):
    from app.core.ports.precheck import ClauseRow

    base = dict(
        sha256="abc123def456" + "0" * 52,
        qualified_no="보통약관/제9조",
        clause_no="제9조",
        section="보통약관",
        title="지급보험금의 계산",
        text="회사는 보험금을 다음과 같이 계산하여 지급합니다.",
        page_from=41,
        page_to=41,
        content_hash="0f0f0f0f" + "0" * 56,
        #: ★★인용 검증이 **수록 식별자로 한 행을 특정**한다(2026-08-03 계약 변경).
        #:   식별자가 없으면 기권하므로 픽스처도 채워야 한다 —
        #:   전에는 없어도 통과했는데, 그게 곧 **아무 행이나 대조**할 수 있다는 뜻이었다.
        ordinal=0,
        release_id="test-release",
        #: 공통 게이트(`app/core/domain/eligibility.py`)가 보는 값
        citation_eligible=True,
        is_statute=False,
        parse_status="ok",
    )
    base.update(kw)
    return ClauseRow(**base)


def _outcome_with(cite) -> PrecheckOutcome:
    from app.core.domain.precheck_result import AppliedPolicyInfo

    return PrecheckOutcome(
        verdict=Verdict.LIKELY_COVERED,
        citations=[cite],
        applied_policy=AppliedPolicyInfo(
            sha256="abc123def456" + "0" * 52,
            insurer="가보험",
            product_name="가상품",
            sale_start="20200101",
        ),
    )


def test_저장소에_없는_조항을_인용하면_막힌다():
    """★핵심. 전에는 인용이 스스로를 증명했다 — 조작해도 통과했다."""
    from app.workflow.precheck_graph import verify_against_store

    row = _row()
    forged = CitationRef(
        clause_id="abc123def456/보통약관/제99조#deadbeef",
        qualified_no="보통약관/제99조",
        quote="아무 말이나 지어낸 인용",
    )
    ok, code, msg = verify_against_store(_outcome_with(forged), _FakeStore([row]))
    assert ok is False
    assert code is ReasonCode.CITATION_UNVERIFIED
    assert "저장소에 없는" in msg


def test_인용문이_원문에_없으면_막힌다():
    from app.workflow.precheck_graph import verify_against_store

    row = _row()
    lying = CitationRef(
        clause_id=row.clause_id,
        qualified_no=row.qualified_no,
        quote="회사는 어떤 경우에도 전액 보상합니다",  # 원문에 없다
        page_from=41,
        page_to=41,
    )
    ok, code, msg = verify_against_store(_outcome_with(lying), _FakeStore([row]))
    assert ok is False
    assert "인용문이 원문에 없습니다" in msg


def test_쪽_범위가_원문_밖이면_막힌다():
    from app.workflow.precheck_graph import verify_against_store

    row = _row()
    bad_page = CitationRef(
        clause_id=row.clause_id,
        qualified_no=row.qualified_no,
        quote="회사는 보험금을",
        page_from=41,
        page_to=99,
    )
    ok, _, msg = verify_against_store(_outcome_with(bad_page), _FakeStore([row]))
    assert ok is False
    assert "쪽 범위" in msg


def test_저장소_장애는_통과가_아니라_실패다():
    """★읽지 못했다는 이유로 검증을 건너뛰면 그게 폴백이다."""
    from app.workflow.precheck_graph import verify_against_store

    class _Broken:
        def load_clauses(self, sha256, *, usable_only=True):
            raise RuntimeError("DB 접속 실패")

    ok, code, msg = verify_against_store(_outcome_with(_CITE), _Broken())
    assert ok is False
    assert code is ReasonCode.CITATION_UNVERIFIED


def test_원문과_일치하면_통과한다():
    from app.workflow.precheck_graph import verify_against_store

    row = _row()
    good = CitationRef(
        clause_id=row.clause_id,
        qualified_no=row.qualified_no,
        quote="회사는 보험금을 다음과 같이",
        page_from=41,
        page_to=41,
    )
    ok, _, msg = verify_against_store(_outcome_with(good), _FakeStore([row]))
    assert ok is True, msg
