"""판정 파이프라인의 인용 검증 연결.

★규칙 엔진이 판정을 소유하고 LLM 은 설명만 만든다.
  이 테스트는 그 경계가 지켜지는지 본다.
"""

from app.core.domain.precheck_result import ReasonCode
from app.core.ports.precheck import ClauseRow
from app.core.usecases.precheck import verify_explanation


def _row(qno: str, text: str) -> ClauseRow:
    return ClauseRow(
        sha256="a" * 64,
        qualified_no=qno,
        clause_no="",
        section="",
        title="",
        text=text,
        page_from=1,
        page_to=1,
        content_hash="h" * 64,
    )


_EV = [
    _row("보통약관/제9조", "회사는 자기부담금을 공제하고 지급합니다."),
    _row("특별약관/제3조", "정신질환은 보상하지 않습니다."),
]


def test_근거_안의_조항을_인용하면_통과한다():
    ok, code, _ = verify_explanation(
        cited_clauses=["보통약관/제9조"], evidence=_EV
    )
    assert ok
    assert code is None


def test_지어낸_조항을_인용하면_설명을_버린다():
    ok, code, msg = verify_explanation(
        cited_clauses=["보통약관/제77조"], evidence=_EV
    )
    assert not ok
    assert code is ReasonCode.CITATION_UNVERIFIED
    assert "제77조" in msg


def test_어느_조항인지_모르면_별도_사유로_기권한다():
    """★같은 번호가 여러 특약에 있을 때. 통과도 폐기도 아니다."""
    ev = [
        _row("보통약관/1.", "보장종목은 상해와 질병입니다."),
        _row("특별약관/1.", "이 특별약관의 보장종목은 다릅니다."),
    ]
    ok, code, msg = verify_explanation(cited_clauses=["1."], evidence=ev)
    assert not ok
    assert code is ReasonCode.AMBIGUOUS_CITATION
    assert "특정할 수 없습니다" in msg


def test_인용_없이_설명만_있으면_버린다():
    ok, code, _ = verify_explanation(
        cited_clauses=[], evidence=_EV, answer_text="보장됩니다."
    )
    assert not ok
    assert code is ReasonCode.CITATION_UNVERIFIED


def test_인용문이_원문과_다르면_버린다():
    ok, code, msg = verify_explanation(
        cited_clauses=["보통약관/제9조"],
        evidence=_EV,
        quotes={"보통약관/제9조": "회사는 전액을 지급합니다"},
    )
    assert not ok
    assert code is ReasonCode.CITATION_UNVERIFIED
