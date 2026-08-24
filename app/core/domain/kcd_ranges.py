"""KCD(한국표준질병사인분류) 코드 — 파싱·범위 판정.

★핵심 발견 — **약관이 KCD 코드를 직접 쓴다**

    면책 조항이 질병명이 아니라 **코드 범위**로 적혀 있다(실측).

        ② 회사는 '한국표준질병사인분류'에 따른 다음의 의료비에 대해서는 보상하지 않습니다.
           ① 정신 및 행동장애(F04∼F99). 다만, F04∼F09, F20∼F29, F30∼F39,
              F40∼F48, F51, F90∼F98 과 관련한 치료에서 발생한 …요양급여에
              해당하는 의료비는 보상합니다.
           ③ 피보험자가 임신, 출산…한 경우(O00∼O99)
           ⑤ 요실금(N39.3, N39.4, R32)

    표본 300문서 중 **239개(80%)** 에 코드가 있다.
    즉 "이 질병코드가 면책인가"는 **외부 KCD 표 없이** 약관만으로 답할 수 있다.

    ★외부 KCD 표가 필요한 곳은 따로 있다 — **질병명 → 코드**.
      사용자가 "우울증"이라고 입력하면 `F32` 를 찾아 줘야 한다.
      그건 이 모듈이 하지 않는다(§`lookup_by_name` 없음).
      진료비 내역서에는 **코드가 이미 적혀 있으므로** 그쪽이 주 경로다.

★코드 표기

    `F04`        3자리 분류
    `F04.1`      세분류
    `F04∼F99`    범위. 구분자가 `∼`(1,817) `～`(2,042) `~`(2,268) `-`(2) 로 섞여 있다
    `N39.3`      세분류 단독 지정(요실금)

★비교는 문자열이 아니라 (알파벳, 숫자) 로 한다

    `"F9" < "F04"` 는 문자열로 참이다. 코드는 **분류문자 + 숫자**이므로
    두 부분을 나눠 비교해야 한다.

★세분류 포함 규칙

    `F04∼F09` 범위에 `F04.1` 이 드는가 → **든다.**
    3자리 범위는 그 아래 세분류를 모두 포함한다는 것이 KCD 관행이다.
    반대로 `N39.3` 처럼 세분류를 콕 집으면 `N39.0` 은 **안 든다.**

이 모듈은 **프레임워크도 바깥 계층도 모른다**(클린아키텍처 2단계 안쪽).
표준 라이브러리 `re` 만 쓴다. 파일·DB·HTTP 를 건드리지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: `F04`, `F04.1`. 앞뒤에 글자·숫자가 붙으면 코드가 아니다.
_CODE = re.compile(r"(?<![A-Za-z0-9])([A-Z])(\d{2})(?:\.(\d{1,2}))?(?![0-9])")
#: 범위 구분자 — 실측으로 확인된 것만.
_RANGE_SEP = r"[~∼～\-]"


@dataclass(frozen=True)
class CodeRef:
    """약관 본문에서 읽은 **코드 참조** 하나. `F04` 또는 `F04.1`.

    ★`app/core/domain/insurance.py` 의 `KcdCode` 와 **다른 것**이다.

        저쪽은 KCD **마스터 항목**이다 — 차수(`version_label`)와 한글명(`name_ko`)을
        갖고 "J20.9 는 몇 차 분류의 무엇인가"를 답한다.
        이쪽은 **약관 문장에서 뽑은 표기**다. 차수도 이름도 모르고,
        범위 안에 드는지만 따진다.

        처음엔 둘 다 `KcdCode` 라 이름 붙였는데, ARCH-003 의 단일 정의 검사가
        **타입 이름 셋을 하드코딩**하고 있어서 못 잡았다(코덱스가 잡았다).
        역할이 다르면 이름을 나눈다.
    """

    letter: str
    number: int
    sub: int | None = None

    @classmethod
    def parse(cls, s: str) -> "CodeRef | None":
        m = _CODE.fullmatch((s or "").strip().upper())
        if not m:
            return None
        return cls(m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None)

    def __str__(self) -> str:
        base = f"{self.letter}{self.number:02d}"
        return f"{base}.{self.sub}" if self.sub is not None else base

    @property
    def base(self) -> "CodeRef":
        """세분류를 뗀 3자리 코드."""
        return CodeRef(self.letter, self.number)

    def _key(self) -> tuple[str, int, int]:
        #: 세분류가 없으면 -1 — 정렬에서 `F04` 가 `F04.0` 앞에 오게 한다.
        return (self.letter, self.number, self.sub if self.sub is not None else -1)


@dataclass(frozen=True)
class KcdRange:
    """코드 범위. `lo == hi` 면 단일 코드다."""

    lo: CodeRef
    hi: CodeRef

    def contains(self, code: CodeRef) -> bool:
        """`code` 가 이 범위에 드는가.

        ★3자리 범위는 아래 세분류를 **포함한다.** `F04∼F09` 에 `F04.1` 이 든다.
        ★세분류를 콕 집은 범위(`N39.3`)에는 `N39.0` 이 **안 든다.**
        """
        if self.lo.letter != code.letter or self.hi.letter != code.letter:
            #: 분류문자가 다르면 범위 밖이다. `F..` 와 `N..` 은 섞이지 않는다.
            if not (self.lo.letter <= code.letter <= self.hi.letter):
                return False
        exact_sub = self.lo.sub is not None or self.hi.sub is not None
        target = code if exact_sub else code.base
        return self.lo._key() <= target._key() <= self.hi._key()

    def __str__(self) -> str:
        return str(self.lo) if self.lo == self.hi else f"{self.lo}~{self.hi}"


def parse_codes(text: str) -> list[CodeRef]:
    """본문에서 코드를 전부 뽑는다(범위 표기는 양끝이 각각 잡힌다)."""
    out: list[CodeRef] = []
    for m in _CODE.finditer(text or ""):
        out.append(
            CodeRef(m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
        )
    return out


_RANGE_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z])(\d{2})(?:\.(\d{1,2}))?"
    rf"\s*{_RANGE_SEP}\s*"
    r"([A-Z])?(\d{2})(?:\.(\d{1,2}))?(?![0-9])"
)


def parse_ranges(text: str) -> list[KcdRange]:
    """본문에서 코드 **범위**를 뽑는다.

    `F04∼F99` 는 물론 `F04∼09` 처럼 뒤 분류문자가 생략된 표기도 받는다.
    범위가 아닌 단일 코드는 `lo == hi` 로 함께 담는다.
    """
    ranges: list[KcdRange] = []
    consumed: list[tuple[int, int]] = []
    for m in _RANGE_RE.finditer(text or ""):
        lo = CodeRef(m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
        hi_letter = m.group(4) or m.group(1)
        hi = CodeRef(hi_letter, int(m.group(5)), int(m.group(6)) if m.group(6) else None)
        if lo._key() > hi._key():
            #: `F99∼F04` 같은 역순은 표기 오류다. 뒤집지 않고 버린다 —
            #: 우리가 의도를 추측하면 안 된다.
            continue
        ranges.append(KcdRange(lo, hi))
        consumed.append((m.start(), m.end()))

    #: 범위에 안 쓰인 단일 코드도 담는다.
    for m in _CODE.finditer(text or ""):
        if any(s <= m.start() < e for s, e in consumed):
            continue
        c = CodeRef(m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
        ranges.append(KcdRange(c, c))
    return ranges


#: ★낱말 사이에 **페이지 번호가 끼어든다.**
#:
#:   실측: `…의료비는 보상    36    합니다)`
#:   페이지 푸터의 `36` 이 본문 텍스트에 섞여 들어와 `보상합니다` 를 쪼갠다.
#:   이걸 모르면 "다만 … 보상합니다" 를 못 찾아 **예외를 면책으로 판정한다.**
#:   그러면 보장되는 질병을 "안 됩니다"라고 답하게 된다.
_GAP = r"[\s\d]{0,12}"

#: 면책을 뜻하는 문구.
_EXCLUDE = re.compile(
    rf"보상하지{_GAP}않|보상하지{_GAP}아니|지급하지{_GAP}않|보장하지{_GAP}않|면책"
)
#: **보상한다**는 선언. 면책 범위를 닫는다.
#: ★`다만 … 보상합니다`(예외)와 구분하려고 `다만` 이 바로 앞에 없는 것만 본다.
_COVER_DECL = re.compile(
    rf"(?<!다만)(?:다음의?[^.。]{{0,40}})?보상{_GAP}합니다|보상하는\s*사항|보상하여\s*드립니다"
)

#: 면책의 **예외**(다시 보상)를 뜻하는 문구.
#:
#: ★구간을 200자로 잡았더니 놓쳤다. 실제 예외 문장은 이렇게 길다.
#:   "다만, F04～F09, F20～F29, F30～F39, F40～F48, F51, F90～F98과 관련한
#:    치료에서 발생한 「국민건강보험법」에 따른 요양급여에 해당하는 의료비는 보상합니다"
#:   범위를 여럿 나열하면 쉽게 300자를 넘는다.
_EXCEPT = re.compile(
    rf"다만[^.。]{{0,400}}?(?:보상{_GAP}합니다|보장{_GAP}합니다|지급{_GAP}합니다)"
)


@dataclass(frozen=True)
class CodeMention:
    """조항에서 발견한 코드 범위 하나와 그 성격."""

    range: KcdRange
    #: `"exclude"` 면책 · `"exception"` 면책의 예외(보상) · `"mention"` 성격 불명
    kind: str
    context: str


def scan_clause(text: str, *, window: int = 160) -> list[CodeMention]:
    """조항 본문에서 코드 범위를 뽑고 **면책인지 예외인지** 표시한다.

    ★성격을 단정하지 않는다

        문맥 창(앞뒤 `window`자) 안에 면책 문구가 있으면 `exclude`,
        `다만 … 보상합니다` 안에 들어 있으면 `exception`,
        둘 다 없으면 `mention` 으로 둔다.

        `mention` 은 **판정에 쓰면 안 된다.** 성격을 모르는 코드다.
        (예: 「의료법」 제3조 인용 옆에 우연히 붙은 코드)
    """
    text = text or ""
    exception_spans = [(m.start(), m.end()) for m in _EXCEPT.finditer(text)]

    #: ★고정 창(앞뒤 160자)으로 성격을 정하면 **긴 목록의 뒤쪽을 놓친다.**
    #:
    #:   "회사는 다음의 의료비에 대해서는 보상하지 않습니다.
    #:    ① 정신 및 행동장애(F04∼F99) … ② … ③ … ④ … ⑤ 비만(E66) ⑥ 요실금(N39.3)"
    #:
    #:   `⑤ 비만(E66)` 은 선언부에서 수백 자 떨어져 있어 창 안에 안 들어온다.
    #:   그래서 면책인데 "목록에 없음"이라고 답했다.
    #:
    #:   대신 **선언이 열어 놓은 범위**로 본다. 면책 선언이 나오면 그 뒤는
    #:   다음 선언이 나올 때까지 전부 그 선언의 지배를 받는다.
    #:   약관이 실제로 그렇게 쓰인다.
    #: ★`다만 … 보상합니다` 의 `보상합니다` 를 보상 선언으로 세면 안 된다.
    #:   그러면 예외 문장이 **뒤따르는 면책 목록을 통째로 닫아 버린다.**
    #:   실측: `① 정신(F04∼F99). 다만 … 보상합니다. ③ 임신·출산(O00∼O99)`
    #:   에서 `O00∼O99` 가 면책이 아니라 `mention` 이 됐다.
    def _in_exception(at: int) -> bool:
        return any(s <= at < e for s, e in exception_spans)

    declarations = sorted(
        [(m.start(), "exclude") for m in _EXCLUDE.finditer(text)]
        + [
            (m.start(), "cover")
            for m in _COVER_DECL.finditer(text)
            if not _in_exception(m.start())
        ]
    )

    def _scope_at(pos: int) -> str:
        """`pos` 를 지배하는 가장 가까운 앞쪽 선언."""
        kind = ""
        for at, k in declarations:
            if at <= pos:
                kind = k
            else:
                break
        return kind

    def _kind_at(pos: int) -> tuple[str, str]:
        ctx = text[max(0, pos - window) : pos + window].replace("\n", " ")
        if any(s <= pos < e for s, e in exception_spans):
            return "exception", ctx
        scope = _scope_at(pos)
        if scope == "exclude":
            return "exclude", ctx
        return "mention", ctx

    out: list[CodeMention] = []
    consumed: list[tuple[int, int]] = []
    for m in _RANGE_RE.finditer(text):
        lo = CodeRef(m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
        hi_letter = m.group(4) or m.group(1)
        hi = CodeRef(hi_letter, int(m.group(5)), int(m.group(6)) if m.group(6) else None)
        if lo._key() > hi._key():
            continue
        consumed.append((m.start(), m.end()))
        kind, ctx = _kind_at(m.start())
        out.append(CodeMention(KcdRange(lo, hi), kind, ctx))

    #: ★범위가 아닌 **단일 코드**도 면책 목록에 오른다. 이걸 빼면 놓친다.
    #:   실측: `⑤ 비만(E66)`, `⑥ 요실금(N39.3, N39.4, R32)`
    #:   처음엔 범위만 훑어서 이 둘을 "면책 목록에 없음"으로 답했다.
    for m in _CODE.finditer(text):
        if any(s <= m.start() < e for s, e in consumed):
            continue
        c = CodeRef(m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
        kind, ctx = _kind_at(m.start())
        out.append(CodeMention(KcdRange(c, c), kind, ctx))
    return out


def judge(code: str, mentions: list[CodeMention]) -> dict:
    """코드 하나에 대해 면책 여부를 본다.

    ★"보장된다"고 말하지 않는다

        면책 목록에 없다는 것이 곧 보장이라는 뜻은 아니다.
        보장은 **보상하는 사항** 조항이 정하고, 여기서는 그걸 보지 않는다.
        그래서 결과는 `excluded` / `exception` / `not_mentioned` 뿐이다.
    """
    c = CodeRef.parse(code)
    if c is None:
        return {"code": code, "status": "invalid_code", "hits": []}

    hits = [m for m in mentions if m.range.contains(c)]
    #: 예외가 면책을 덮는다 — "다만 …는 보상합니다"가 뒤에 오는 규정이다.
    if any(m.kind == "exception" for m in hits):
        status = "exception"
    elif any(m.kind == "exclude" for m in hits):
        status = "excluded"
    else:
        status = "not_mentioned"
    return {
        "code": str(c),
        "status": status,
        "hits": [
            {"range": str(m.range), "kind": m.kind, "context": m.context} for m in hits
        ],
    }


#: ── KCD 장(chapter) 분류 ─────────────────────────────────────────────
#:
#: ★★**이 표는 `scripts/viz/regen_preprocess_viz.py` 안에 묻혀 있었다**(2026-08-04).
#:   시각화 스크립트가 도메인 지식을 들고 있으면, 그 지식이 필요한 다른 곳
#:   (관리자 화면·API)이 **같은 표를 또 만든다.** 그러면 둘이 어긋난다.
#:   그래서 도메인으로 옮긴다. 시각화는 여기서 가져다 쓴다.
#:
#: ★이건 **장 이름**이지 질병명이 아니다. 우리는 KCD 코드→질병명 사전을 갖고 있지
#:   않다(약 2만 항목). `F32` 가 「우울에피소드」라고 말할 근거가 없으므로
#:   **말하지 않는다.** 말할 수 있는 것은 「F 는 정신·행동 장」까지다.
#:   (이 모듈 머리말 §"외부 KCD 표가 필요한 곳은 따로 있다" 와 같은 이야기다.)
_CHAPTERS: tuple[tuple[str, str, str], ...] = (
    ("A00", "B99", "1 감염성·기생충"), ("C00", "D48", "2 신생물"), ("D50", "D89", "3 혈액·면역"),
    ("E00", "E90", "4 내분비·대사"), ("F00", "F99", "5 정신·행동"), ("G00", "G99", "6 신경"),
    ("H00", "H59", "7 눈"), ("H60", "H95", "8 귀"), ("I00", "I99", "9 순환"),
    ("J00", "J99", "10 호흡"), ("K00", "K93", "11 소화"), ("L00", "L99", "12 피부"),
    ("M00", "M99", "13 근골격"), ("N00", "N99", "14 비뇨생식"), ("O00", "O99", "15 임신·출산"),
    ("P00", "P96", "16 출생전후기"), ("Q00", "Q99", "17 선천기형"), ("R00", "R99", "18 증상·징후"),
    ("S00", "T98", "19 손상·중독"), ("V01", "Y98", "20 외인"), ("Z00", "Z99", "21 보건서비스"),
    ("U00", "U99", "22 특수목적"),
)

_CHAPTER_KEYS = tuple(
    ((a[0], int(a[1:3])), (b[0], int(b[1:3])), name) for a, b, name in _CHAPTERS
)


def chapter_of(code: "CodeRef | str") -> str:
    """이 코드가 속한 KCD 장 이름. 모르면 `"기타"`.

    ★비교는 문자열이 아니라 `(알파벳, 숫자)` 로 한다 — `"C00" <= "D48"` 은
      문자열로도 맞지만 `"H60" <= "H95"` 처럼 같은 글자 안에서 갈리는 구간이 있어
      숫자를 따로 봐야 한다.
    """
    c = CodeRef.parse(code) if isinstance(code, str) else code
    if c is None:
        return "기타"
    k = (c.letter, c.number)
    for lo, hi, name in _CHAPTER_KEYS:
        if lo <= k <= hi:
            return name
    return "기타"
