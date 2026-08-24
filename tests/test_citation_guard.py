"""인용 검증 — 지어낸 조항·틀린 인용문·우회를 거르는지.

★v1 은 87% 문서에서 무력했다. 그 결함들이 다시 생기지 않게 못박는다.
"""

from app.core.domain.citation_guard import (
    EvidenceClause,
    Resolution,
    make_handles,
    verify,
)

#: 실제 약관 구조를 본뜬 근거.
_EV = [
    EvidenceClause(
        qualified_no="보통약관/제9조",
        text="회사는 보험금을 지급할 때 자기부담금을 공제합니다.",
    ),
    EvidenceClause(
        qualified_no="특별약관/제3조",
        text="정신질환은 보상하지 않습니다.",
    ),
]

#: ★한 문서에서 `1.` 이 여러 부에 있는 실제 상황(실측: 최대 96개).
_EV_COLLIDING = [
    EvidenceClause(qualified_no="보통약관/1.", text="보장종목은 상해와 질병입니다."),
    EvidenceClause(qualified_no="특별약관/1.", text="이 특별약관의 보장종목은 다릅니다."),
    EvidenceClause(
        qualified_no="이륜자동차 운전중상해 부담보 특별약관/1.",
        text="특별약관의 체결 및 효력에 관한 사항입니다.",
    ),
]


# ── 기본 ──────────────────────────────────────────────────────────────
def test_전체_경로로_인용하면_통과한다():
    r = verify(cited_clauses=["보통약관/제9조"], evidence=_EV)
    assert r.ok
    assert r.checks[0].resolution is Resolution.EXACT


def test_근거에_없는_조항을_인용하면_폐기한다():
    r = verify(cited_clauses=["보통약관/제15조"], evidence=_EV)
    assert not r.ok
    assert r.reason_code == "citation_not_in_evidence"


def test_번호만_와도_후보가_하나면_해소한다():
    r = verify(cited_clauses=["제9조"], evidence=_EV)
    assert r.ok
    assert r.checks[0].resolution is Resolution.RESOLVED


def test_공백_표기_차이를_흡수한다():
    r = verify(cited_clauses=["보통약관/제 9 조"], evidence=_EV)
    assert r.ok


# ── ★v1 의 핵심 결함 ──────────────────────────────────────────────────
def test_같은_번호가_여러_부에_있으면_기권한다():
    """★v1 은 부 이름을 떼서 `보통약관/1.` `특별약관/1.` 을 같은 것으로 봤다.

    그래서 LLM 이 **어느 특약의 1조를 인용하든** 통과했다(87% 문서에서).
    이제는 특정할 수 없다고 말한다 — 통과시키지도, 무작정 폐기하지도 않는다.
    """
    r = verify(cited_clauses=["1."], evidence=_EV_COLLIDING)
    assert not r.ok
    assert r.reason_code == "ambiguous_citation"
    assert len(r.checks[0].candidates) == 3


def test_부를_명시하면_충돌_상황에서도_통과한다():
    r = verify(cited_clauses=["특별약관/1."], evidence=_EV_COLLIDING)
    assert r.ok
    assert r.checks[0].matched.text.startswith("이 특별약관의")


def test_손잡이로_인용하면_충돌이_없다():
    """★근거마다 `E001` 을 주면 이름·번호를 정확히 쓸 필요가 없다."""
    ev = make_handles(_EV_COLLIDING)
    r = verify(cited_clauses=["E002"], evidence=ev)
    assert r.ok
    assert r.checks[0].resolution is Resolution.EXACT
    assert r.checks[0].matched.qualified_no == "특별약관/1."


def test_없는_손잡이는_폐기한다():
    ev = make_handles(_EV_COLLIDING)
    r = verify(cited_clauses=["E099"], evidence=ev)
    assert not r.ok
    assert r.reason_code == "citation_not_in_evidence"


def test_부를_썼는데_틀리면_번호로_강등하지_않는다():
    """`없는특약/제9조` 는 제9조가 근거에 있어도 통과하면 안 된다."""
    r = verify(cited_clauses=["없는특약/제9조"], evidence=_EV)
    assert not r.ok
    assert r.reason_code == "citation_not_in_evidence"


