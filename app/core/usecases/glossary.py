"""용어 설명 유스케이스 — "이 말이 무슨 뜻인가"에만 답한다.

★이 유스케이스가 막아야 하는 것

1. **판정으로 넘어가는 것.**
   챗봇이 "그래서 저 보장되나요?" 로 자연스럽게 이어지는데, 여기서 답하면
   약관버전 확정·인용검증·4단 판정을 **전부 건너뛴다.** 그래서 출력에
   `verdict` 가 **없다** — 필드를 안 만들면 실수로도 답할 수 없다.

2. **정의를 지어내는 것.**
   약관에서 못 찾으면 `found=False` 다. LLM 상식으로 메우지 않는다.
   실손보험 용어는 일상어와 뜻이 다르다("상해"·"통원"·"입원"은 정의 조항이 따로 있다).

3. **약관마다 다른 정의를 하나로 합치는 것.**
   같은 낱말이 보험사·세대마다 다르게 정의된다. 여러 구절이 나오면
   **합치지 않고 각각 보여주고**, 출처가 갈리면 그 사실을 경고로 남긴다.

4. **정의가 아닌 언급을 정의라고 하는 것.**
   구절은 「용어의 정의」 조항과 붙임 정의표에서만 모은다. 본문에서
   그 낱말이 쓰인 문장은 정의가 아니다.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from app.core.errors import ValidationErr
from app.core.ports.glossary import GlossarySourcePort, TermPassage

#: 인용할 앞뒤 폭. 정의표는 칸이 무너져 나오므로 뒤쪽을 넉넉히 본다.
_BEFORE = 60
_AFTER = 260

#: 한 번에 보여줄 구절 수. 많이 보여준다고 더 정확해지지 않는다.
DEFAULT_MAX_QUOTES = 3

#: 이보다 짧으면 검색이 아니라 잡음이다("의", "및" …).
_MIN_TERM = 2

#: 인용 창은 원문 줄바꿈 수에 따라 서로 다른 지점에서 잘릴 수 있다. 한쪽이
#: 다른 쪽의 정확한 접두어일 때 같은 문구로 보려면 이만큼은 일치해야 한다.
_MIN_EXACT_PREFIX = 120

#: ★출력에 **항상** 붙는다. 사용자가 이걸 판정으로 읽지 않게 한다.
NOT_A_JUDGMENT = (
    "이 답은 용어의 뜻만 설명합니다. 보장 여부는 여기서 판정하지 않습니다 — "
    "가입한 약관 버전으로 따로 확인해야 합니다."
)


@dataclass(frozen=True)
class TermQuote:
    """원문 인용 하나. **가공하지 않는다.**"""

    quote: str
    kind: str
    insurer: str
    sha256: str
    qualified_no: str
    title: str
    page_from: int
    page_to: int
    content_hash: str

    @property
    def locator(self) -> str:
        where = self.qualified_no or self.title
        return f"{self.sha256[:12]}/{where} p{self.page_from}"


@dataclass(frozen=True)
class TermExplanation:
    """용어 설명 결과.

    ★`verdict` 필드가 **없다.** 있으면 언젠가 채우게 된다.
    """

    term: str
    found: bool
    quotes: tuple[TermQuote, ...] = ()
    #: 정의를 담은 구절이 몇 개나 있었는가. `quotes` 는 그중 일부다.
    total_passages: int = 0
    #: 몇 개 보험사에서 나왔는가. 2 이상이면 정의가 갈릴 수 있다.
    insurers: tuple[str, ...] = ()
    message: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        #: ★구조로 강제한다 — 근거 없이 "찾았다"고 말할 수 없다.
        if self.found and not self.quotes:
            raise ValidationErr("근거 인용 없이 found=True 를 만들 수 없습니다.")


def _quote_around(p: TermPassage, term: str) -> TermQuote | None:
    i = p.text.find(term)
    if i < 0:
        return None
    start = max(0, i - _BEFORE)
    end = min(len(p.text), i + len(term) + _AFTER)
    #: ★잘린 자리를 표시한다. 붙여 놓으면 문장이 거기서 끝난 것처럼 읽힌다.
    quote = ("…" if start > 0 else "") + p.text[start:end] + ("…" if end < len(p.text) else "")
    return TermQuote(
        quote=quote,
        kind=p.kind,
        insurer=p.insurer,
        sha256=p.sha256,
        qualified_no=p.qualified_no,
        title=p.title,
        page_from=p.page_from,
        page_to=p.page_to,
        content_hash=p.content_hash,
    )


def _comparison_text(quote: str, term: str) -> str:
    """줄바꿈·OCR·페이지 장식만 걷어 낸 중복 비교용 문자열."""
    text = unicodedata.normalize("NFKC", quote)
    at = text.find(term)
    if at >= 0:
        text = text[at:]

    kept: list[str] = []
    for line in text.splitlines():
        compact = "".join(line.split())
        if (
            not compact
            or compact.isdigit()
            or len(compact) == 1
            or compact in {"용어", "정의"}
            or "목차로돌아가기" in compact
            or (compact.startswith("무배당") and term not in compact)
        ):
            continue
        kept.append(compact)

    return "".join(ch for ch in "".join(kept) if ch.isalnum())


def _is_same_definition(candidate: TermQuote, previous: TermQuote, term: str) -> bool:
    """같은 보험사의 정규화 문구가 **정확히 같은 경우만** 묶는다."""
    if not candidate.insurer or candidate.insurer != previous.insurer:
        return False
    a = _comparison_text(candidate.quote, term)
    b = _comparison_text(previous.quote, term)
    if not a or not b:
        return False
    if a == b:
        return True
    #: `_quote_around` 는 원문 문자 수로 자른다. 줄바꿈이 많은 표는 정규화 뒤
    #: 더 짧아지므로, 한쪽이 충분히 긴 **정확한 접두어**면 같은 문구다.
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= _MIN_EXACT_PREFIX and longer.startswith(shorter)


def explain(
    term: str,
    *,
    source: GlossarySourcePort,
    insurer: str | None = None,
    max_quotes: int = DEFAULT_MAX_QUOTES,
) -> TermExplanation:
    """약관에서 이 용어의 정의를 찾아 **원문 그대로** 보여준다.

    ★못 찾으면 못 찾았다고 한다. 상식으로 메우지 않는다.
    """
    t = (term or "").strip()
    if len(t) < _MIN_TERM:
        raise ValidationErr(f"용어는 {_MIN_TERM}자 이상이어야 합니다.")

    #: ★`limit=0` 은 **상한 없음**이다. 몇 개인지 세어서 알려주려면 다 훑어야 한다.
    #:   상한을 걸면 `total_passages` 가 상한값으로 굳어 개수인 척한다.
    passages = list(source.find(t, insurer=insurer, limit=0))
    if not passages:
        scope = f"{insurer} 약관" if insurer else "수집한 약관"
        return TermExplanation(
            term=t,
            found=False,
            message=(
                f"{scope}의 「용어의 정의」에서 '{t}' 를 찾지 못했습니다. "
                "약관에 정의가 없는 낱말이거나, 다른 표현으로 적혀 있을 수 있습니다."
            ),
            warnings=(NOT_A_JUDGMENT,),
        )

    representatives: list[TermQuote] = []
    consolidated = 0
    for p in passages:
        q = _quote_around(p, t)
        if q is None:
            continue
        #: 같은 정의가 여러 버전 문서에 줄바꿈·쪽장식만 달리해 실린다.
        #: 원문 인용은 그대로 두되, 같은 보험사의 동일 문구는 대표 하나로 묶는다.
        if any(_is_same_definition(q, old, t) for old in representatives):
            consolidated += 1
            continue
        representatives.append(q)

    quotes = representatives[:max_quotes]

    if not quotes:
        return TermExplanation(
            term=t,
            found=False,
            total_passages=len(passages),
            message=f"'{t}' 가 실린 구절은 있으나 인용할 위치를 잡지 못했습니다.",
            warnings=(NOT_A_JUDGMENT,),
        )

    insurers = tuple(sorted({p.insurer for p in passages if p.insurer}))
    warnings = [NOT_A_JUDGMENT]
    if len(insurers) > 1:
        warnings.append(
            f"이 용어는 {len(insurers)}개 보험사 약관에 나옵니다. "
            "정의가 보험사·세대마다 다를 수 있어 합치지 않고 각각 보여줍니다. "
            "가입한 약관의 정의를 확인하세요."
        )
    if consolidated:
        warnings.append(
            f"같은 보험사의 동일 정의 {consolidated}개는 줄바꿈·OCR 차이를 합쳐 "
            "대표 인용으로 묶었습니다."
        )
    if len(representatives) > len(quotes):
        warnings.append(
            f"서로 다른 정의 {len(representatives)}개 중 {len(quotes)}개만 보여줍니다."
        )
    #: ★붙임 정의표는 **칸이 무너진 채로** 인용된다. 그 사실을 감추지 않는다.
    if any(q.kind == "appendix" for q in quotes):
        warnings.append(
            "붙임 정의표는 원문이 표라서 줄과 칸이 흐트러져 보일 수 있습니다. "
            "인용문은 가공하지 않은 그대로입니다."
        )

    return TermExplanation(
        term=t,
        found=True,
        quotes=tuple(quotes),
        total_passages=len(passages),
        insurers=insurers,
        message=f"약관 원문에서 '{t}' 의 정의를 찾았습니다.",
        warnings=tuple(warnings),
    )


__all__ = ["TermExplanation", "TermQuote", "explain", "NOT_A_JUDGMENT", "DEFAULT_MAX_QUOTES"]
