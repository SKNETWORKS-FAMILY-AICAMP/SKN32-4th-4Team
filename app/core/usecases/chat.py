"""용어 챗봇 — **약관에 적힌 것만 말한다.**

★이 챗봇은 **생성하지 않는다.**

    LLM 이 문장을 지어내지 않고, 약관 원문 인용과 **고정 문구**만 조립한다.
    그래서 "그럴듯하게 틀린 답"이 구조적으로 나올 수 없다.
    나중에 LLM 을 붙이더라도 **인용 검증을 통과한 것만** 실어야 한다.

★가장 위험한 것 — "그래서 저 보장되나요?"

    챗봇은 대화라서 이 질문으로 자연스럽게 흘러간다. 거기서 답하면
    약관버전 확정·인용검증·4단 판정을 **전부 건너뛴다**
    (`docs/handoff/11_AI_구조_지도.md` §2).

    그래서 이 유스케이스의 응답에는 **`verdict` 가 없다.**
    보장 질문을 받으면 답하지 않고 **판정 경로로 넘긴다**(`intent="precheck"`).
    화면은 그 신호를 받아 판정 양식을 띄운다.

★못 찾으면 못 찾았다고 한다. 상식으로 메우지 않는다.

    실손보험 용어는 일상어와 뜻이 다르다 — "상해"·"통원"·"입원" 모두
    약관에 별도 정의가 있다. 사전적 의미로 답하면 사람이 손해를 본다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.errors import ValidationErr
from app.core.ports.glossary import GlossarySourcePort
from app.core.usecases import glossary

#: 무엇을 하는 대화인가. **닫힌 목록**이다 — 여기 없는 의도는 만들지 않는다.
INTENT_TERM = "term"  # 용어 뜻을 묻는다
INTENT_PRECHECK = "precheck"  # 보장 여부를 묻는다 → ★답하지 않고 넘긴다
INTENT_HELP = "help"  # 무엇을 할 수 있는지 묻는다
INTENT_UNKNOWN = "unknown"

#: ★보장 여부를 묻는 신호. 하나라도 걸리면 **용어 설명으로 처리하지 않는다.**
#:   경계에서는 **판정 쪽으로 넘긴다** — 용어 설명인 척 보장을 답하는 것이
#:   그 반대보다 훨씬 위험하기 때문이다.
_COVERAGE = re.compile(
    r"보장(되|돼|받|여부|해|가능|안)|보상(되|돼|받|해|가능|안)|"
    r"청구\s*(가능|되|돼|할)|받을\s*수\s*있|지급(되|돼|받|가능)|"
    r"면책(인가|인지|되)|되나요|될까요|얼마\s*(나|까지|받)"
)
#: ★`해줘` 를 넣었더니 **"비급여 설명해줘"가 보장 질문으로 잡혔다.**
#:   넓게 잡는 쪽이 안전하지만, 용어 설명이 아예 안 되면 챗봇이 쓸모가 없다.
#:   `얼마` 도 단독으로는 뺐다 — "얼마 받을 수 있나요" 는 `받을 수 있` 로 이미 잡힌다.
#: 무엇을 할 수 있는지 묻는 말
_HELP = re.compile(r"^\s*(도움말|help|뭐\s*할|무엇을\s*할|사용법|어떻게\s*써)")

#: 질문 끝에 붙는 말. 떼어 낸다.
#:
#: ★처음엔 "무슨 뜻"·"뜻이 뭐" 만 넣었더니 `"통원 뜻"` 에서 **"뜻"이 안 떨어졌다.**
#:   그대로 검색하면 약관에 "통원 뜻" 이라는 말이 없어 "찾지 못했습니다" 가 나온다.
#:   찾을 수 있는 것을 못 찾았다고 말하는 것도 거짓말이다.
_TAIL = re.compile(
    r"\s*(?:이|가|은|는|을|를|의|란|이란|라는|이라는)?\s*"
    r"(?:무슨\s*뜻|뜻이\s*뭐\w*|뜻|의미|정의|개념|"
    r"뭐야|뭐임|뭔가요|뭐예요|뭐에요|무엇인가요|무엇|"
    r"설명(?:해|좀|해줘|해\s*주세요)?|알려\s*줘|알려\s*주세요|찾아\s*줘)?\s*$"
)
#: 조사만 남은 경우도 한 번 더 떼어 낸다("상해가" → "상해").
_TRAILING_PARTICLE = re.compile(r"(?<=.{2})(?:이|가|은|는|을|를|의|란|이란)$")
#: 질문에서 떼어 낼 앞말
_LEAD = re.compile(r"^\s*(약관에서\s*|약관의\s*|보험에서\s*)")

MIN_TERM = 2
MAX_TERM = 30


@dataclass(frozen=True)
class ChatTurn:
    """챗봇 한 마디.

    ★`verdict` 필드가 **없다.** 있으면 언젠가 채우게 된다.
    """

    intent: str
    #: 화면에 보일 말. **고정 문구**다 — 모델이 지어낸 문장이 아니다.
    message: str
    #: 용어 설명일 때만 채워진다.
    term: str = ""
    explanation: glossary.TermExplanation | None = None
    #: 화면이 다음에 무엇을 해야 하는가. `precheck_form` | `none`
    next_action: str = "none"
    warnings: tuple[str, ...] = field(default_factory=tuple)


def classify(text: str) -> str:
    """무엇을 묻는 대화인가.

    ★규칙으로 가른다. 모델에 맡기지 않는다 —
      의도 분류가 틀리면 보장 질문이 용어 설명으로 새고, 그게 최악이다.
    """
    t = (text or "").strip()
    if not t:
        return INTENT_UNKNOWN
    if _HELP.search(t):
        return INTENT_HELP
    #: ★보장 신호를 **먼저** 본다. "도수치료 보장되나요?" 는 용어 질문이 아니다.
    if _COVERAGE.search(t):
        return INTENT_PRECHECK
    return INTENT_TERM


def extract_term(text: str) -> str:
    """질문에서 용어만 남긴다.

    ★형태소 분석기를 쓰지 않는다. 붙은 말을 떼는 정도면 충분하고,
      분석기가 틀리면 왜 틀렸는지 설명하기 어렵다.
    """
    t = _LEAD.sub("", (text or "").strip())
    t = t.strip("\"'“”‘’ ?？.。,，")
    t = _TAIL.sub("", t).strip()
    t = _TRAILING_PARTICLE.sub("", t).strip()
    return t.strip("\"'“”‘’ ?？.。,，")


#: ★고정 문구. 모델이 만들지 않는다.
_HELP_TEXT = (
    "약관에 적힌 **용어의 뜻**을 원문 그대로 찾아 드립니다.\n"
    "예: “도수치료가 뭐야”, “통원 뜻”, “본인부담금”\n\n"
    "보장 여부는 이 대화창에서 답하지 않습니다 — "
    "가입한 약관으로 따로 판정해야 정확합니다."
)
_PRECHECK_TEXT = (
    "보장 여부는 여기서 답하지 않습니다.\n"
    "가입하신 **보험사·가입일·질병기호**로 약관을 확정해야 근거를 댈 수 있어서, "
    "위 판정 양식으로 안내해 드립니다.\n\n"
    "대신 이 대화창에서는 **약관 용어의 뜻**을 원문으로 확인하실 수 있습니다."
)


def reply(
    text: str, *, source: GlossarySourcePort, insurer: str | None = None
) -> ChatTurn:
    """한 마디에 답한다."""
    intent = classify(text)

    if intent == INTENT_HELP:
        return ChatTurn(intent=intent, message=_HELP_TEXT)

    if intent == INTENT_PRECHECK:
        #: ★답하지 않는다. 넘긴다.
        return ChatTurn(
            intent=intent,
            message=_PRECHECK_TEXT,
            next_action="precheck_form",
            warnings=(glossary.NOT_A_JUDGMENT,),
        )

    if intent == INTENT_UNKNOWN:
        return ChatTurn(intent=intent, message=_HELP_TEXT)

    term = extract_term(text)
    if not (MIN_TERM <= len(term) <= MAX_TERM):
        #: ★못 알아들었으면 **못 알아들었다고** 한다. 아무 용어나 찍지 않는다.
        return ChatTurn(
            intent=INTENT_UNKNOWN,
            message=(
                "어떤 용어를 찾으시는지 알아듣지 못했습니다. "
                "낱말만 적어 주세요 — 예: “도수치료”, “통원”."
            ),
        )

    try:
        ex = glossary.explain(term, source=source, insurer=insurer)
    except ValidationErr as e:
        return ChatTurn(intent=INTENT_UNKNOWN, message=str(e))

    return ChatTurn(
        intent=INTENT_TERM,
        term=term,
        explanation=ex,
        message=ex.message,
        warnings=ex.warnings,
    )


__all__ = [
    "ChatTurn",
    "INTENT_HELP",
    "INTENT_PRECHECK",
    "INTENT_TERM",
    "INTENT_UNKNOWN",
    "classify",
    "extract_term",
    "reply",
]