# ── ★코덱스가 찾은 우회 경로 ─────────────────────────────────────────
def test_인용이_아예_없으면_폐기한다():
    """★v1 은 `ok = not unknown` 이라 빈 인용도 통과했다."""
    r = verify(cited_clauses=[], evidence=_EV, answer_text="보장됩니다.")
    assert not r.ok
    assert r.reason_code == "no_citation"


def test_본문에서만_말하고_선언하지_않으면_폐기한다():
    """★v1 은 경고로만 뒀다. 그러면 선언을 비우고 본문에만 써서 우회할 수 있다."""
    r = verify(
        cited_clauses=["보통약관/제9조"],
        evidence=_EV,
        answer_text="제9조와 제77조에 따라 보장됩니다.",
    )
    assert not r.ok
    assert r.reason_code == "undeclared_citation"
    assert "제77조" in r.undeclared_mentions


def test_인용한_조항_자신은_선언_누락이_아니다():
    r = verify(
        cited_clauses=["보통약관/제9조"],
        evidence=_EV,
        answer_text="제9조에 따라 자기부담금을 공제합니다.",
    )
    assert r.ok


def test_인용문이_원문에_없으면_폐기한다():
    """★v1 은 경고로만 뒀다."""
    r = verify(
        cited_clauses=["보통약관/제9조"],
        evidence=_EV,
        quotes={"보통약관/제9조": "회사는 전액을 지급합니다"},
    )
    assert not r.ok
    assert r.reason_code == "quote_not_in_source"


def test_빈_인용문은_통과가_아니라_실패다():
    """★`quote=""` 로 대조를 통째로 건너뛸 수 있으면 안 된다."""
    r = verify(
        cited_clauses=["보통약관/제9조"],
        evidence=_EV,
        quotes={"보통약관/제9조": ""},
    )
    assert not r.ok
    assert r.reason_code == "quote_not_in_source"


def test_인용문이_원문에_있으면_통과한다():
    r = verify(
        cited_clauses=["보통약관/제9조"],
        evidence=_EV,
        quotes={"보통약관/제9조": "자기부담금을 공제합니다"},
    )
    assert r.ok


def test_같은_조항의_인용문_여러_개를_모두_본다():
    """★v1 은 dict 라 마지막 것만 남았다."""
    r = verify(
        cited_clauses=["보통약관/제9조"],
        evidence=_EV,
        quotes={"보통약관/제9조": ["자기부담금을 공제합니다", "전액 지급합니다"]},
    )
    assert not r.ok  # 두 번째가 원문에 없다
    assert r.reason_code == "quote_not_in_source"


def test_조항_표기가_아니면_invalid_다():
    """★v1 은 `search` 라 `"아무말 제9조 아무말"` 이 통과했다."""
    r = verify(cited_clauses=["우울증은 보장됩니다"], evidence=_EV)
    assert not r.ok
    assert r.reason_code == "invalid_citation"


def test_조와_호를_구분한다():
    """★`제1조` 와 `1.` 은 문법 층이 다르다. v1 은 같은 키로 만들었다."""
    ev = [EvidenceClause(qualified_no="보통약관/제1조", text="목적")]
    r = verify(cited_clauses=["1."], evidence=ev)
    assert not r.ok  # `1.` 로는 `제1조` 에 닿지 않는다


def test_조항의_가지번호를_구분한다():
    ev = [
        EvidenceClause(qualified_no="보통약관/제9조", text="본조"),
        EvidenceClause(qualified_no="보통약관/제9조의2", text="가지조"),
    ]
    r = verify(cited_clauses=["제9조의2"], evidence=ev)
    assert r.ok
    assert r.checks[0].matched.text == "가지조"


