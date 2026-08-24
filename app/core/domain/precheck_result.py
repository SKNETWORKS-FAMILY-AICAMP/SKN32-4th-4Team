"""사전판정 결과 — 도메인 타입.

이 모듈은 **프레임워크도 바깥 계층도 모른다**(클린아키텍처 2단계 안쪽).
표준 라이브러리만 쓴다.

★왜 DTO 와 따로 두나

    `app/schemas/precheck.py` 는 **pydantic** 모델이다. HTTP 로 나가는 모양이다.
    유스케이스가 그걸 직접 쓰면 **안쪽이 바깥을 참조**하게 된다(ARCH-002 위반).

    실제로 그랬다 — `app/core/usecases/precheck.py` 가 `app.schemas.precheck` 를
    import 하고 있었다. `ARCH-002` 의 금지 목록에 `app.schemas` 가 없어서
    테스트를 빠져나갔다. **테스트의 빈틈이었다.**

    그래서 판정 결과는 여기서 **순수 자료구조**로 정의하고,
    HTTP 로 내보낼 때 라우터가 pydantic 모델로 옮긴다.
    방향이 항상 안쪽 → 바깥이다.

★`Verdict` 는 여기서 정의하지 않는다

    `app/core/domain/insurance.py` 에 이미 있다. 같은 사실을 두 곳에 두면
    반드시 갈라진다(ARCH-003 이 막는다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.core.domain.insurance import Verdict

__all__ = [
    "AppliedPolicyInfo",
    "CitationRef",
    "CodeVerdict",
    "EvidenceTier",
    "PrecheckInput",
    "PrecheckOutcome",
    "ReasonCode",
    "Verdict",
]


class ReasonCode(str, Enum):
    """왜 그렇게 판정했는지. 기권했을 때 특히 중요하다."""

    # 판정에 이른 경우
    EXCLUDED_BY_CLAUSE = "excluded_by_clause"
    EXCEPTION_APPLIES = "exception_applies"
    COVERED_BY_CLAUSE = "covered_by_clause"
    # 판정하지 못한 경우
    #: ★**보유는 하고 있는데 아직 확정되지 않았다.** `insurer_not_supported` 와 다르다.
    #:   확정 0건일 때 "약관을 보유하고 있지 않습니다"라고 답하면 **사실을 잘못 전한다** —
    #:   1,367건을 갖고 있는데 없다고 말하는 것이다(CLAUDE.md §3).
    #:   사용자가 할 일도 다르다: 전자는 기다리면 되고, 후자는 다른 곳을 찾아야 한다.
    DOCUMENTS_NOT_CONFIRMED = "documents_not_confirmed"
    INSURER_NOT_SUPPORTED = "insurer_not_supported"
    PRODUCT_NOT_MATCHED = "product_not_matched"
    NO_VERSION_AT_DATE = "no_version_at_date"
    AMBIGUOUS_PRODUCT = "ambiguous_product"
    AMBIGUOUS_PRODUCT_LINE = "ambiguous_product_line"
    NO_EVIDENCE = "no_evidence"
    DOCUMENT_NOT_RELIABLE = "document_not_reliable"
    INVALID_CODE = "invalid_code"
    #: ★인용 검증 실패 — 근거를 댔지만 그 근거가 우리 것이 아니다.
    CITATION_UNVERIFIED = "citation_unverified"
    #: ★어느 조항인지 특정할 수 없다. 통과시키지도 폐기하지도 않고 기권한다.
    AMBIGUOUS_CITATION = "ambiguous_citation"


class EvidenceTier(str, Enum):
    """근거의 급. ★섞어 쓰지 않는다."""

    POLICY_CLAUSE = "policy_clause"       # 약관 원문 — 유일하게 판정 근거가 된다
    EXTERNAL_REPORT = "external_report"   # 외부 에이전트가 준 사례 — 참고만
    STATISTICS = "statistics"             # 승인율 등 집계 — 참고만


@dataclass(frozen=True)
class PrecheckInput:
    """사전판정 입력. HTTP 요청 DTO 와 별개다."""

    insurer: str
    enrolled_on: str
    kcd_codes: tuple[str, ...]
    product_name: str | None = None
    client_ref: str | None = None


@dataclass(frozen=True)
class CitationRef:
    """인용 조항 하나. **원문을 찾아갈 수 있어야 한다.**"""

    clause_id: str
    qualified_no: str
    section: str = ""
    title: str = ""
    #: 같은 제목·조번호가 여러 특약에 반복될 때 구분하는 실제 담보 범위.
    scope: str = ""
    quote: str = ""
    page_from: int = 0
    page_to: int = 0
    tier: EvidenceTier = EvidenceTier.POLICY_CLAUSE
    #: ★★**수록(occurrence) 식별자.** `{release_id}:{sha256}:{clause|annex}:{ordinal}`
    #:
    #:   `clause_id` 는 `sha12 + qualified_no + hash8` 이라 해시를 자르고
    #:   세대·조항/부록 구분이 없다. 같은 키에 여러 행이 걸리면
    #:   **검증기가 엉뚱한 행과 대조해 통과**시킬 수 있다(코덱스 지적).
    #:
    #:   ★비어 있으면 **검증을 건너뛰지 않고 기권**해야 한다.
    #:     "식별자가 없다"는 "괜찮다"가 아니다.
    occurrence_id: str = ""


@dataclass(frozen=True)
class AppliedPolicyInfo:
    """무엇으로 판정했는가."""

    insurer: str
    product_name: str
    sale_start: str
    sale_end: str = ""
    generation: int | None = None
    generation_label: str = ""
    product_line: str = ""
    sha256: str = ""
    date_confidence: str = "exact"
    generation_confidence: str = ""
    parse_status: str = "ok"


@dataclass(frozen=True)
class CodeVerdict:
    """질병코드 하나에 대한 판정."""

    code: str
    verdict: Verdict
    reason_code: ReasonCode | None = None
    citations: tuple[CitationRef, ...] = ()
    note: str = ""


@dataclass
class PrecheckOutcome:
    """사전판정 결과.

    ★`abstained=True` 여도 오류가 아니다. "확인 불가"는 정상 결과다.
    """

    verdict: Verdict
    abstained: bool = False
    reason_code: ReasonCode | None = None
    message: str = ""

    applied_policy: AppliedPolicyInfo | None = None
    per_code: list[CodeVerdict] = field(default_factory=list)
    citations: list[CitationRef] = field(default_factory=list)
    candidates: list[AppliedPolicyInfo] = field(default_factory=list)

    rule_engine_version: str = ""
    extractor: str = ""
    trace_id: str = ""
    warnings: list[str] = field(default_factory=list)
