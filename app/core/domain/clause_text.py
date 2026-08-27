"""약관 본문을 **찾을 때** 쓰는 정규화 — 공백 하나로 결론이 뒤집히지 않게.

핵심 — 왜 이 모듈이 있나 — 실제로 두 번 틀렸다 (2026-08-05)

    ① 「우선공제가 2021-07 이후 사라진다 → 노후실손이 4세대 개편에 편입」
       **틀렸다.** 약관이 「우선 **공백** 공제」로 쓴다. `우선공제` 로 찾으니
       0건이 나왔고, 그걸 「구조가 바뀌었다」고 읽었다.
       `우선\\s*공제` 로 고치니 네 시기 모두 나온다 — **구조는 안 바뀌었다.**

    ② 상품라인 분류에서 「무배당수호천사온라인 **실손 의료비**보장보험」이
       표지 `실손의료비` 에 안 걸려 5건이 `unknown` 으로 샜다.

    두 번 다 **「검색되지 않음」을 「없음」으로 읽은 것**이 사고의 뿌리다.
    약관 PDF 는 표·줄바꿈·조판 때문에 낱말 가운데가 갈린다.

참고 — 그래서 규칙을 코드로 만든다

    · 찾기 전에 **NFKC 정규화**하고 연속 공백을 하나로 줄인다
    · 낱말 안의 공백을 **정규식이 허용**하게 만든다(`term_pattern`)
    · 공백을 통째로 지운 보조 문자열도 함께 둔다(`squeezed`)
    · 참고 — **「0건」을 결론으로 쓰지 않는다** — 추출 실패와 부재를 가른다

참고 — 이건 **검색용**이지 인용용이 아니다

    화면에 보여 줄 인용문은 **가공하지 않은 원문**이어야 한다.
    여기서 만든 문자열은 「있나 없나」를 판단할 때만 쓴다.
"""

from __future__ import annotations

import re
import unicodedata

#: 조판이 만든 공백류 — 줄바꿈·탭·전각공백·비분리공백.
_WS = re.compile(r"[\s  -​　]+")


def normalize(text: str) -> str:
    """찾기용 정규화. **연속 공백을 하나로** 줄이고 NFKC 로 맞춘다.

    참고 — 원문을 바꾸지 않는다 — 이 결과는 검색 판단에만 쓴다.
    """
    return _WS.sub(" ", unicodedata.normalize("NFKC", text or "")).strip()


def squeezed(text: str) -> str:
    """공백을 **통째로** 지운 보조 문자열.

    참고 — 줄바꿈이 낱말 가운데를 자른 경우까지 잡는다. 다만 이것만 쓰면
      다른 낱말의 꼬리에 걸리므로(실측: `실손의료비1501` 안의 `의료비1501`)
      `term_pattern` 을 먼저 쓰고 이쪽은 보조로 둔다.
    """
    return _WS.sub("", unicodedata.normalize("NFKC", text or ""))


def term_pattern(term: str, *, flags: int = 0) -> re.Pattern[str]:
    """낱말 안 어디에나 공백이 끼어도 찾는 정규식.

        term_pattern("우선공제")   →  우선\\s*공제
        term_pattern("실손의료비") →  실손\\s*의료\\s*비

    참고 — 글자 사이마다 `\\s*` 를 넣는다. 약관은 어디서든 줄이 갈릴 수 있어
      「어느 자리에 공백이 오는가」를 미리 알 수 없다.
    """
    chars = [re.escape(c) for c in unicodedata.normalize("NFKC", term or "") if not c.isspace()]
    if not chars:
        raise ValueError("빈 낱말로는 패턴을 만들 수 없습니다")
    return re.compile(r"\s*".join(chars), flags)


def find_count(text: str, term: str) -> int:
    """이 낱말이 몇 번 나오나. **공백에 흔들리지 않는다.**"""
    return len(term_pattern(term).findall(normalize(text)))


def presence(text: str, term: str, *, min_hits: int = 1) -> str:
    """있나 없나 — 그런데 **「모른다」를 따로 낸다.**

    반환값: `"present"` · `"absent"` · `"unknown"`

    핵심 — **「0건」을 곧바로 「없다」로 읽지 않는다.**

        본문이 비었거나 지나치게 짧으면 그건 **추출이 실패한 것**이지
        「약관에 그 말이 없다」가 아니다. 둘을 섞으면 조판 사고가
        도메인 결론으로 둔갑한다 — 실제로 그렇게 두 번 틀렸다.
    """
    norm = normalize(text)
    if len(norm) < 200:
        #: 200자는 표지 한 장도 안 된다. 이 정도면 추출이 안 된 것으로 본다.
        return "unknown"
    return "present" if len(term_pattern(term).findall(norm)) >= min_hits else "absent"


__all__ = ["find_count", "normalize", "presence", "squeezed", "term_pattern"]