def test_같은_경로_후보가_여럿이면_인용문이_든_것을_고른다():
    """★`by_path` 가 dict 라 **뒤에 온 것만 남았다**(코덱스 지적 2026-08-04).

    같은 `qualified_no` 가 여러 문서·여러 쪽에 실린다 — 중복률 66% 코퍼스에서
    드문 일이 아니다. 덮어쓰면 인용문이 **앞의 것에 있어도 못 찾아**
    `quote_mismatch` 가 난다. 근거가 있는데 없다고 말하는 것이다.

    ★같은 파일 위쪽에 「번호 색인은 list 다. dict 로 만들면 덮인다」고
      적어 놓고, 바로 아래 줄에서 같은 실수를 하고 있었다.
    """
    from app.core.domain.citation_guard import EvidenceClause, verify

    ev = [
        EvidenceClause(qualified_no="보통약관/제9조", text="첫 번째 문서의 본문이다."),
        EvidenceClause(qualified_no="보통약관/제9조", text="두 번째 문서의 본문이다."),
    ]
    #: 인용문이 **앞의 것**에 있다 — 덮어쓰던 시절엔 못 찾았다.
    r = verify(cited_clauses=["보통약관/제9조"], evidence=ev,
               quotes={"보통약관/제9조": ["첫 번째 문서의"]})
    c = r.checks[0]
    #: ★`quote_mismatch` 는 어긋났을 때만 채워진다(없으면 빈 문자열).
    assert not c.quote_mismatch, f"인용문이 있는데 못 찾았습니다: {c.quote_mismatch!r}"

    #: 뒤의 것에 있어도 찾아야 한다.
    r2 = verify(cited_clauses=["보통약관/제9조"], evidence=ev,
                quotes={"보통약관/제9조": ["두 번째 문서의"]})
    assert not r2.checks[0].quote_mismatch

    #: ★어디에도 없으면 **사유가 남아야** 한다. 조용히 통과시키면 안 된다.
    r3 = verify(cited_clauses=["보통약관/제9조"], evidence=ev,
                quotes={"보통약관/제9조": ["없는 문장"]})
    assert r3.checks[0].quote_mismatch


def test_검증된_quote_안의_준용_조항은_미선언으로_오탐하지_않는다():
    quote = "회사는 제3조(보험금의 지급사유)에서 정한 사유가 발생한 경우 보상합니다."
    ev = [
        EvidenceClause(
            qualified_no="보통약관/제9조",
            text=quote,
            handle="E001",
            clause_id="clause-9",
        )
    ]

    r = verify(
        cited_clauses=["E001"],
        evidence=ev,
        answer_text=f"제9조에 따른 결과입니다. 다만 {quote}",
        quotes={"E001": quote},
    )

    assert r.ok
    assert r.undeclared_mentions == []


def test_quote_밖에서_새로_주장한_조항은_계속_차단한다():
    quote = "회사는 제3조에서 정한 지급사유가 발생한 경우 보상합니다."
    ev = [EvidenceClause("보통약관/제9조", quote, "E001", "clause-9")]

    r = verify(
        cited_clauses=["E001"],
        evidence=ev,
        answer_text=f"제9조에 따른 결과입니다. {quote} 별도로 제4조에 따라 지급합니다.",
        quotes={"E001": quote},
    )

    assert not r.ok
    assert r.reason_code == "undeclared_citation"
    assert r.undeclared_mentions == ["제4조"]


def test_근거와_어긋난_quote는_준용_조항을_면제하지_않는다():
    ev = [
        EvidenceClause(
            "보통약관/제9조",
            "회사는 제3조에서 정한 사유가 발생하면 지급합니다.",
            "E001",
            "clause-9",
        )
    ]

    r = verify(
        cited_clauses=["E001"],
        evidence=ev,
        answer_text="제9조의 근거에는 제4조에서 정한 사유가 있다고 적었습니다.",
        quotes={"E001": "제4조에서 정한 사유"},
    )

    assert not r.ok
    assert r.reason_code == "quote_not_in_source"
    assert r.undeclared_mentions == ["제4조"]


def test_quote_payload가_답변에_없으면_다른_mention을_면제하지_않는다():
    quote = "제3조에서 정한 지급사유가 발생한 경우"
    ev = [EvidenceClause("보통약관/제9조", quote, "E001", "clause-9")]

    r = verify(
        cited_clauses=["E001"],
        evidence=ev,
        answer_text="제9조의 결론이며 제3조를 별도로 적용합니다.",
        quotes={"E001": quote},
    )

    assert not r.ok
    assert r.reason_code == "undeclared_citation"
    assert r.undeclared_mentions == ["제3조"]


