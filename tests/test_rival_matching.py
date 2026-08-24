"""경쟁 상품명 대조 — **자기 그림자를 증거로 세지 않는다.**

참고 — 이 파일이 지키는 명제

    1. 상대 이름이 **내 이름 안에** 들어 있으면 증거가 아니다(정보량 0).
    2. 위험 — 거꾸로 내 이름이 상대 이름에 삼켜지면 **자동 확정을 막는다.**
       — 빼는 것이 아니라 막는 것이다. 빼면 오히려 통과한다.
    3. 독립적으로 확인된 다른 본약관은 여전히 모호로 남는다.
    4. 특약은 상대로 세지 않는다.

핵심 — 왜 이 시험이 있나 (2026-08-11 전수 재측정)

    `_norm()` 이 괄호·점을 지우므로 상품명이 이렇게 겹친다 —

        나  : 무배당 흥국화재 다이렉트 실손의료보험(25.07)  →  …실손의료보험2507
        상대: 무배당 흥국화재 다이렉트 실손의료보험        →  …실손의료보험

    내 이름이 문서에 있으면 상대 키는 **반드시** 걸린다. 그런데 그걸
    「다른 상품도 실려 있다」는 증거로 셌다. 모호 240건 중 **161건(67.1%)**
    이 이 경우였다 — 기준의 문제가 아니라 **결함**이다.

    위험 — 반대 방향은 실측 0건이지만 남긴다. **없다는 것을 재고 나서** 하는 말이라야 한다.
"""

from __future__ import annotations

import pytest

from scripts.confirm.identify_documents import _gen_splits_month, _norm, _rivals


def _row(name: str, sha: str = "aa"):
    return {"product_name": name, "sha256": sha}


def _doc(*names: str) -> str:
    return _norm(" ".join(names))


# ── ① 자기 그림자 ────────────────────────────────────────────────────────
def test_상대_이름이_내_이름_안에_있으면_증거가_아니다():
    me = _row("무배당 흥국화재 다이렉트 실손의료보험(25.07)", "aa")
    short = _row("무배당 흥국화재 다이렉트 실손의료보험", "bb")
    #: 참고 — 문서에는 **내 이름만** 적혀 있다. 상대 키는 그 안에 들어 있어 저절로 걸린다.
    rivals, shadowed = _rivals(me, _doc(me["product_name"]), [short])
    assert rivals == [], "내 이름 안에 든 짧은 이름은 다른 상품의 증거가 될 수 없다"
    assert shadowed == []


def test_괄호_판본만_다른_짧은_이름도_같다():
    me = _row("무배당 흥국화재 실손의료보험(개인재개용_25.07)", "aa")
    short = _row("무배당 흥국화재 실손의료보험(개인재개용)", "bb")
    rivals, _ = _rivals(me, _doc(me["product_name"]), [short])
    assert rivals == []


# ── ② 위험 — 삼켜짐 — 빼는 것이 아니라 막는 것 ──────────────────────────────────
def test_내_이름이_더_긴_이름에_삼켜지면_막는다():
    me = _row("무배당 흥국화재 다이렉트 실손의료보험", "aa")
    longer = _row("무배당 흥국화재 다이렉트 실손의료보험(25.07)", "bb")
    #: 참고 — 문서에 적힌 것은 **긴 이름뿐**이다. 내 이름은 그 부분문자열로 걸렸을 뿐이다.
    rivals, shadowed = _rivals(me, _doc(longer["product_name"]), [longer])
    assert shadowed == [longer["product_name"]], "삼켜진 사실을 신호로 남겨야 한다"
    #: 위험·핵심 — 이걸 `rivals` 에서 빼기만 하면 **오히려 통과한다.** 별도 신호라야 막힌다.
    assert rivals == []


# ── ③ 진짜 경쟁은 여전히 모호 ────────────────────────────────────────────
def test_독립적인_다른_본약관은_여전히_모호다():
    me = _row("무배당 프로미라이프 실손의료비보험(계약전환용)2101", "aa")
    other = _row("무배당 프로미라이프 실손의료비보험2101", "bb")
    #: 어느 쪽도 다른 쪽의 부분문자열이 아니다 — 둘 다 문서에 실제로 적혀 있다.
    rivals, shadowed = _rivals(me, _doc(me["product_name"], other["product_name"]), [other])
    assert rivals == [other["product_name"]]
    assert shadowed == []


def test_문서에_없는_상대는_세지_않는다():
    me = _row("무배당 A화재 실손의료보험2101", "aa")
    other = _row("무배당 A화재 실손의료보험1704", "bb")
    rivals, shadowed = _rivals(me, _doc(me["product_name"]), [other])
    assert rivals == [] and shadowed == []


