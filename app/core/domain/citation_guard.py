"""인용 검증 — LLM 이 댄 조항이 **근거에 실제로 있는지** 대조한다.

이 모듈은 **프레임워크도 바깥 계층도 모른다**(클린아키텍처 2단계 안쪽).
검증에 필요한 것은 인자로 받는다 — 저장소를 직접 읽지 않는다.

★왜 필요한가 — 프롬프트는 방어선이 아니다

    근거가 0건이면 LLM 을 부르지 않는다. 그런데 근거가 **하나라도** 있으면
    LLM 이 호출되고 그 다음은 프롬프트에 맡겨진다. 조항에 "우울증"이 없는데
    "정신과 질환이니 비슷하게 보장될 겁니다" 처럼 지어낼 위험이 여기 있다.

    ★이건 코드로 강제할 수 있다. LLM 이 인용한 조항을 뽑아 근거와 대조하고,
      없으면 그 답을 버린다. 프롬프트를 무시하는 모델도 이 검사는 못 지나간다.

────────────────────────────────────────────────────────────────────────
★v2 — 전면 재작성. v1 은 **87% 문서에서 무력했다.**
────────────────────────────────────────────────────────────────────────

v1 은 조항 키에서 부(部) 이름을 뗐다.

    def _clause_key(qualified_no):  # v1
        return "제9조"   # `보통약관/제 9 조` → `제9조`

의도는 타당했다 — *"LLM 이 부 이름까지 정확히 쓰길 기대할 수 없다.
부까지 맞추길 요구하면 정답인데 폐기하는 일이 생긴다."*
**결과가 뒤집혔다.** 실측(v4 전량 1,367문서):

    qualified_no 그대로   중복 31,085건 / 1,181문서(86%)  최대 25
    v1 의 키 적용 후      중복 71,707건 / 1,191문서(87%)  최대 96

한 문서에서 키 `'1.'` 에 9개가 뭉쳤다.

    보통약관/1.                        p34  '보장종목'
    특별약관/1.                        p58  '보장종목'
    이륜자동차 운전중상해 부담보 특별약관/1.  p72  '특별약관의 체결 및 효력'

그래서 두 가지가 깨졌다.

  1) LLM 이 **어느 특약의 1조를 인용하든** 허용 목록을 통과한다 → 검증 무력화
  2) `by_key` 가 dict comprehension 이라 **95개가 마지막 것에 덮인다** →
     인용문 원문 대조가 **다른 조항 본문**과 이뤄져 틀린 근거가 "검증됨"으로 나온다

코덱스 교차검증에서 결함이 더 나왔다.

  3) `ok = not unknown` 이라 **빈 `cited_clauses` 로도 통과**했다
  4) `quote_mismatches` · `undeclared_mentions` 가 경고여서
     **틀린 인용문도 통과**했다
  5) `_NUM.search()` 라 `"아무말 제9조 아무말"` 이 통과했다
  6) `1.`(호 번호)과 `제1조`를 같은 것으로 봤다 — 문법 층이 다르다
  7) `quotes` 가 dict 라 같은 조항의 복수 인용이 덮였다

★v2 의 설계

  · **핸들로 인용하게 한다.** 근거마다 `E001` 같은 요청 단위 손잡이를 주고
    LLM 은 그걸로 인용한다. 이름·번호를 정확히 쓰라고 기대하지 않는다.
  · 핸들이 없으면 **전체 경로**(`보통약관/제9조`)로 맞춘다.
  · 번호만 왔으면 **후보가 정확히 1개일 때만** 해소한다.
  · 0개면 `unknown`, **2개 이상이면 `ambiguous`** — 느슨하게 통과시키지도,
    빡빡하게 폐기하지도 않고 **모른다고 한다.**
  · 승인은 **fail-closed** 다. 인용이 하나도 없거나, 하나라도 해소 실패거나,
    인용문이 원문과 안 맞으면 그 답은 쓰지 않는다.

★이 검증기가 하지 못하는 것 (정직 기록)

  인용문이 **결론을 뒷받침하는지**는 보지 않는다. 문자열 포함 검사이지
  함의(entailment) 검사가 아니다. 정확한 조항의 진짜 문장을 인용하면서
  엉뚱한 결론을 내는 것은 여기서 못 막는다.

  ★그래서 최종 구조는 **규칙 엔진이 판정을 소유하고 LLM 은 설명만 만드는 것**이다.
    이 검증기로 LLM 의 판정 자체를 검증하려 하면 안전장치가 완성되지 않는다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256

#: 조 단위 인용. `제9조`, `제9조의2`. ★`fullmatch` 로 쓴다 —
#: `search` 면 `"아무말 제9조 아무말"` 이 통과한다(코덱스 지적).
_ARTICLE_FULL = re.compile(r"제\s*(\d{1,3})\s*조(?:\s*의\s*(\d{1,2}))?")
#: 특별약관의 번호 조항. `1.`, `4-1.` — ★`제1조` 와 **다른 문법 층**이다.
_NUMBERED_FULL = re.compile(r"(\d{1,3})(?:-(\d{1,2}))?\s*[.．]?")
#: 본문에서 조항을 말한 흔적(선언 누락 확인용). 여기서는 `search` 가 맞다.
_MENTION = re.compile(r"제\s*(\d{1,3})\s*조(?:\s*의\s*(\d{1,2}))?")
#: 근거 손잡이. `E001`.
_HANDLE = re.compile(r"^E\d{3,4}$")


class Resolution(str, Enum):
    """인용 하나를 어떻게 해소했나."""

    #: 손잡이 또는 전체 경로로 정확히 맞았다.
    EXACT = "exact"
    #: 번호만 왔는데 후보가 **정확히 1개**여서 해소했다.
    RESOLVED = "resolved"
    #: 후보가 없다. 근거에 없는 조항을 인용했다.
    UNKNOWN = "unknown"
    #: 후보가 2개 이상이다. **어느 것인지 모른다.**
    AMBIGUOUS = "ambiguous"
    #: 조항 표기로 읽히지 않는다.
    INVALID = "invalid"


@dataclass(frozen=True)
class EvidenceClause:
    """판정에 넘긴 근거 조항 하나.

    `handle` 은 **요청 단위**다. 다음 요청에서 같은 값이 다른 조항을 가리켜도 된다.
    영속 식별자가 아니다 — 그건 저장소가 따로 갖는다.
    """

    qualified_no: str
    text: str
    handle: str = ""
    clause_id: str = ""


@dataclass(frozen=True)
class CitationCheck:
    """인용 하나의 검증 결과."""

    cited: str
    resolution: Resolution
    matched: EvidenceClause | None = None
    #: 후보가 여럿일 때 무엇들이었나(사용자에게 되물을 때 쓴다).
    candidates: tuple[str, ...] = ()
    #: 인용문이 원문에 없으면 여기 남는다.
    quote_mismatch: str = ""


@dataclass
class GuardResult:
    """검증 결과. `ok=False` 면 **그 답을 쓰면 안 된다.**"""

    ok: bool
    checks: list[CitationCheck] = field(default_factory=list)
    #: 본문에서 조항을 말했는데 `cited_clauses` 에 선언하지 않았다.
    undeclared_mentions: list[str] = field(default_factory=list)
    #: 인용이 하나도 없다.
    no_citations: bool = False

    @property
    def unknown_citations(self) -> list[str]:
        return [c.cited for c in self.checks if c.resolution is Resolution.UNKNOWN]

    @property
    def ambiguous_citations(self) -> list[str]:
        return [c.cited for c in self.checks if c.resolution is Resolution.AMBIGUOUS]

    @property
    def invalid_citations(self) -> list[str]:
        return [c.cited for c in self.checks if c.resolution is Resolution.INVALID]

    @property
    def quote_mismatches(self) -> list[str]:
        return [c.cited for c in self.checks if c.quote_mismatch]

    @property
    def reason_code(self) -> str:
        """왜 폐기했나. `ok=True` 면 빈 문자열."""
        if self.ok:
            return ""
        if self.no_citations:
            return "no_citation"
        if self.invalid_citations:
            return "invalid_citation"
        if self.unknown_citations:
            return "citation_not_in_evidence"
        if self.ambiguous_citations:
            return "ambiguous_citation"
        if self.quote_mismatches:
            return "quote_not_in_source"
        if self.undeclared_mentions:
            return "undeclared_citation"
        return "unverified"

    @property
    def reason(self) -> str:
        if self.ok:
            return ""
        parts = []
        if self.no_citations:
            parts.append("인용한 조항이 없습니다")
        if self.invalid_citations:
            parts.append(f"조항 표기가 아닙니다: {', '.join(self.invalid_citations)}")
        if self.unknown_citations:
            parts.append(f"근거에 없는 조항입니다: {', '.join(self.unknown_citations)}")
        if self.ambiguous_citations:
            parts.append(
                f"어느 조항인지 특정할 수 없습니다: {', '.join(self.ambiguous_citations)}"
            )
        if self.quote_mismatches:
            parts.append(f"인용문이 원문에 없습니다: {', '.join(self.quote_mismatches)}")
        if self.undeclared_mentions:
            parts.append(
                "본문에서 말했으나 인용에 선언하지 않았습니다: "
                f"{', '.join(self.undeclared_mentions)}"
            )
        return " / ".join(parts)


def _norm_space(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _parse_clause_ref(s: str) -> tuple[str, str] | None:
    """조항 표기를 `(문법종류, 정규화 번호)` 로. 못 읽으면 None.

    ★`제1조` 와 `1.` 을 **다른 것**으로 본다. 약관에서 층이 다르다 —
      `제N조` 는 조이고 `1.` 은 특별약관의 조이거나 본문의 호일 수 있다.
      v1 은 둘을 같은 키로 만들어 섞었다.
    """
    t = (s or "").strip()
    if not t:
        return None
    m = _ARTICLE_FULL.fullmatch(t)
    if m:
        no = f"제{int(m.group(1))}조" + (f"의{int(m.group(2))}" if m.group(2) else "")
        return ("article", no)
    m = _NUMBERED_FULL.fullmatch(t)
    if m:
        no = f"{int(m.group(1))}." + (f"{int(m.group(2))}" if m.group(2) else "")
        return ("numbered", no)
    return None


def _split_path(qualified_no: str) -> tuple[str, str]:
    """`보통약관/제9조` → `(보통약관, 제9조)`."""
    if "/" in (qualified_no or ""):
        head, tail = qualified_no.rsplit("/", 1)
        return head.strip(), tail.strip()
    return "", (qualified_no or "").strip()


def _index(
    evidence: list[EvidenceClause],
) -> tuple[dict, dict, dict]:
    """`(핸들→근거, 전체경로→근거, 번호→근거목록)`.

    ★번호 색인은 **list** 다. dict 로 만들면 같은 번호의 다른 조항이 덮인다
      (v1 이 그랬다 — 한 키에 96개가 뭉쳐 95개가 사라졌다).

    ★★**경로 색인도 list 다.** 위 경고를 적어 놓고 바로 아래 줄에서
      `by_path[...] = e` 로 덮어쓰고 있었다(코덱스 지적 · `feature-agent` 브랜치가
      먼저 고쳤다). 같은 `qualified_no` 가 **여러 문서·여러 쪽**에 실린다 —
      중복률 66% 인 코퍼스에서 이건 드문 일이 아니다.
      덮어쓰면 뒤에 온 것만 남아, 인용문이 **앞의 것에 있어도 못 찾는다.**
    """
    by_handle: dict[str, EvidenceClause] = {}
    by_path: dict[str, list[EvidenceClause]] = defaultdict(list)
    by_no: dict[tuple[str, str], list[EvidenceClause]] = defaultdict(list)
    for e in evidence:
        if e.handle:
            by_handle[e.handle.strip().upper()] = e
        by_path[_norm_space(e.qualified_no)].append(e)
        ref = _parse_clause_ref(_split_path(e.qualified_no)[1])
        if ref:
            by_no[ref].append(e)
    return by_handle, by_path, by_no


def _with_quote(
    cited: str, hit: EvidenceClause, res: Resolution, quotes: dict[str, list[str]]
) -> CitationCheck:
    """인용문이 원문에 실제로 있는지 확인한다."""
    for q in quotes.get(cited, []):
        if not q.strip():
            #: ★빈 인용문은 대조를 건너뛰는 것이 아니라 **실패**다.
            #:   건너뛰면 `quote=""` 로 검사를 통째로 우회할 수 있다(코덱스 지적).
            return CitationCheck(cited, res, hit, quote_mismatch="(빈 인용문)")
        if _norm_space(q) not in _norm_space(hit.text):
            return CitationCheck(cited, res, hit, quote_mismatch=q[:40])
    return CitationCheck(cited, res, hit)


def _logical_key(evidence: EvidenceClause) -> tuple[str, ...]:
    """같은 경로에 모인 근거를 논리 조항 단위로 구분한다.

    영속 ``clause_id``가 있으면 그것이 정체성이다. ID가 없는 입력에서는 같은 경로와
    같은 본문만 중복 occurrence로 접는다. 경로만 같고 본문이 다르면 서로 다른 조항이다.
    """
    clause_id = (evidence.clause_id or "").strip()
    if clause_id:
        return ("clause_id", clause_id)
    return (
        "content",
        _norm_space(evidence.qualified_no),
        _norm_space(evidence.text),
    )


def _logical_groups(candidates: list[EvidenceClause]) -> list[list[EvidenceClause]]:
    groups: dict[tuple[str, ...], list[EvidenceClause]] = defaultdict(list)
    for evidence in candidates:
        groups[_logical_key(evidence)].append(evidence)
    return list(groups.values())


def _candidate_labels(groups: list[list[EvidenceClause]]) -> tuple[str, ...]:
    """충돌 후보를 입력 순서와 무관한 식별 가능한 문자열로 남긴다."""
    labels: list[str] = []
    for group in groups:
        evidence = group[0]
        identity = (evidence.clause_id or evidence.handle or "").strip()
        if not identity:
            digest = sha256(_norm_space(evidence.text).encode("utf-8")).hexdigest()[:12]
            identity = f"text:{digest}"
        labels.append(f"{evidence.qualified_no} [{identity}]")
    return tuple(sorted(labels))


def _group_has_quotes(group: list[EvidenceClause], quote_list: list[str]) -> bool:
    """quote 전부가 같은 논리 조항의 어느 chunk엔가 실제 존재하는지 본다."""
    if not quote_list or any(not quote.strip() for quote in quote_list):
        return False
    return all(
        any(_norm_space(quote) in _norm_space(evidence.text) for evidence in group)
        for quote in quote_list
    )


def _with_group_quote(
    cited: str,
    group: list[EvidenceClause],
    res: Resolution,
    quote_list: list[str],
) -> CitationCheck:
    """한 논리 조항의 여러 chunk를 합쳐 quote를 검증한다."""
    if not quote_list:
        return CitationCheck(cited, res, group[0])
    if any(not quote.strip() for quote in quote_list):
        return CitationCheck(cited, res, group[0], quote_mismatch="(빈 인용문)")
    if not _group_has_quotes(group, quote_list):
        mismatch = next(
            quote
            for quote in quote_list
            if not any(
                _norm_space(quote) in _norm_space(evidence.text) for evidence in group
            )
        )
        return CitationCheck(cited, res, group[0], quote_mismatch=mismatch[:40])

    # 가능하면 모든 quote를 담은 chunk를 matched로 돌려 디버깅 정보를 선명하게 한다.
    hit = next(
        (
            evidence
            for evidence in group
            if all(_norm_space(q) in _norm_space(evidence.text) for q in quote_list)
        ),
        group[0],
    )
    return CitationCheck(cited, res, hit)


def _check_one(
    cited: str,
    by_handle: dict,
    by_path: dict,
    by_no: dict,
    quotes: dict[str, list[str]],
) -> CitationCheck:
    raw = (cited or "").strip()

    # ① 손잡이 — 가장 확실하다.
    if _HANDLE.match(raw.upper()):
        hit = by_handle.get(raw.upper())
        if hit is None:
            return CitationCheck(raw, Resolution.UNKNOWN)
        return _with_quote(raw, hit, Resolution.EXACT, quotes)

    # ② 전체 경로 — `보통약관/제9조` 또는 `보통약관 제9조`.
    cands = list(by_path.get(_norm_space(raw)) or ())
    if not cands and " " in raw and "/" not in raw:
        cands = list(by_path.get(_norm_space(raw.replace(" ", "/", 1))) or ())
    if cands:
        #: ★같은 경로라도 서로 다른 논리 조항이면 quote 없이 임의 선택하지 않는다.
        #:   과거에는 quote가 없을 때 모든 후보가 mismatch 없는 것으로 보여 첫 후보가
        #:   EXACT로 통과했다. quote가 **정확히 한 논리 후보**를 고를 때만 해소한다.
        groups = _logical_groups(cands)
        quote_list = quotes.get(raw, [])
        if len(groups) == 1:
            return _with_group_quote(raw, groups[0], Resolution.EXACT, quote_list)

        labels = _candidate_labels(groups)
        if not quote_list:
            return CitationCheck(raw, Resolution.AMBIGUOUS, candidates=labels)

        matching = [group for group in groups if _group_has_quotes(group, quote_list)]
        if len(matching) == 1:
            return _with_group_quote(raw, matching[0], Resolution.EXACT, quote_list)

        mismatch = ""
        if not matching:
            mismatch = (
                "(빈 인용문)"
                if any(not quote.strip() for quote in quote_list)
                else quote_list[0][:40]
            )
        return CitationCheck(
            raw,
            Resolution.AMBIGUOUS,
            candidates=labels,
            quote_mismatch=mismatch,
        )

    section, tail = _split_path(raw)

    #: ★먼저 **조항 표기로 읽히는지**부터 본다.
    #:   순서를 반대로 했더니 `"우울증은 보장됩니다"` 가 공백 때문에
    #:   "부 이름이 붙은 경로"로 오인돼 `unknown` 이 됐다.
    #:   조항이 아닌 문장은 `invalid` 여야 한다 — 원인이 다르면 사유도 달라야 한다.
    ref = _parse_clause_ref(tail)
    if ref is None:
        #: 공백 구분 경로(`보통약관 제9조`)일 수 있다. 마지막 낱말이 조항이면 경로다.
        last = raw.rsplit(" ", 1)[-1] if " " in raw else ""
        if last and _parse_clause_ref(last):
            #: 경로로 읽었는데 ①②에서 못 맞았다 = 틀린 경로다.
            return CitationCheck(raw, Resolution.UNKNOWN)
        return CitationCheck(raw, Resolution.INVALID)

    #: ★부 이름을 썼는데 안 맞으면 **번호로 강등하지 않는다.**
    #:   부까지 썼는데 틀렸다는 것은 다른 조항을 가리킨 것이다(코덱스 지적).
    if section:
        return CitationCheck(raw, Resolution.UNKNOWN)

    # ③ 번호만 왔다 — 후보가 **정확히 1개일 때만** 해소한다.
    cands = by_no.get(ref, [])
    if not cands:
        return CitationCheck(raw, Resolution.UNKNOWN)
    #: ★같은 조항이 여러 청크로 쪼개진 것은 후보 1개로 접는다.
    #:   경로가 같아도 clause_id/본문이 다른 논리 조항은 **따로 센다.**
    groups = _logical_groups(cands)
    if len(groups) > 1:
        return CitationCheck(
            raw,
            Resolution.AMBIGUOUS,
            candidates=_candidate_labels(groups),
        )
    return _with_group_quote(raw, groups[0], Resolution.RESOLVED, quotes.get(raw, []))


def _quote_occurrences(answer_text: str, quote: str) -> list[tuple[int, int]]:
    """공백 차이를 흡수해 quote의 원문 좌표 span을 모두 돌려준다."""
    normalized: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(answer_text or ""):
        if char.isspace():
            continue
        normalized.append(char)
        offsets.append(index)

    needle = _norm_space(quote)
    if not needle:
        return []
    haystack = "".join(normalized)
    occurrences: list[tuple[int, int]] = []
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found < 0:
            break
        occurrences.append((offsets[found], offsets[found + len(needle) - 1] + 1))
        start = found + 1
    return occurrences


def _validated_quote_spans(
    *,
    answer_text: str,
    cited_clauses: list[str],
    checks: list[CitationCheck],
    quotes: dict[str, list[str]],
) -> list[tuple[int, int]]:
    """근거 대조를 통과했고 답변에도 실제 존재하는 quote span만 고른다.

    같은 문장이 답변에 여러 번 나와도 quote payload 한 개는 span 한 개만 면제한다.
    따라서 동일 문장을 quote 바깥에 복제해 미선언 조항 검사를 우회할 수 없다.
    """
    spans: list[tuple[int, int]] = []
    used: set[tuple[int, int]] = set()
    for cited, check in zip(cited_clauses, checks):
        if check.matched is None or check.quote_mismatch:
            continue
        if check.resolution not in (Resolution.EXACT, Resolution.RESOLVED):
            continue
        for quote in quotes.get(cited, []):
            occurrence = next(
                (span for span in _quote_occurrences(answer_text, quote) if span not in used),
                None,
            )
            if occurrence is not None:
                spans.append(occurrence)
                used.add(occurrence)
    return spans


def verify(
    *,
    cited_clauses: list[str],
    evidence: list[EvidenceClause],
    answer_text: str = "",
    quotes: dict[str, list[str]] | dict[str, str] | None = None,
    require_citation: bool = True,
) -> GuardResult:
    """인용을 검증한다.

    Args:
        cited_clauses: LLM 이 인용했다고 선언한 것. 손잡이(`E001`)·전체 경로·번호.
        evidence: 실제로 넘겨준 근거 조항들.
        answer_text: 답변 본문. 선언하지 않고 본문에서만 말한 조항을 잡는다.
        quotes: `{인용 → 인용문}` 또는 `{인용 → [인용문…]}`.
            ★값이 문자열 하나면 같은 조항의 복수 인용이 덮인다. 리스트를 권장한다.
        require_citation: 인용이 하나도 없으면 실패로 볼 것인가.
            ★기본이 `True` 다 — v1 은 빈 인용도 통과시켰다.

    Returns:
        `GuardResult`. `ok=False` 면 **그 답을 쓰면 안 된다.**
    """
    qmap: dict[str, list[str]] = {}
    for k, v in (quotes or {}).items():
        qmap[k] = [v] if isinstance(v, str) else list(v)

    by_handle, by_path, by_no = _index(evidence)
    checks = [_check_one(c, by_handle, by_path, by_no, qmap) for c in cited_clauses]

    #: 본문에서 말했는데 선언하지 않은 조항.
    #:
    #: ★v1 은 이걸 경고로만 뒀다. 그러면 `cited_clauses=[]` 로 두고 본문에만
    #:   "제9조에 따르면" 이라 써서 검사를 통째로 우회할 수 있다(코덱스 지적).
    #:   다만 **해소된 근거의 번호**는 뺀다 — 그건 우리가 준 조항 자신이다.
    declared: set[str] = set()
    for ch, c in zip(checks, cited_clauses):
        ref = _parse_clause_ref(_split_path(c)[1])
        if ref:
            declared.add(ref[1])
        if ch.matched:
            r2 = _parse_clause_ref(_split_path(ch.matched.qualified_no)[1])
            if r2:
                declared.add(r2[1])
    #: ★근거 원문 대조를 통과하고 답변에도 실제 존재하는 quote span 안의 번호만
    #:   준용 조항으로 면제한다. evidence 어디엔가 등장한다는 이유로 넓게 허용하지 않는다.
    quote_spans = _validated_quote_spans(
        answer_text=answer_text,
        cited_clauses=cited_clauses,
        checks=checks,
        quotes=qmap,
    )
    mentioned: set[str] = set()
    for mention in _MENTION.finditer(answer_text or ""):
        if any(
            span_start <= mention.start() and mention.end() <= span_end
            for span_start, span_end in quote_spans
        ):
            continue
        normalized = f"제{int(mention.group(1))}조" + (
            f"의{int(mention.group(2))}" if mention.group(2) else ""
        )
        mentioned.add(normalized)
    undeclared = sorted(mentioned - declared)

    no_citations = require_citation and not cited_clauses
    #: ★fail-closed. 하나라도 걸리면 그 답은 쓰지 않는다.
    ok = (
        not no_citations
        and not any(
            ch.resolution
            in (Resolution.UNKNOWN, Resolution.AMBIGUOUS, Resolution.INVALID)
            for ch in checks
        )
        and not any(ch.quote_mismatch for ch in checks)
        and not undeclared
    )
    return GuardResult(
        ok=ok,
        checks=checks,
        undeclared_mentions=undeclared,
        no_citations=no_citations,
    )


def make_handles(evidence: list[EvidenceClause]) -> list[EvidenceClause]:
    """근거에 `E001`… 손잡이를 붙인다. LLM 에게 줄 때 쓴다.

    ★요청 단위다. 다음 요청에서 `E001` 이 다른 조항을 가리켜도 된다.
      영속 식별자가 아니다 — 그건 저장소가 따로 갖는다.
    """
    return [
        EvidenceClause(
            qualified_no=e.qualified_no,
            text=e.text,
            handle=f"E{i:03d}",
            clause_id=e.clause_id,
        )
        for i, e in enumerate(evidence, 1)
    ]
