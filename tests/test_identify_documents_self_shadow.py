"""_rivals()의 셀프섀도 가드 — 채널 낱말이 이름 가운데 끼는 경우.

★삼성생명 실사례(sha `b3b339438e49`, 2026-08-26)에서 발견된 매처 결함의 회귀
테스트. "인터넷"이 "삼성생명"과 "실손의료비보장보험1.0" 사이에 삽입되면
`k in me`도 `me in k`도 안 걸려 자기 자신을 경쟁 상품으로 오탐했다.

★`flat_doc` 은 실제 호출부(1026번째 줄 부근)처럼 `_norm()` 을 거친 정규화 문자열이어야
한다 — 원문 그대로 넘기면 공백·문장부호 차이로 `k not in flat_doc` 조기 필터에 걸려
버려서, 이 파일이 고치려는 로직까지 가 보지도 못한 채 "우연히" 통과해버린다.
"""

from scripts.confirm.identify_documents import _norm, _rivals


def test_채널낱말이_가운데_끼면_자기자신은_증거가_아니다():
    row = {"sha256": "f21dab22d47bb5d0", "product_name": "삼성생명인터넷실손의료비보장보험1.0(기본형,갱신형,무배당)"}
    sibling = {"sha256": "다른sha", "product_name": "삼성생명실손의료비보장보험1.0(기본형,갱신형,무배당)"}
    flat_doc = _norm(
        "삼성생명인터넷실손의료비보장보험1.0(기본형,갱신형,무배당) 관련 문서 본문... "
        "삼성생명실손의료비보장보험1.0(기본형,갱신형,무배당)이라고도 부릅니다."
    )

    rivals, shadowed = _rivals(row, flat_doc, [sibling])

    assert rivals == []
    assert shadowed == []


def test_채널낱말이_반대쪽에_있어도_자기자신이다():
    """방향을 바꿔도(내 이름은 채널 낱말 없이, 상대 이름에 채널 낱말) 같은 결론."""
    row = {"sha256": "aaa", "product_name": "삼성생명실손의료비보장보험1.0(기본형,갱신형,무배당)"}
    sibling = {"sha256": "bbb", "product_name": "삼성생명인터넷실손의료비보장보험1.0(기본형,갱신형,무배당)"}
    flat_doc = _norm(
        "삼성생명실손의료비보장보험1.0(기본형,갱신형,무배당) 관련 문서 본문... "
        "삼성생명인터넷실손의료비보장보험1.0(기본형,갱신형,무배당)이라고도 부릅니다."
    )

    rivals, shadowed = _rivals(row, flat_doc, [sibling])

    assert rivals == []
    assert shadowed == []


def test_진짜_다른_상품은_여전히_경쟁으로_잡는다():
    """★오탐 방지 확인 — 채널 낱말과 무관한 진짜 다른 상품명은 그대로 rivals에 남아야 한다."""
    row = {"sha256": "ccc", "product_name": "삼성생명실손의료비보장보험1.0(기본형,갱신형,무배당)"}
    sibling = {"sha256": "ddd", "product_name": "삼성생명실손의료비보장보험2.0(기본형,갱신형,무배당)"}
    flat_doc = _norm(
        "삼성생명 실손의료비보장보험1.0(기본형,갱신형,무배당) 관련 문서 본문... "
        "삼성생명 실손의료비보장보험2.0(기본형,갱신형,무배당)과 비교하면..."
    )

    rivals, shadowed = _rivals(row, flat_doc, [sibling])

    assert "삼성생명실손의료비보장보험2.0(기본형,갱신형,무배당)" in rivals


def test_채널낱말_수정_전이었다면_오탐했다는_것을_직접_확인():
    """★이 회귀가 진짜로 옛 로직에서 재현되는지 직접 대조 — `k in me`·`me in k` 둘 다
    거짓이어야 이 테스트가 잡으려는 그 구멍이 맞다."""
    from scripts.confirm.identify_documents import full_name_key

    me = full_name_key("삼성생명인터넷실손의료비보장보험1.0(기본형,갱신형,무배당)")
    k = full_name_key("삼성생명실손의료비보장보험1.0(기본형,갱신형,무배당)")
    assert k not in me
    assert me not in k
