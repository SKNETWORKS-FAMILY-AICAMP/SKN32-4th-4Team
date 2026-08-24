"""깨진 글꼴 되살리기 — **되살아난 것이 보일 때만 되살렸다고 한다.**

참고 — 이 파일이 지키는 명제

    1. 고정 거리로 밀린 글자를 되돌린다(cmap 없는 CID 글꼴).
    2. 위험 — **한글 비율로 채점하지 않는다.** 낱말로 채점한다.
    3. 한 문서에 거리가 여럿일 수 있다 — 줄마다 고른다.
    4. 되살아나지 않으면 **원문을 그대로** 돌려준다. 없던 글자를 만들지 않는다.
    5. 짧은 글로는 「깨졌다」고 판단하지 않는다.

핵심 — 왜 이 시험이 있나 (2026-08-11)

    문서 확정이 막힌 건들을 보다가 표지가 이렇게 나온 것을 찾았다 —

        ⱨⵤ␭ٻ⊬䟸㐘㋄㢌⨀⽸⸨㣙⸨䜌ڃᵥ㐔䝉ڄ

    처음엔 「추출 실패라 자동으로 못 푼다」고 적었다. 게으른 결론이었다.
    실제로는 글리프 번호가 유니코드에서 **일정한 거리**만큼 밀린 것이라
    계산으로 되돌릴 수 있었다 —

        무 U+BB34 ← ⱨ U+2C68   차이 0x8ECC

    위험 — 그리고 첫 구현은 **틀린 거리를 골랐다.** 「한글이 가장 많이 나오는 거리」로
      채점했더니 `0xA585` 가 한글 68% 로 1등이었는데 결과는 말이 아니었다.
      **대리 지표가 진짜 기준을 밀어낸 것**이다.
"""

from __future__ import annotations

from app.core.domain.glyph_recovery import find_offsets, looks_broken, recover

#: 실측 문서(현대해상 노후실손 Hi1701)의 표지 조각. 거리는 0x8ECC 다.
_BROKEN = "ⱨⵤ␭ٻ⊬䟸㐘㋄㢌⨀⽸⸨㣙⸨䜌ڃᵥ㐔䝉ڄٻ⸨䋩㚱Ḵ"
_REAL = "무배당 노후실손의료비보장보험(갱신형) 보통약관"


def _many(s: str, n: int = 12) -> str:
    """닻 낱말이 충분히 나오도록 되풀이한 본문."""
    return "\n".join([s] * n)


def test_고정_거리로_밀린_글자를_되돌린다():
    rec = recover(_many(_BROKEN))
    assert rec.recovered
    assert rec.offset == 0x8ECC
    #: 한글 부분이 되살아나야 한다(괄호·라틴은 다른 글꼴이라 그대로 둔다).
    assert "무배당" in rec.text and "노후실손의료비보장보험" in rec.text
    assert "보통약관" in rec.text


def test_한글_비율이_아니라_낱말로_채점한다():
    """위험 — 한글 자리로 옮기기만 하면 비율은 오른다 — 뜻이 되지 않아도."""
    offs = find_offsets(_many(_BROKEN))
    assert offs, "낱말이 나오는 거리를 찾아야 한다"
    #: 참고 — 첫 구현이 골랐던 0xA585 는 한글은 많지만 낱말이 없다 — 1등이면 안 된다.
    assert offs[0] == 0x8ECC


def test_되살아나지_않으면_원문을_그대로_돌려준다():
    #: 평범한 한국어는 건드릴 것이 없다.
    plain = _many("이 약관은 보험계약의 내용을 정한 것입니다")
    rec = recover(plain)
    assert rec.text == plain
    assert not rec.recovered


def test_뜻이_없는_글은_되살리지_않는다():
    #: 참고 — 닻 낱말이 나오지 않으면 「되살렸다」고 하지 않는다. 없던 글자를 만들지 않는다.
    rec = recover(_many("㵜㐘㋄㢌⨀⽸", 30))
    assert not rec.recovered
    assert rec.offset == 0


def test_짧은_글로는_깨졌다고_판단하지_않는다():
    #: 「- 1 -」 같은 쪽번호만 있는 쪽을 깨진 것으로 세면 안 된다.
    assert not looks_broken("- 1 -")
    assert not looks_broken(_BROKEN)  # 200자 미만


def test_긴_깨진_글은_깨진_것으로_본다():
    assert looks_broken(_many(_BROKEN, 30))


def test_한_문서에_거리가_여럿이면_줄마다_고른다():
    """위험 — 실측 — 삼성화재 약관 한 편에 글꼴이 여럿이라 거리도 여럿이었다.

    참고 — 두 번째 글꼴을 **글리프 자리(저코드)** 로 만든다. 처음엔 한글에서 0x1000 만
      내려 만들었는데, 그건 여전히 한글 근처라 후보 생성에서 아예 빠진다 —
      **깨진 PDF 의 실제 모습이 아니었다.** 시험 자료가 틀렸던 것이다.
    """
    other = "".join(chr(ord(c) - 0x9000) if 0xAC00 <= ord(c) < 0xD7A4 else c
                    for c in "이 특별약관의 보험금 지급 사유는 다음과 같습니다")
    mixed = _many(_BROKEN) + "\n" + _many(other)
    rec = recover(mixed)
    assert rec.recovered
    #: 두 글꼴 모두 되살아나야 한다 — 하나만 고르면 나머지는 깨진 채 남는다.
    assert "노후실손의료비보장보험" in rec.text
    assert "특별약관" in rec.text and "지급" in rec.text