def test_같은_quote가_답변에_두번_나오면_payload_한개는_span_한개만_면제한다():
    quote = "제3조에서 정한 지급사유"
    ev = [EvidenceClause("보통약관/제9조", quote, "E001", "clause-9")]

    r = verify(
        cited_clauses=["E001"],
        evidence=ev,
        answer_text=f"제9조의 인용은 {quote}입니다. 별도 주장도 {quote}입니다.",
        quotes={"E001": quote},
    )

    assert not r.ok
    assert r.reason_code == "undeclared_citation"
    assert r.undeclared_mentions == ["제3조"]


def test_중첩_quote_span은_입력순서대로_결정론적으로_처리한다():
    long_quote = "회사는 제3조에서 정한 지급사유가 발생한 경우 보상합니다."
    ev = [EvidenceClause("보통약관/제9조", long_quote, "E001", "clause-9")]

    r = verify(
        cited_clauses=["E001"],
        evidence=ev,
        answer_text=f"제9조의 원문은 '{long_quote}'입니다.",
        quotes={"E001": [long_quote, "제3조에서 정한 지급사유"]},
    )

    assert r.ok
    assert r.undeclared_mentions == []


def test_동일_경로의_서로_다른_후보는_quote_없으면_ambiguous다():
    ev = [
        EvidenceClause("보통약관/제9조", "본문 A", clause_id="clause-a"),
        EvidenceClause("보통약관/제9조", "본문 B", clause_id="clause-b"),
    ]

    r = verify(cited_clauses=["보통약관/제9조"], evidence=ev)
    reversed_result = verify(
        cited_clauses=["보통약관/제9조"], evidence=list(reversed(ev))
    )

    assert not r.ok
    assert r.reason_code == "ambiguous_citation"
    assert r.checks[0].resolution is Resolution.AMBIGUOUS
    assert r.checks[0].matched is None
    assert r.checks[0].candidates == reversed_result.checks[0].candidates
    assert len(r.checks[0].candidates) == 2


def test_동일_경로_충돌도_quote가_한_후보만_식별하면_exact다():
    ev = [
        EvidenceClause("보통약관/제9조", "본문 A의 고유 문장", clause_id="clause-a"),
        EvidenceClause("보통약관/제9조", "본문 B의 고유 문장", clause_id="clause-b"),
    ]

    r = verify(
        cited_clauses=["보통약관/제9조"],
        evidence=ev,
        quotes={"보통약관/제9조": "본문 B의 고유 문장"},
    )

    assert r.ok
    assert r.checks[0].resolution is Resolution.EXACT
    assert r.checks[0].matched.clause_id == "clause-b"


def test_동일_경로_충돌에서_quote가_어느_후보에도_없으면_실패한다():
    ev = [
        EvidenceClause("보통약관/제9조", "본문 A", clause_id="clause-a"),
        EvidenceClause("보통약관/제9조", "본문 B", clause_id="clause-b"),
    ]

    r = verify(
        cited_clauses=["보통약관/제9조"],
        evidence=ev,
        quotes={"보통약관/제9조": "존재하지 않는 quote"},
    )

    assert not r.ok
    assert r.reason_code == "ambiguous_citation"
    assert r.checks[0].quote_mismatch == "존재하지 않는 quote"


def test_동일_경로_충돌에서_quote가_복수_후보에_있으면_ambiguous다():
    ev = [
        EvidenceClause("보통약관/제9조", "공통 문장과 본문 A", clause_id="clause-a"),
        EvidenceClause("보통약관/제9조", "공통 문장과 본문 B", clause_id="clause-b"),
    ]

    r = verify(
        cited_clauses=["보통약관/제9조"],
        evidence=ev,
        quotes={"보통약관/제9조": "공통 문장"},
    )

    assert not r.ok
    assert r.reason_code == "ambiguous_citation"
    assert r.checks[0].matched is None


def test_같은_clause_id의_여러_chunk는_논리_후보_하나로_접는다():
    ev = [
        EvidenceClause("보통약관/제9조", "첫 chunk", clause_id="clause-9"),
        EvidenceClause("보통약관/제9조", "둘째 chunk", clause_id="clause-9"),
    ]

    r = verify(cited_clauses=["보통약관/제9조"], evidence=ev)

    assert r.ok
    assert r.checks[0].resolution is Resolution.EXACT
