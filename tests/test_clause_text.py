"""약관 검색 정규화 — **공백 하나로 결론이 뒤집힌 일**을 시험으로 못박는다.

★실제로 두 번 틀렸다(2026-08-05)

    ① 「우선공제」로 찾아 0건 → 「노후실손이 4세대 개편에 편입됐다」고 결론.
       약관은 「우선 공백 공제」로 쓴다. **구조는 안 바뀌었다.**
    ② 「실손의료비」 표지가 「실손 의료비」에 안 걸려 5건이 unknown 으로 샜다.

    둘 다 **「검색되지 않음」을 「없음」으로 읽은 것**이 뿌리다.
"""

from __future__ import annotations

import pytest

from app.core.domain.clause_text import (
    find_count,
    normalize,
    presence,
    squeezed,
    term_pattern,
)

#: ★실제 약관에서 본 변형들. 조판·OCR 이 만드는 모양이다.
_VARIANTS = [
    "우선공제", "우선 공제", "우선  공제", "우선\n공제", "우선\t공제", "우선　공제",
]


@pytest.mark.parametrize("text", _VARIANTS)
def test_낱말_안_공백이_어디에_오든_찾는다(text: str) -> None:
    """★이 시험이 없어서 「우선공제가 사라졌다」는 틀린 결론을 냈다."""
    assert term_pattern("우선공제").search(normalize(text)), f"못 찾음: {text!r}"


@pytest.mark.parametrize("term", ["실손의료비", "실손의료보험", "비중증", "표준화이전"])
def test_상품라인_표지도_공백에_흔들리지_않는다(term: str) -> None:
    spaced = " ".join(term)          # 글자마다 공백
    broken = term[:2] + "\n" + term[2:]  # 줄바꿈이 가운데를 자름
    for t in (term, spaced, broken):
        assert term_pattern(term).search(normalize(t)), f"{term!r} 를 {t!r} 에서 못 찾음"


def test_횟수를_셀_때도_같다():
    a = "우선공제 후 급여에서 우선 공제한다"
    assert find_count(a, "우선공제") == 2


def test_squeezed_는_보조일_뿐이다():
    """★공백을 다 지우면 **다른 낱말의 꼬리**에 걸린다. 그래서 보조로만 쓴다."""
    assert squeezed("실손 의료비 보험") == "실손의료비보험"
    #: 이 성질 때문에 단독으로 쓰면 안 된다 — `의료비` 가 `실손의료비` 안에 있다.
    assert "의료비" in squeezed("무배당 실손 의료비 보험")


def test_짧은_본문은_없음이_아니라_모른다():
    """★★**「0건」을 결론으로 쓰지 않는다.**

    본문이 비었거나 짧으면 그건 **추출 실패**이지 「약관에 그 말이 없다」가 아니다.
    둘을 섞으면 조판 사고가 도메인 결론으로 둔갑한다.
    """
    assert presence("", "우선공제") == "unknown"
    assert presence("짧은 조각", "우선공제") == "unknown"

    long_without = "가" * 300
    assert presence(long_without, "우선공제") == "absent"

    long_with = "가" * 300 + " 비급여에서 우선 공제한 후 급여에서 공제"
    assert presence(long_with, "우선공제") == "present"


def test_빈_낱말은_거부한다():
    """★빈 패턴은 아무것에나 걸린다 — 조용히 True 를 주면 안 된다."""
    with pytest.raises(ValueError):
        term_pattern("   ")
