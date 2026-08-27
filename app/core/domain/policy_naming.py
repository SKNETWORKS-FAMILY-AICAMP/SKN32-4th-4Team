"""약관 상품명 판별 규칙.

이 모듈은 **프레임워크도 바깥 계층도 모른다**(클린아키텍처 2단계 안쪽).
정규식만 쓴다.

★왜 따로 뺐나

    "이 상품명이 특약인가"는 **도메인 지식**이지 파일 읽기가 아니다.
    어댑터에 두면 다른 어댑터(DB 적재본)에서 같은 규칙을 또 쓰게 되고,
    그러면 두 곳이 갈라진다.
"""

from __future__ import annotations

import re

#: ★★**두 질문을 한 함수에 섞지 않는다.**
#:
#:   ⓐ 「이게 특약인가」            → `is_rider()`            엄격
#:   ⓑ 「후보에서 뒤로 미룰 것인가」  → `looks_like_rider_candidate()` 느슨
#:
#:   전에는 하나였고 `계약전환` 을 포함했다. 그런데 **「계약전환용」은 본약관**이다 —
#:   전환계약자가 가입하는 상품이지 부가 특약이 아니다.
#:
#:   실측 2026-08-05 — 판정 가능 약관 중 「계약전환용」 **117건이 전부**
#:   `is_rider=True` 였다. `resolve()` 는 상품명이 없으면
#:   `main_only = [v for v in applicable if not v.is_rider]` 로 특약을 걸러내므로,
#:   **전환계약자가 상품명을 안 적으면 자기 약관을 못 찾았다** —
#:   「그 시기에 특약으로 표시된 것밖에 없는」 조합이 17건이었고 전부
#:   `ambiguous_product_line` 으로 되물었다.

#: 이름이 **특약 표지로 끝나는** 것만 특약으로 본다.
#: ★뒤에 번호·로마자가 붙는 실제 이름을 허용한다 — `특약Ⅱ`·`특약2101` 을 놓치면 안 된다.
_RIDER_STRICT = re.compile(r"(특별\s*약관|특약)\s*[ⅠⅡⅢⅣⅤIVX0-9(){}\[\]\-_.]*\s*$")

#: 후보 정렬용 **느슨한** 표지. 세대 분류에는 쓰지 않는다.
_RIDER_LOOSE = re.compile(r"특별약관|특약|할인|중지\s*및\s*재개|제도성")


def is_rider(product_name: str) -> bool:
    """**특약 문서**인가 — 이름이 「…특별약관」·「…특약」으로 끝나는가.

    ★이 값이 `PolicyVersionRow.is_rider` 가 되고 `resolve()` 의 본약관 우선
      필터에 쓰인다. 느슨하게 잡으면 **본약관이 후보에서 빠진다.**
    """
    return bool(_RIDER_STRICT.search((product_name or "").strip()))


def looks_like_rider_candidate(product_name: str) -> bool:
    """사용자가 **특약을 지목했나** — 후보 우선순위 조정용 휴리스틱.

    ★세대 분류에 쓰지 말 것. 「할인」·「중지 및 재개」가 든 본약관도 잡는다.
    """
    return bool(_RIDER_LOOSE.search(product_name or ""))


#: ★옛 이름. 호출부를 옮기는 동안만 남긴다 — **엄격 판정으로 연결**한다.
def looks_like_rider(product_name: str) -> bool:
    """(옛 이름) `is_rider` 를 쓸 것."""
    return is_rider(product_name)


def normalize(name: str) -> str:
    """비교용 정규화. 공백·구분기호 차이를 흡수한다."""
    return re.sub(r"[\s·∙・()（）\[\]]+", "", name or "")