# ── ④ 특약 ──────────────────────────────────────────────────────────────
def test_특약은_경쟁_상대로_세지_않는다():
    me = _row("무배당 A화재 실손의료보험2101", "aa")
    rider = _row("실손의료비보장 안정화 할인 특별약관", "bb")
    rivals, _ = _rivals(me, _doc(me["product_name"], rider["product_name"]), [rider])
    assert rivals == []


def test_자기_자신은_상대가_아니다():
    me = _row("무배당 A화재 실손의료보험2101", "aa")
    same = _row("무배당 A화재 실손의료보험2101", "aa")
    rivals, shadowed = _rivals(me, _doc(me["product_name"]), [same])
    assert rivals == [] and shadowed == []


# ── ⑤ 월 정밀도 날짜로 세대 모순을 선언하지 않는다 ────────────────────────
#
# 핵심 — 실손 세대 경계 중 **월 가운데** 있는 것은 5세대(`2026-05-06`) 하나뿐이다.
#   상품명 코드 `2605` 에서 뽑은 판매일은 일(日)을 모르므로 `20260501` 로 채워지는데,
#   그 채운 값이 경계 앞에 떨어져 「규칙은 4세대」가 된다. **채운 값이 결론을 만든다.**
#   실측 2026-08-11 — 이 한 가지로 NH농협 3건이 원장에서 빠졌다.
@pytest.mark.parametrize(("ym", "splits"), [
    ("20260501", True),    # 참고 — 5세대 경계 2026-05-06 이 달 가운데 있다
    ("20260401", False),
    ("20260601", False),
    ("20210701", False),   # 4세대 경계는 1일이라 달을 가르지 않는다
    ("20170401", False),
    ("20091001", False),
    #: 위험 — 말일을 `31` 로 고정하면 여기가 True 로 나온다 — 9월엔 31일이 없다.
    ("20090901", False),
])
def test_그_달_안에서_세대가_갈리는지(ym, splits):
    assert _gen_splits_month(ym) is splits


# ── ⑥ 구판 잔존 문구 후보 — 관찰용일 뿐, 확정을 바꾸지 않는다 ────────────────
#
# 핵심 — 실측 2026-08-13(현대해상) — 「Hi2108」 표지·URL·sale_start 가 모두
#   일치하는 문서의 41쪽에 「무배당 유병력자실손의료비보장보험(갱신형)(Hi1901)은
#   만기환급금 및…」 이라는 옛 판본 번호가 남은 문장이 있었다. 상품안내 문구를
#   옛 판에서 복사해 오면서 번호를 안 고친 흔적으로 보인다.
#
#   코덱스 교차검증(2026-08-13)이 이걸 자동 확정 근거로 쓰는 것에 반대했다 —
#   「판본이 높다」는 신판의 증거가 아니라 부재 증거 추론이고, 매니페스트 이름
#   자체가 오표기일 수 있으며, 상대가 잔존 문구가 아니라 구약관 전체가 부록으로
#   포함된 것일 수도 있다. 그래서 **관찰용 표시**로만 남긴다 — `out["ok"]` 를
#   바꾸지 않는다.
from scripts.confirm.identify_documents import _legacy_same_family_rivals  # noqa: E402


def test_같은_상품군_구판은_관찰용으로_잡힌다():
    #: 실측 사례 재현.
    got = _legacy_same_family_rivals(
        "무배당유병력자실손의료비보장보험(갱신형)(Hi2108)",
        ["무배당 유병력자실손의료비보장보험(갱신형)(Hi1901)"])
    assert got == ["무배당 유병력자실손의료비보장보험(갱신형)(Hi1901)"]


def test_판본_없는_범용_이름은_비교하지_않는다():
    #: 위험 — 코덱스 지적. 「삼성급여실손의료비보장보험[계약전환용](갱신형,무배당)」
    #:   처럼 판본이 없는 이름은 특정 시점을 가리키는지조차 알 수 없다.
    got = _legacy_same_family_rivals(
        "삼성급여실손의료비보장보험(2201)[계약전환용](갱신형,무배당)",
        ["삼성급여실손의료비보장보험[계약전환용](갱신형,무배당)"])
    assert got == []


def test_다른_상품군은_판본이_높아도_안_잡힌다():
    #: 위험 — 같은 상품군(family_key)일 때만 의미가 있다. 다른 상품이면
    #:   판본 비교 자체가 무의미하다.
    got = _legacy_same_family_rivals(
        "무배당 A실손의료보험2507",
        ["무배당 B실손의료보험2404"])
    assert got == []


