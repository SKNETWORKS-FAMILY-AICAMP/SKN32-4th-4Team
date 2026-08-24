"""근거로 쓸 수 있는 조항인가 — **규칙은 여기 하나뿐이다.**

★왜 이게 필요했나

    같은 질문을 파일 저장소·적재·PG·검색·인용 검증이 **각자 다르게** 답하고 있었다
    (코덱스 실측 지적 2026-08-03):

        적재      `citation_eligible is False` 만 제외 → **필드가 없으면 통과**
        파일      조항별 `citation_eligible` 을 **아예 안 봄**
        PG        조회 결과를 **무조건** `usable=True`, 통계를 무조건 `parse_status="ok"`
        인용검증  `usable_only=False` 로 읽고 `row.usable` 을 **확인 안 함**
        적재      `statute=True`(법령 조문)도 안 걸러짐

    다섯 곳이 다르면 어느 것이 맞는지 아무도 모른다. 그리고 대부분이
    **모르면 통과(fail-open)** 였다 — 필드가 없다는 것은 "괜찮다"가 아니라 "모른다"이다.

★규칙

    1. 문서가 파싱됐나          `parse_status == "ok"`
    2. 인용해도 되나            `citation_eligible is True`  ★`is not False` 가 아니다
    3. 조항 형태인가            `chunk_type` 이 페이지 덩어리가 아니다
    4. 약관 조항인가            `is_statute` 가 아니다 — 법령 조문은 약관이 아니다
    5. 인용에 필요한 게 있나    번호·본문이 비어 있지 않다

★**모르면 못 쓴다.** 필드가 없으면 거절한다(CLAUDE.md §0).
  "확인 안 된 약관으로 보장 여부를 답하지 않는다"와 같은 원칙이다.
"""

from __future__ import annotations

from dataclasses import dataclass

#: 조항이 아니라 페이지 덩어리인 청크. "제N조"를 댈 수 없다.
NON_CLAUSE_CHUNKS = frozenset({"page_fallback", "page", "unknown"})

#: 규칙이 바뀌면 이 값을 올린다. 승인 manifest 에 박아 **어느 규칙으로 만든 산출물인지** 남긴다.
RULES_VERSION = "elig-1"


@dataclass(frozen=True)
class EligibilityResult:
    """쓸 수 있나. **왜 안 되는지 못 대면 다음 사람이 되풀이한다.**

    ★이름을 `Verdict` 로 지었다가 `test_arch_003` 에 걸렸다 —
      `app/core/domain/insurance.Verdict` 가 이미 있고 **뜻이 완전히 다르다**
      (그쪽은 보장 판정 4단: `likely_covered`·`unlikely`·…).
      같은 이름이 두 뜻으로 쓰이면 읽는 사람이 어느 쪽인지 매번 확인해야 한다.
    """

    usable: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.usable


def check(clause: dict, *, parse_status: str | None) -> EligibilityResult:
    """조항 하나를 판정한다. `clause` 는 조항 JSON 의 dict 그대로.

    ★`parse_status` 를 **명시로 받는다.** 조항 dict 안에 없기 때문이다 —
      기본값을 `"ok"` 로 두면 안 준 곳이 조용히 통과한다.
    """
    if parse_status is None:
        return EligibilityResult(False, "문서 파싱 상태를 모른다")
    if parse_status != "ok":
        return EligibilityResult(False, f"문서 파싱 상태가 '{parse_status}'")

    #: ★`is True` 다. `is not False` 로 쓰면 **필드가 없을 때 통과**한다.
    if clause.get("citation_eligible") is not True:
        v = clause.get("citation_eligible")
        return EligibilityResult(False, "인용 부적격" if v is False else "인용 가능 여부를 모른다")

    #: ★`chunk_type` 은 **없는 것이 정상**이다. 추출기가 fallback 경로에서만
    #:   `"page_fallback"` 을 붙인다(`to_clauses.py:1320`). 그래서 부재를
    #:   "모른다"로 보지 않는다 — 그렇게 했더니 전 조항이 거절됐다.
    ct = clause.get("chunk_type")
    if ct in NON_CLAUSE_CHUNKS:
        return EligibilityResult(False, f"조항이 아니라 '{ct}' 청크")

    #: ★★**키 이름이 `statute` 다.** 처음에 `is_statute` 로 읽어
    #:   211,131건 전부 부재로 나왔다(코덱스가 실측으로 잡았다).
    #:   있지도 않은 키를 보면서 "막았다"고 보고할 뻔했다.
    #:   `is_statute` 는 별칭으로만 남긴다.
    st = clause.get("statute", clause.get("is_statute"))
    #: ★★**`is False` 로 본다.** `if st:` 로 쓰면 `0`·`""`·`[]` 가 통과한다(코덱스 지적).
    #:   그건 "법령이 아니다"가 아니라 **값이 이상하다**는 뜻이다 — 모르면 못 쓴다.
    if st is not False:
        if st is True:
            #: 법령 조문은 약관 조항이 아니다. 그대로 인용하면
            #: "단체취급 특별약관 제651조(고지의무위반…)" 같은 근거가 나간다.
            return EligibilityResult(False, "약관 조항이 아니라 인용 법령")
        return EligibilityResult(False, f"`statute` 값이 참/거짓이 아니다: {st!r}")

    if not (clause.get("qualified_no") or "").strip():
        return EligibilityResult(False, "조 번호가 비어 있다 — 인용을 특정할 수 없다")
    if not (clause.get("text") or "").strip():
        return EligibilityResult(False, "본문이 비어 있다")

    return EligibilityResult(True)


def check_row(row, *, parse_status: str | None = None) -> EligibilityResult:
    """`ClauseRow`(포트 모델)용. 저장소가 이미 만든 행을 다시 검사한다.

    ★이중 검사가 낭비처럼 보이지만 그렇지 않다 — 저장소마다 채우는 필드가 다르고,
      실제로 PG 어댑터는 **무조건** `usable=True` 로 만들고 있었다.
    """
    return check(
        {
            "citation_eligible": getattr(row, "citation_eligible", None),
            "chunk_type": getattr(row, "chunk_type", None),
            "statute": getattr(row, "is_statute", None),
            "qualified_no": getattr(row, "qualified_no", ""),
            "text": getattr(row, "text", ""),
        },
        parse_status=parse_status if parse_status is not None
        else getattr(row, "parse_status", None),
    )


__all__ = ["EligibilityResult", "check", "check_row", "NON_CLAUSE_CHUNKS", "RULES_VERSION"]
