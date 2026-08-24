"""별표·붙임 **참조 해소** — 조항이 가리키는 부록을 찾아 그 KCD 코드를 물려준다.

★왜 필요한가 — **면책 목록이 조항 본문에 없다**

    약관은 이렇게 쓴다.

        제N조(보상하지 않는 사항) … 다음의 특정질병(【별표1】)으로 인한 …은
        보상하지 않습니다.

    코드 목록은 조항이 아니라 **부록**(`doc["annexes"]`)에 있다.
    지금 판정 경로(`app/core/usecases/precheck.py`)는 `scan_clause(c.text)` 로
    **조항 본문만** 훑으므로 이 코드들이 판정에 도달하지 못한다.

    실측(s6 전량 1,367문서 · 2026-08-03):
      · 부록을 가진 문서 **1,188 / 1,367 (86.9%)**
      · 부록에 KCD 코드가 있는 `ok` 문서 **939**
      · 조항 본문의 **괄호형** 별표 참조 **11,379건** / 1,149문서

★이 모듈이 하지 않는 것

    · **성격을 지어내지 않는다.** 부록의 코드는 그 자체로는 면책도 보상도 아니다.
      **참조한 조항의 그 자리**가 면책 선언 아래일 때만 `exclude` 다.
      참조가 선언 밖이면 `mention` 으로 둔다 — `mention` 은 판정에 쓰면 안 되는 등급이다.
    · **조용히 건너뛰지 않는다.** 못 이은 참조는 `AnnexResolution.unresolved` 에
      사유와 함께 남는다. 세지 않으면 분모가 줄어 커버리지가 실제보다 좋아 보인다
      (CLAUDE.md §3).
    · 파일·DB 를 읽지 않는다. `clauses`·`annexes` 를 **받아서** 푼다
      (클린아키텍처 2단계 안쪽. 표준 라이브러리 `re` 와 `kcd_ranges` 만 쓴다).

★부록 본문의 자기 선언은 **성격을 만들지 못하고 거부만 한다**(veto-only)

    두 방향을 각각 실측하고 나눠서 정했다.

    ① **부록 자기 선언으로 `exclude` 를 만들면 안 된다.**
       부록에 `scan_clause` 를 그대로 돌리면 코드 언급 45,494건 중
       **9,560건이 `exclude`** 로 나오는데, 표본을 열어 보니 상당수가 가짜다.

           부록 `특정질병분류표` 안:
             "…암진단(…)은 보험금을 지급하지 않습니다. 90일이 경과된 이후에도 …
              【질병손해 보장 관련】 - 사례  A씨는 질병보험 가입 후 뇌졸중(I64) …"

           앞쪽 **민원 사례 설명**의 "지급하지 않습니다"가 선언으로 잡혀
           뒤의 예시 코드 `I64` 까지 면책으로 물든다. 부록은 표·해설·사례가
           뒤섞인 잡낭이라 "선언이 뒤를 지배한다" 규칙이 과하게 뻗는다.

    ② **그런데 부록이 대놓고 "보상합니다"라고 쓴 자리에 `exclude` 를 씌우면
       그건 우리가 틀린 것이다.** 원문 PDF 를 열어 실제로 잡았다.

           NH농협생명 `New안심케어NH실손의료비보험(갱신형,무배당)_2605` p168
           `[별첨 1] 재해분류표`
             "1. 보장 대상이 되는 재해
                다음 각 호에 해당하는 재해는 … 보험금을 **지급합니다.**
                ① 한국표준질병·사인분류 상의 (S00-Y84) …
              2. 보험금을 지급하지 않는 재해
                … 지급하지 않습니다. … 과잉노력(X50) 무중력(X52) …"

           참조한 조항(제도성 특약 제1조)이 `지급하지 않습니다` 선언 아래라
           물려주면 **`S00~Y84` 전체가 면책**이 된다. 상해분류 전체다.
           실제로는 그 범위가 **보장 대상**이고, 면책은 `X50`·`X52`… 쪽이다.

    그래서 규칙은 이렇다 —
    **성격은 참조한 조항에서만 물려받되, 부록이 그 자리에서 반대로 말하면 물려주지
    않고 `mention` 으로 내린다.** 부록 문맥은 `exclude` 를 **거부**할 수만 있고
    **만들 수는 없다.** ①의 과탐은 exclude 를 만드는 방향이라 이 규칙에 안 걸리고,
    ②의 오류는 걸린다.

    실측(s6 `ok` 1,306문서): `exclude` 를 물려받는 (참조,부록) 쌍 938건 중
    **342건**의 부록에 `다만 … 보상/보장/지급` 구간이 있었다. 삼킬 수 없는 양이다.

★법령의 별표와 **우리 약관의 별표**는 다른 것이다

    조항 본문의 별표 참조 대부분이 사실은 **법령 인용**이다.

        「국민건강보험 요양급여의 기준에 관한 규칙」 제9조 제1항([별표2]비급여대상)
        「신의료기술의 안전성·유효성 평가결과 고시」 [별표1] 신의료기술의 …

    이걸 같은 문서의 `[별표 2]` 에 이어 버리면 **엉뚱한 표의 코드가 면책이 된다.**
    같은 함정이 이 저장소에서 이미 한 번 터졌다 — 구조 감사의 `S4_annex_absorption`
    신호가 문장 안 인용 `제9조([별표1] 비급여대상)에 의한…` 을 부록 흡수로 잡아
    11,478건을 4,789건으로 정정했다(`config/accepted_extraction.json`).

    그래서 **법령 신호가 보이면 잇지 않고 `statute_annex` 로 남긴다.**
    이으면 틀릴 수 있는 것보다 못 잇는 편이 낫다(CLAUDE.md §0).

★알려진 한계 — 이 규칙이 **놓치는** 참조가 있다

    "「신용정보의 이용 및 보호에 관한 법률」 등 관계 법령(【별표1】기타관계법령을 참조)"
    여기 【별표1】 은 **우리 약관의 부록**인데 앞에 `」` 가 있어 법령 참조로 분류된다.
    지금은 못 잇고 센다. 잘못 잇는 것보다 낫다고 보고 이렇게 뒀다.

★맨몸 참조(`별표 1`, `별표2`)는 **기본적으로 잇지 않는다**

    실측 19,694건인데 표본을 훑으면 거의 전부 법령 조문 본문이다
    (`① 법 제10조에 따라 기금에서 부담하는 급여비용의 범위는 별표 1과 같다.`).
    괄호가 없으면 문장 안 법령 인용과 우리 부록 참조를 가릴 근거가 약하다.
    `include_bare=True` 로 켤 수 있게만 두고 **기본은 끈다.**
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.core.domain import kcd_ranges as kcd
from app.core.domain.kcd_ranges import CodeMention

#: 부록의 종류를 여는 낱말. PDF 추출이 낱말 사이에 공백·줄바꿈을 끼워 넣는다
#: (`별\n표2`, `별 표 2` 가 실제로 있다).
_KIND_WORD = r"별\s*표|붙\s*임|별\s*첨"

#: 괄호 — 약관마다 다르다. 실측: `[별표1]` `【별표2】` `<붙임1>` `(별표1)` `〔별표〕`
_OPEN = r"[\[\(<【〔]"
_CLOSE = r"[\]\)>】〕]"

#: 괄호형 참조. 안쪽은 닫는 괄호 전까지 최대 40자.
#: ★40자로 잡은 이유 — 제목이 딸려 오는 표기가 있다.
#:   `(별표3 “재해분류표”에서 정한 재해를 말하며, 이하 “재해”라 합니다)`
_REF_BRACKET = re.compile(
    _OPEN + r"\s*(" + _KIND_WORD + r")\s*([^\[\]\(\)<>【】〔〕]{0,40}?)\s*" + _CLOSE
)

#: 맨몸형 참조. 기본적으로 쓰지 않는다(모듈 도크스트링 참조).
_REF_BARE = re.compile(r"(" + _KIND_WORD + r")\s*([0-9①-⑮]{1,3}(?:\s*의\s*[0-9]{1,2})?)")

#: 동그라미 숫자 → 아라비아. 실측 라벨에 `【별표①】` 이 있다.
_CIRCLED = {chr(0x2460 + i): str(i + 1) for i in range(15)}

#: 라벨 앞머리의 번호. `1` `4의2` `2-1` `①`
_NUM = re.compile(r"^([0-9]{1,2}|[①-⑮])(?:\s*(?:의|[-–])\s*([0-9]{1,2}))?")

#: 제목에서 떼어낼 따옴표·구분자. `<붙임1_용어의 정의>` `[별첨: …]`
_TITLE_STRIP = re.compile(r"^[\s_:：·\-–~“”\"'‘’「」『』()]+|[\s_:：·\-–~“”\"'‘’「」『』()]+$")

#: 제목 뒤에 붙는 조사·군더더기. `재해분류표”에서 정한 재해를 말하며…` 의 꼬리를 자른다.
_TITLE_TAIL = re.compile(r"(?:에서|에|와|과|을|를|이|가|은|는|의|참조|같습니다|말하며).*$")

#: ★**법령 인용 신호.** 참조 앞쪽 창 안에 이게 있으면 잇지 않는다.
#:   `」` 하나만으로도 잡는 이유 — 법령명은 예외 없이 `「…」` 로 묶여 나온다.
_STATUTE_NEAR = re.compile(r"[」』]|시행령|시행규칙|고시|법률|법\s*제\s*\d|조\s*제\s*\d\s*항")

#: 법령 신호를 볼 창(참조 앞 글자 수). 실측 `「국민건강보험 요양급여의 기준에 관한 규칙」
#: 제9조 제1항([별표2]…` 는 여는 「 부터 참조까지 약 34자다. 여유를 두고 60.
_STATUTE_WINDOW = 60

#: ★★**계약별로 정해지는 조건부 면책.** 이걸 무조건 면책으로 물려주면 크게 틀린다.
#:
#:   실측(흥국화재 `무배당 흥국화재 다이렉트 실손의료보험(1810)` p98 · 원문 확인):
#:     "2. 【별표4】“특정질병 분류표” **중에서 회사가 지정한 질병**(이하 “특정질병”)"
#:
#:   `제도성 특별약관`(특정부위·특정질병 부담보)의 별표는 **후보 목록**이지
#:   면책 목록이 아니다. 그 계약에 이 특약이 붙었는지, 붙었다면 무엇을 지정했는지는
#:   **인수심사 결과**라 약관에 없다. 우리는 모른다.
#:
#:   그대로 물려주면 롯데 `<별표4>` 의 `C00~C97`(암 전체)이 모든 가입자에게
#:   면책으로 붙는다. "보장 안 됩니다"라고 잘못 말하는 것이다.
#:   실측 비중: `exclude` 를 물려받는 (참조,부록) 쌍 938건 중 **457건(48.7%)** 이 이 꼴이다.
#:
#:   그래서 조건부 참조는 `mention` 으로 내린다 — 모르면 모른다고 한다(CLAUDE.md §0).
#:
#: ★**낱말 안에 줄바꿈이 들어온다.** 처음엔 `회사가\s*지정한` 으로 썼는데
#:   DB손해보험 산출물이 `…①특정부위 중에서 회\n사가 지정한 부위…` 라
#:   못 잡았고, 그 문서 200건에서 `C00~C97`(암 전체)이 무조건 면책으로 붙었다.
#:   `kcd_ranges._GAP` 이 같은 함정을 이미 적어 뒀다 — 글자 사이마다 공백을 허용한다.
_CONDITIONAL = re.compile(
    r"회\s*사\s*가\s*지\s*정\s*한|부\s*담\s*보|가\s*입\s*자\s*가\s*선\s*택"
)

#: 지정 문구는 참조 **뒤에** 온다(`【별표4】“특정질병 분류표” 중에서 회사가 지정한 질병`).
_CONDITIONAL_WINDOW = 80


@dataclass(frozen=True)
class AnnexRef:
    """조항 본문에서 읽은 **부록 참조** 하나."""

    #: 원문 표기 그대로. `[별표2]`, `<붙임1_용어의 정의>`
    raw: str
    #: `"별표"` | `"붙임"` | `"별첨"` (공백 제거 후)
    kind: str
    #: `"1"` `"4의2"` — 번호가 없으면 빈 문자열(`<붙임>` 같은 표기)
    number: str
    #: 라벨에 딸린 제목. `"재해분류표"` — 없으면 빈 문자열
    title: str
    #: 조항 본문 안 위치(문자 오프셋)
    at: int
    #: 참조 자리의 성격. `"exclude"` | `"exception"` | `"mention"`
    #: ★`kcd_ranges` 가 코드에 매기는 것과 **같은 규칙**으로 매긴다.
    scope: str
    #: 앞뒤 문맥(감사용)
    context: str
    #: 법령의 별표를 가리키는 것으로 보인다
    looks_statute: bool
    #: ★계약별 지정에 걸린 조건부 참조(`… 중에서 회사가 지정한 질병`).
    #:   면책 여부를 약관만으로 정할 수 없다 → 성격을 물려주지 않는다.
    conditional: bool = False
    #: 괄호형인가. `False` 면 맨몸형(`별표 1`)
    bracketed: bool = True

    @property
    def key(self) -> str:
        """매칭 키. `"별표|1"` — 번호가 없으면 `"별표|"`."""
        return f"{self.kind}|{self.number}"


@dataclass(frozen=True)
class ResolvedRef:
    """이어진 참조 하나 — 어느 조항이 어느 부록을 가리켰나."""

    ref: AnnexRef
    clause_ordinal: int
    clause_qualified_no: str
    annex_ordinal: int
    annex_label: str
    #: 어떤 규칙으로 이었나. 감사에서 규칙별 정확도를 따로 볼 수 있어야 한다.
    #: `"kind_number"` | `"kind_number+section"`
    match_rule: str
    #: 부록에서 뽑은 코드 + **참조 자리에서 물려받은** 성격
    mentions: tuple[CodeMention, ...] = ()


@dataclass(frozen=True)
class UnresolvedRef:
    """못 이은 참조 하나. ★비워 두고 **센다**."""

    ref: AnnexRef
    clause_ordinal: int
    clause_qualified_no: str
    #: `"statute_annex"`   법령의 별표다 — 우리 부록이 아니다
    #: `"no_annexes"`      이 문서에 부록 자체가 없다
    #: `"no_such_annex"`   번호·제목이 맞는 부록이 없다
    #: `"ambiguous_label"` 같은 라벨이 여럿이라 하나를 고를 수 없다
    reason: str
    #: `ambiguous_label` 일 때 후보들
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnnexResolution:
    """문서 하나의 해소 결과."""

    resolved: tuple[ResolvedRef, ...] = ()
    unresolved: tuple[UnresolvedRef, ...] = ()

    @property
    def code_mentions(self) -> list[CodeMention]:
        """판정에 바로 넣을 수 있는 코드 언급 전부.

        ★`mention` 도 함께 나간다. 거르는 것은 판정 쪽 일이다
          (`kcd_ranges.judge` 가 `exclude`/`exception` 만 본다).
        """
        out: list[CodeMention] = []
        for r in self.resolved:
            out.extend(r.mentions)
        return out

    def counts(self) -> dict:
        """감사용 집계. 미해소를 **사유별로** 센다."""
        by_reason: dict[str, int] = {}
        for u in self.unresolved:
            by_reason[u.reason] = by_reason.get(u.reason, 0) + 1
        by_rule: dict[str, int] = {}
        for r in self.resolved:
            by_rule[r.match_rule] = by_rule.get(r.match_rule, 0) + 1
        by_kind: dict[str, int] = {}
        for m in self.code_mentions:
            by_kind[m.kind] = by_kind.get(m.kind, 0) + 1
        return {
            "refs_total": len(self.resolved) + len(self.unresolved),
            "resolved": len(self.resolved),
            "unresolved": len(self.unresolved),
            "unresolved_by_reason": by_reason,
            "resolved_by_rule": by_rule,
            "code_mentions": len(self.code_mentions),
            "code_mentions_by_kind": by_kind,
        }


# ── 성격 판정 ────────────────────────────────────────────────────────


def _declaration_scope(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, int]]]:
    """면책·보상 **선언 위치**와 예외 구간.

    ★★`kcd_ranges` 의 정규식을 **그대로 가져다 쓴다.** 복사하지 않는다.

        같은 규칙을 두 곳에 적으면 갈라진다. 이 저장소가 이미 값을 치렀다 —
        세대 상수를 설정·적재·검색 세 곳에 두었다가 판정은 `s5`, 검색은 `s6`
        를 보고 있었다(`app/core/release.py` 도크스트링).

        ★비공개 이름(`_EXCLUDE` 등)에 손을 대는 것은 임시다. 판정 경로에
          붙일 때(다음 단계) 이 선언 스캐너를 `kcd_ranges` 의 공개 함수로
          올리고 여기서는 그걸 부른다.
    """
    exception_spans = [(m.start(), m.end()) for m in kcd._EXCEPT.finditer(text)]

    def _in_exception(at: int) -> bool:
        return any(s <= at < e for s, e in exception_spans)

    declarations = sorted(
        [(m.start(), "exclude") for m in kcd._EXCLUDE.finditer(text)]
        + [
            (m.start(), "cover")
            for m in kcd._COVER_DECL.finditer(text)
            if not _in_exception(m.start())
        ]
    )
    return declarations, exception_spans


def _scope_at(
    pos: int, declarations: list[tuple[int, str]], exception_spans: list[tuple[int, int]]
) -> str:
    """`pos` 자리의 성격. `kcd_ranges.scan_clause` 와 같은 규칙이다.

    ★고정 창이 아니라 **선언이 열어 놓은 범위**로 본다. 면책 선언이 나오면
      그 뒤는 다음 선언까지 전부 그 지배를 받는다. 약관이 실제로 그렇게 쓰인다.
    """
    if any(s <= pos < e for s, e in exception_spans):
        return "exception"
    kind = ""
    for at, k in declarations:
        if at <= pos:
            kind = k
        else:
            break
    return "exclude" if kind == "exclude" else "mention"


# ── 참조 읽기 ────────────────────────────────────────────────────────


def _norm(s: str) -> str:
    """공백·줄바꿈 제거 + 동그라미 숫자 → 아라비아."""
    s = re.sub(r"\s+", "", s or "")
    return "".join(_CIRCLED.get(ch, ch) for ch in s)


def _split_number_title(inner: str) -> tuple[str, str]:
    """라벨 안쪽 → `(번호, 제목)`.

    `"1"` → `("1", "")` · `"3 “재해분류표”에서 정한 재해를…"` → `("3", "재해분류표")`
    `"-실손의료비관련1"` → `("", "실손의료비관련1")` — 앞이 하이픈이면 번호가 아니다
    """
    t = _norm(inner)
    m = _NUM.match(t)
    number = ""
    if m:
        number = m.group(1) + (f"의{m.group(2)}" if m.group(2) else "")
        t = t[m.end() :]
    title = _TITLE_STRIP.sub("", t)
    title = _TITLE_TAIL.sub("", title)
    title = _TITLE_STRIP.sub("", title)
    return number, title


def find_refs(text: str, *, window: int = 160, include_bare: bool = False) -> list[AnnexRef]:
    """조항 본문에서 부록 참조를 전부 찾는다.

    Args:
        include_bare: 맨몸형(`별표 1`)도 찾을지. **기본은 끈다** — 실측상
            거의 전부 법령 조문 본문이라 우리 부록과 가릴 근거가 약하다.
    """
    text = text or ""
    declarations, exception_spans = _declaration_scope(text)
    out: list[AnnexRef] = []
    spans: list[tuple[int, int]] = []

    def _add(m: re.Match, bracketed: bool) -> None:
        kind = _norm(m.group(1))
        number, title = _split_number_title(m.group(2))
        head = text[max(0, m.start() - _STATUTE_WINDOW) : m.start()]
        tail = text[m.end() : m.end() + _CONDITIONAL_WINDOW]
        out.append(
            AnnexRef(
                raw=m.group(0),
                kind=kind,
                number=number,
                title=title,
                at=m.start(),
                scope=_scope_at(m.start(), declarations, exception_spans),
                context=text[max(0, m.start() - window) : m.start() + window].replace("\n", " "),
                looks_statute=bool(_STATUTE_NEAR.search(head)),
                conditional=bool(_CONDITIONAL.search(tail)),
                bracketed=bracketed,
            )
        )

    for m in _REF_BRACKET.finditer(text):
        spans.append((m.start(), m.end()))
        _add(m, True)

    if include_bare:
        for m in _REF_BARE.finditer(text):
            #: 괄호형 안쪽에서 다시 잡히는 것은 버린다 — 같은 참조를 두 번 센다.
            if any(s <= m.start() < e for s, e in spans):
                continue
            _add(m, False)

    return out


# ── 부록 색인 ────────────────────────────────────────────────────────


@dataclass
class _AnnexIndex:
    """문서 하나의 부록 라벨 색인."""

    by_key: dict[str, list[int]] = field(default_factory=dict)
    rows: list[Mapping[str, Any]] = field(default_factory=list)


def _index_annexes(annexes: Sequence[Mapping[str, Any]]) -> _AnnexIndex:
    """`annexes[].label` 을 참조와 같은 방식으로 정규화해 색인한다.

    ★라벨은 참조보다 자유롭다. 실측 60종 이상 —
      `[별표 1]` `【별표①】` `<붙임1_용어의 정의>` `[별표-실손의료비관련1]`
      그리고 **종류 낱말이 아예 없는 것**(`특정질병 분류표` `재해분류표`).
      마지막 부류는 색인에 들어가지 못한다 — `resolve()` 의 ① 주석 참조.
    """
    idx = _AnnexIndex(rows=list(annexes))
    for i, a in enumerate(annexes):
        t = _norm(a.get("label", "") or "").strip("[](){}<>【】〔〕")
        km = re.match(r"^(별표|붙임|별첨)", t)
        if not km:
            continue
        number, _title = _split_number_title(t[km.end() :])
        idx.by_key.setdefault(f"{km.group(1)}|{number}", []).append(i)
    return idx


# ── 해소 ─────────────────────────────────────────────────────────────


#: ★**느슨한 예외 구간.** `kcd_ranges._EXCEPT` 는 닫는 말이 `보상합니다` 여야 하는데,
#:   부록은 표 칸 안이라 말을 줄인다 — 실측 삼성화재 `[별표-실손의료비관련1]` p151:
#:     "(다만, F04~F09, F20~F29 … 요양급여에해당하는의료비는**보상**)"
#:   `합니다` 가 없어 엄격한 규칙이 못 잡는다(부록 전량 자기 스캔에서 `exception` 0건).
#:   그래서 여기서만 닫는 말을 느슨하게 본다. ★대신 이걸로 `exception` 을 **선언하지
#:   않는다** — 느슨한 만큼 헛잡는다("다만, 회사가 보험 금 지급"이 실제로 잡혔다).
#:   잡히면 `mention`(성격 불명)으로 내릴 뿐이다. 모르면 모른다고 한다(CLAUDE.md §0).
_LOOSE_EXCEPT = re.compile(r"다\s*만[^.。]{0,400}?(?:보상|보장|지급)")


def _veto_spans(annex_text: str) -> tuple[list[tuple[int, str]], list[tuple[int, int]]]:
    """부록 안에서 `exclude` 물려주기를 **거부할** 근거들.

    Returns:
        `(선언목록, 거부구간)` — 선언목록은 `_scope_at` 이 쓰는 형식 그대로.
    """
    declarations, strict = _declaration_scope(annex_text)
    loose = [(m.start(), m.end()) for m in _LOOSE_EXCEPT.finditer(annex_text)]
    return declarations, strict + loose


def _inherit(annex_text: str, scope: str) -> tuple[CodeMention, ...]:
    """부록에서 코드를 뽑아 **참조 자리의 성격**을 물려준다.

    ★`scope` 가 `mention` 이면 그대로 `mention` 이다 — 승격시키지 않는다.
    ★`scope` 가 `exclude` 라도 부록이 그 자리에서 **보상 쪽을 말하면** `mention`
      으로 내린다(veto-only. 모듈 도크스트링 ② 참조).

    ★★코드 위치를 알아야 해서 `kcd_ranges.scan_clause` 의 훑기를 그대로 되풀이한다.
      `parse_ranges` 는 위치를 안 돌려준다. **두 벌이 갈라질 위험이 있는 코드다.**
      판정 경로에 붙일 때(다음 단계) `scan_clause` 에 성격 주입 인자를 두고
      여기서는 그걸 부르도록 합친다. 지금은 검증 스크립트가 두 벌의 코드 집합이
      같은지 매번 대조한다.
    """
    text = annex_text or ""
    if not text:
        return ()

    if scope == "exclude":
        decls, vetoes = _veto_spans(text)
    else:
        decls, vetoes = [], []

    def _kind_at(pos: int) -> str:
        if scope != "exclude":
            return scope
        if any(s <= pos < e for s, e in vetoes):
            return "mention"
        #: 부록 자기 선언이 `보상/지급합니다`(cover)면 그 자리는 면책이 아니다.
        #: `_scope_at` 은 cover 를 `mention` 으로 돌려주므로 그대로 쓰면 된다.
        return "exclude" if _scope_at(pos, decls, []) == "exclude" or not decls else "mention"

    out: list[CodeMention] = []
    consumed: list[tuple[int, int]] = []
    for m in kcd._RANGE_RE.finditer(text):
        lo = kcd.CodeRef(m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
        hi_letter = m.group(4) or m.group(1)
        hi = kcd.CodeRef(hi_letter, int(m.group(5)), int(m.group(6)) if m.group(6) else None)
        if lo._key() > hi._key():
            #: 역순 표기는 버린다 — 의도를 추측하지 않는다(`kcd_ranges` 와 같다).
            continue
        consumed.append((m.start(), m.end()))
        out.append(CodeMention(range=kcd.KcdRange(lo, hi), kind=_kind_at(m.start()), context=""))

    #: ★범위가 아닌 **단일 코드**도 분류표에 오른다(`담석증 K80`, `자궁근종 D25`).
    for m in kcd._CODE.finditer(text):
        if any(s <= m.start() < e for s, e in consumed):
            continue
        c = kcd.CodeRef(m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
        out.append(CodeMention(range=kcd.KcdRange(c, c), kind=_kind_at(m.start()), context=""))
    return tuple(out)


def resolve(
    clauses: Sequence[Mapping[str, Any]],
    annexes: Sequence[Mapping[str, Any]],
    *,
    include_bare: bool = False,
) -> AnnexResolution:
    """문서 하나의 별표 참조를 푼다.

    Args:
        clauses: `doc["clauses"]` 그대로.
        annexes: `doc["annexes"]` 그대로. ★`s5` 산출물에는 이 키가 **없다** —
            부록 분리는 `s6` 부터다. 빈 목록을 주면 모든 참조가
            `no_annexes` 로 남는다(조용히 사라지지 않는다).

    Returns:
        `AnnexResolution` — 이은 것과 **못 이은 것**을 함께 담는다.
    """
    idx = _index_annexes(annexes)
    resolved: list[ResolvedRef] = []
    unresolved: list[UnresolvedRef] = []

    for c in clauses:
        c_ord = c.get("ordinal", -1)
        c_no = c.get("qualified_no", "") or ""
        #: ★법령 조문을 그대로 실은 조항(`statute: true`)은 통째로 법령 문맥이다.
        c_statute = bool(c.get("statute", c.get("is_statute", False)))
        for ref in find_refs(c.get("text", ""), include_bare=include_bare):
            reason = ""
            hit: int | None = None
            rule = ""
            cands: tuple[str, ...] = ()

            if ref.looks_statute or c_statute:
                reason = "statute_annex"
            elif not idx.rows:
                reason = "no_annexes"
            else:
                #: ① 종류+번호가 맞는 부록. **이것 하나만 쓴다.**
                #:
                #:   ★제목으로 잇는 규칙(`(별표2 "재해분류표" 참조)` → 라벨
                #:     `재해분류표`)을 만들었다가 **뺐다.** 전량에서 2건 걸렸는데
                #:     원문 PDF 를 열어 보니 **둘 다 틀렸다**(동양생명
                #:     `무배당수호천사평생실손의료비든든보험` p190):
                #:     조항은 `무배당재해사망특약`의 `별표2` 를 가리키는데
                #:     이어진 부록은 p70 `무배당동양종신입원특약`의 것이고
                #:     원문 라벨도 `(별표3)` 이었다. 그 특약의 재해분류표는
                #:     애초에 부록으로 분리되지 않았다.
                #:     내용이 같아 보여도 **인용 위치가 틀리면 틀린 것이다.**
                #:
                #:   그래서 라벨에 종류 낱말이 없는 부록(`특정질병 분류표`
                #:   `재해분류표`)은 지금 **닿지 못한다** — 참조 94건이
                #:   `no_such_annex` 로 남는다. 비워 두고 센다.
                got = idx.by_key.get(ref.key, [])
                rule = "kind_number"

                #: ② ★같은 라벨이 여럿이면 **부(section)로 가른다.**
                #:
                #:   특약마다 별표 번호가 1부터 다시 시작하므로 한 문서에
                #:   `<붙임1_용어의 정의>` 가 셋씩 들어 있다(보통약관·요양병원 특약·
                #:   상급병실료 특약). 조항과 부록의 `section` 이 같은 것 하나뿐이면
                #:   그것이다.
                #:
                #:   ★부 탐지가 완벽하지 않다. 실측으로 `section` 이
                #:   `"적용범위) ②에서 정한 조건을 만족하지 않아 이 특약"` 처럼
                #:   문장 조각인 부록이 있다. 그러면 어느 것과도 안 맞아
                #:   **그대로 `ambiguous_label` 로 남는다** — 억지로 고르지 않는다.
                if len(got) > 1:
                    sec = _norm(c.get("section", "") or "")
                    same = [i for i in got if _norm(idx.rows[i].get("section", "") or "") == sec]
                    if sec and len(same) == 1:
                        got, rule = same, rule + "+section"

                if not got:
                    reason = "no_such_annex"
                elif len(got) > 1:
                    #: ★하나를 골라 주지 않는다. 어느 것인지 **우리가 정할 수 없다**
                    #:   (`file_clause_store.find_by_number` 와 같은 태도).
                    reason = "ambiguous_label"
                    cands = tuple(str(idx.rows[i].get("label", "")) for i in got)
                else:
                    hit = got[0]

            if hit is None:
                unresolved.append(
                    UnresolvedRef(
                        ref=ref,
                        clause_ordinal=c_ord,
                        clause_qualified_no=c_no,
                        reason=reason,
                        candidates=cands,
                    )
                )
                continue

            a = idx.rows[hit]
            resolved.append(
                ResolvedRef(
                    ref=ref,
                    clause_ordinal=c_ord,
                    clause_qualified_no=c_no,
                    annex_ordinal=a.get("ordinal", hit),
                    annex_label=a.get("label", "") or "",
                    match_rule=rule,
                    #: ★조건부 참조는 성격을 물려주지 않는다(`_CONDITIONAL` 주석).
                    mentions=_inherit(
                        a.get("text", ""), "mention" if ref.conditional else ref.scope
                    ),
                )
            )

    return AnnexResolution(resolved=tuple(resolved), unresolved=tuple(unresolved))