def test_상대가_더_신판이면_안_잡힌다():
    #: 내 판본이 더 낮으면(즉 내가 구판일 수도 있으면) 관찰 대상이 아니다.
    got = _legacy_same_family_rivals(
        "실손의료비보장 안정화 할인 특별약관2404",
        ["실손의료비보장 안정화 할인 특별약관2507"])
    assert got == []


def test_판본_없는_이름은_아무_상대도_안_잡는다():
    #: 참고 — 「모른다」를 「맞다」로 바꾸지 않는다는 원칙 — 내 판본을 모르면
    #:   상대가 뭐든 비교할 근거가 없다.
    got = _legacy_same_family_rivals(
        "실손의료비보장 안정화 할인 특별약관",
        ["실손의료비보장 안정화 할인 특별약관2404"])
    assert got == []


# ── ⑦ "개인실손의료보험" 정의 문구 — 범주 용어는 경쟁 상품이 아니다 ────────────
#
# 핵심 — 실측 2026-08-14(현대해상) — 「그 같이 다른 상품이 같이 실린 경우
#   어떻게 판별하는지에 대한 안내도 보험사측에서 제공할 거 아니야」라는 지시로
#   현대해상 상품요약서(약관 PDF가 아니라 별도 첨부 — `prodSmryApnflId`)를
#   직접 크롤링해 확인. Hi2607 문서의 실제 원문 —
#
#     "* 개인실손의료보험 : 노후·유병력자·여행 실손의료보험 및 단체실손의료보험을
#      제외한 실손의료보험"
#
#   이건 특정 상품이 아니라 **범주를 가리키는 용어 정의**다. 모호 93건 중
#   "개인실손의료보험"이 상대로 잡힌 42건을 전수 재측정 — 42건 전부(100%)
#   "이하…개인실손…이라 함" 정의절과 함께 나왔다. 예외 없는 신호다.
def test_정의_문구가_있으면_개인실손의료보험은_경쟁상대가_아니다():
    #: 실측 사례 재현 — Hi2607.
    me = _row("(무)현대해상실손의료비보장보험(계약전환용)(갱신형)(Hi2607)1종(계약전환용)", "aa")
    term = _row("개인실손의료보험", "bb")
    body = (me["product_name"] + " "
            "이하 '개인실손의료보험'이라 함 노후·유병력자·여행 실손의료보험 및 "
            "단체실손의료보험을 제외한 실손의료보험 " + term["product_name"])
    rivals, shadowed = _rivals(me, _doc(body), [term], full_flat=_doc(body))
    assert rivals == [], "정의 문구가 있으면 범주 용어지 경쟁 상품이 아니다"
    assert shadowed == []


def test_정의_문구가_없으면_개인실손의료보험은_여전히_모호다():
    #: 위험 — 정의절 없이 그냥 이름만 실려 있으면 진짜 공존일 수 있다 — 여전히 막는다.
    me = _row("(무)현대해상실손의료비보장보험(계약전환용)(갱신형)(Hi2607)1종(계약전환용)", "aa")
    term = _row("개인실손의료보험", "bb")
    rivals, _ = _rivals(me, _doc(me["product_name"], term["product_name"]),
                         [term], full_flat=_doc(me["product_name"], term["product_name"]))
    assert rivals == [term["product_name"]]


def test_정의_문구는_본문에_있고_flat_doc은_표지로_좁혀져도_통한다():
    #: 핵심 — 표지 한 줄로 이름을 맞춘 경우 `flat_doc` 은 표지 줄들뿐이라 본문의
    #:   정의절이 안 보인다. `full_flat` 으로 문서 전체를 따로 받아야 한다.
    me = _row("(무)현대해상실손의료비보장보험(계약전환용)(갱신형)(Hi2607)1종(계약전환용)", "aa")
    term = _row("개인실손의료보험", "bb")
    cover_only = _doc(me["product_name"], term["product_name"])
    full_doc = cover_only + _doc("이하 '개인실손의료보험'이라 함")
    rivals, _ = _rivals(me, cover_only, [term], full_flat=full_doc)
    assert rivals == [], "정의절은 본문에 있다 — full_flat 이 문서 전체를 봐야 잡힌다"


def test_다른_이름은_정의_문구가_있어도_배제되지_않는다():
    #: 위험 — 배제는 "개인실손의료보험" 정확히 그 이름에만 적용된다.
    #:   다른 상품명까지 덩달아 봐주면 진짜 경쟁을 놓친다.
    me = _row("무배당 A화재 실손의료보험2101", "aa")
    other = _row("무배당 A화재 실손의료보험1704", "bb")
    body = _doc(me["product_name"], other["product_name"], "이하 '개인실손의료보험'이라 함")
    rivals, _ = _rivals(me, body, [other], full_flat=body)
    assert rivals == [other["product_name"]]
