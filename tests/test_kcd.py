"""KCD 코드 파싱·범위·면책 판정."""

from app.core.domain.kcd_ranges import (
    CodeMention, CodeRef, KcdRange, judge, parse_ranges, scan_clause,
)


def test_코드를_읽는다():
    assert str(CodeRef.parse("F04")) == "F04"
    assert str(CodeRef.parse("N39.3")) == "N39.3"
    assert CodeRef.parse("F4") is None          # 두 자리여야 한다
    assert CodeRef.parse("ABC") is None


def test_문자열_비교가_아니라_숫자로_비교한다():
    #: ★"F9" < "F04" 는 문자열로 참이다. 이러면 범위 판정이 깨진다.
    r = KcdRange(CodeRef("F", 4), CodeRef("F", 99))
    assert r.contains(CodeRef("F", 9))
    assert r.contains(CodeRef("F", 32))
    assert not r.contains(CodeRef("F", 3))


def test_세자리_범위는_세분류를_포함한다():
    r = KcdRange(CodeRef("F", 4), CodeRef("F", 9))
    assert r.contains(CodeRef("F", 4, 1))       # F04.1 은 F04~F09 에 든다
    assert r.contains(CodeRef("F", 9, 9))


def test_세분류를_콕_집으면_다른_세분류는_안_든다():
    r = KcdRange(CodeRef("N", 39, 3), CodeRef("N", 39, 3))
    assert r.contains(CodeRef("N", 39, 3))
    assert not r.contains(CodeRef("N", 39, 0))


def test_분류문자가_다르면_범위_밖이다():
    r = KcdRange(CodeRef("F", 4), CodeRef("F", 99))
    assert not r.contains(CodeRef("N", 39))


def test_여러_구분자를_받는다():
    #: 실측: ∼ 1,817회 · ～ 2,042회 · ~ 2,268회
    for sep in ("~", "∼", "～"):
        rs = parse_ranges(f"정신 및 행동장애(F04{sep}F99)")
        assert any(str(r) == "F04~F99" for r in rs), sep


def test_뒤_분류문자가_생략된_표기도_받는다():
    rs = parse_ranges("(F04~09)")
    assert any(r.contains(CodeRef("F", 7)) for r in rs)


def test_역순_범위는_뒤집지_않고_버린다():
    #: 의도를 추측하면 안 된다.
    rs = parse_ranges("(F99~F04)")
    assert not any(str(r) == "F04~F99" for r in rs)


_실제_면책조항 = (
    "회사는 '한국표준질병사인분류'에 따른 다음의 의료비에 대해서는 보상하지 않습니다. "
    "① 정신 및 행동장애(F04∼F99). 다만, F04∼F09, F20∼F29 과 관련한 치료에서 "
    "발생한 요양급여에 해당하는 의료비는 보상합니다. "
    "③ 피보험자가 임신, 출산으로 입원 또는 통원한 경우(O00∼O99)"
)


def test_면책과_예외를_구분한다():
    ms = scan_clause(_실제_면책조항)
    kinds = {str(m.range): m.kind for m in ms}
    assert kinds["F04~F99"] == "exclude"
    assert kinds["O00~O99"] == "exclude"
    assert kinds["F04~F09"] == "exception"      # "다만 … 보상합니다"


def test_예외가_면책을_덮는다():
    ms = scan_clause(_실제_면책조항)
    assert judge("F05", ms)["status"] == "exception"     # F04~F09 예외에 든다
    assert judge("F50", ms)["status"] == "excluded"      # F04~F99 면책만 해당
    assert judge("O10", ms)["status"] == "excluded"


def test_언급되지_않은_코드는_면책이_아니라고만_말한다():
    #: ★"보장된다"고 말하지 않는다. 보장은 다른 조항이 정한다.
    r = judge("S72", scan_clause(_실제_면책조항))
    assert r["status"] == "not_mentioned"


def test_잘못된_코드는_거부한다():
    assert judge("우울증", [])["status"] == "invalid_code"


def test_성격을_모르는_코드는_mention_으로_둔다():
    ms = scan_clause("「의료법」 제3조에 규정한 종합병원. 참고 A00~A09")
    assert all(m.kind == "mention" for m in ms)


#: ★실제 약관에서 그대로 가져온 문장. 추출 아티팩트까지 포함한다.
_실제_DB손보_1904 = (
    "②회사는 '한국표준질병사인분류'에 따른 다음의 입원의료비에 대해서는 보상하지 않 습니다. "
    "① 정신 및 행동장애(F04～F99)        (다만, F04～F09, F20～F29, F30～F39, "
    "F40～F48, F51, F90～F98과 관련 한 치       료 에서 발생한 「국민건강보험법」에 "
    "따른 요양급여에 해당하는 의료비는 보상    36    합니다) "
    "② 여성생식기의 비염증성 장애(N96～N98) "
    "③ 피보험자가 임신, 출산으로 입원한 경우(O00～ O99) "
    "④ 선천성 뇌질환(Q00～Q04) ⑤ 비만(E66) ⑥ 요실금(N39.3, N39.4, R32)"
)


def test_낱말_사이에_낀_페이지번호를_뚫고_예외를_찾는다():
    """★`보상    36    합니다` — 페이지 푸터가 본문에 섞여 낱말을 쪼갠다.

    이걸 못 넘으면 '다만 … 보상합니다' 를 못 찾아 **예외를 면책으로 판정**한다.
    보장되는 질병을 "안 됩니다"라고 답하게 되는 것이다.
    """
    ms = scan_clause(_실제_DB손보_1904)
    assert judge("F05", ms)["status"] == "exception"
    assert judge("F32", ms)["status"] == "exception"


def test_예외에_없는_정신질환은_면책이다():
    ms = scan_clause(_실제_DB손보_1904)
    assert judge("F60", ms)["status"] == "excluded"      # F04~F99 에만 든다


def test_범위가_아닌_단일_코드도_면책으로_잡는다():
    """`⑤ 비만(E66)`, `⑥ 요실금(N39.3, ...)` — 범위 표기가 아니다."""
    ms = scan_clause(_실제_DB손보_1904)
    assert judge("E66", ms)["status"] == "excluded"
    assert judge("N39.3", ms)["status"] == "excluded"
    assert judge("R32", ms)["status"] == "excluded"


def test_세분류가_다르면_면책이_아니다():
    #: `N39.3` 은 면책이지만 `N39.0` 은 목록에 없다.
    ms = scan_clause(_실제_DB손보_1904)
    assert judge("N39.0", ms)["status"] == "not_mentioned"
