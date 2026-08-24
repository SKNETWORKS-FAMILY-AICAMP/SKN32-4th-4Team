"""문서 식별 도메인 — "이 PDF가 무엇인가"를 판정한다.

취득 계획서에 적은 대로 **수집보다 식별이 어렵다.** PDF를 받아오는 건 쉬운 부분이고,
어려운 것은 "이 파일이 어느 보험사·어느 상품·몇 세대·시행일 언제의 약관인가"이다.
그걸 모르면 사용자의 계약에 적용되는 약관인지 알 수 없고, 받아둔 의미가 없다.

★이 모듈이 강제하는 것

1. **자동 확정 금지.** 자동 분석은 `Candidate` 만 만든다. `CONFIRMED` 전이는
   검수자 ID와 사유가 없으면 실패한다.
2. **파일명 순번을 문서종류로 믿지 않는다.** `fileN` 은 전송 슬롯 이름이지
   문서종류 계약이 아니다. 표지·목차·본문 **독립 근거를 교차**해야 한다.
3. **판매구간으로 세대를 확정하지 않는다.** 제도 시행일 · 상품 판매개시일 ·
   약관 개정일은 서로 다른 날짜다. 판매구간은 후보를 **제거하는 보조 제약**일 뿐이다.
4. **모호하면 격리한다.** `Ambiguous` 는 장애가 아니라 정상적인 도메인 결과다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from app.core.errors import ValidationErr
from app.core.domain.insurance import IdentificationStatus


class DocumentKind(str, Enum):
    """문서 종류. 삼성화재 공시 표의 열 이름과 대응한다."""

    POLICY_TERMS = "policy_terms"          # 보험약관 ★우리가 필요한 것
    PRODUCT_SUMMARY = "product_summary"    # 상품요약서
    BUSINESS_METHOD = "business_method"    # 사업방법서
    UNKNOWN = "unknown"


class VariantKind(str, Enum):
    """약관 변형.

    ★삼성화재 표지에 실제로 있던 표기다 — `[계약전환용]` `[전환·재개용]` `[자녀보험전환용]`.
    **별도 버전으로 취급한다.** 전환 대상·적용 요건·면책/재개 조건이 다를 수 있어,
    합치면 일반계약에 전환용 조건을 적용하는 오판이 생긴다.
    """

    STANDARD = "standard"
    CONTRACT_CONVERSION = "contract_conversion"
    CONVERSION_RESUME = "conversion_resume"
    CHILD_CONVERSION = "child_conversion"
    UNKNOWN = "unknown"


class EvidenceSource(str, Enum):
    """근거가 문서의 어디서 나왔나. **서로 독립이어야 교차검증이 성립한다.**"""

    COVER = "cover"        # 표지
    TOC = "toc"            # 목차
    BODY = "body"          # 본문
    CATALOG = "catalog"    # 공시 카탈로그 메타데이터
    FILENAME = "filename"  # ★출처 힌트일 뿐, 판정 근거로 쓰지 않는다


class ExtractionQuality(str, Enum):
    OK = "ok"
    LOW_TEXT = "low_text"      # 추출 텍스트가 너무 적다(스캔본 가능성)
    UNREADABLE = "unreadable"  # 암호화·손상


@dataclass(frozen=True)
class Evidence:
    """판정 근거 한 조각. **어디서 나왔는지와 원문 발췌를 항상 함께** 들고 다닌다."""

    source: EvidenceSource
    field: str          # "document_kind" / "generation" / "variant" / "product_name" …
    value: str
    excerpt: str        # 사람이 검수할 때 읽을 짧은 발췌
    page: int | None = None

    @property
    def is_decisive(self) -> bool:
        """판정 근거로 쓸 수 있는가. ★파일명은 근거가 아니다."""
        return self.source is not EvidenceSource.FILENAME


@dataclass(frozen=True)
class Artifact:
    """수집한 파일 그 자체. **정체성은 URL이 아니라 바이트 해시다.**

    URL은 바뀔 수 있고, 다른 URL이 같은 파일을 줄 수 있으며,
    같은 URL의 내용이 시점에 따라 교체될 수도 있다.
    그래서 URL 기준 중복제거는 **중복을 놓치면서 동시에 변경 이력도 덮는다.**
    """

    sha256: str
    bytes: int
    page_count: int | None = None
    quality: ExtractionQuality = ExtractionQuality.OK


@dataclass(frozen=True)
class SourceOccurrence:
    """이 파일이 '어디서 언제' 나왔는가. 같은 Artifact에 여러 개가 달릴 수 있다."""

    artifact_sha256: str
    url: str
    fetched_at: str
    product_code: str
    product_name: str
    sale_start: str
    sale_end: str


@dataclass(frozen=True)
class Candidate:
    """자동 분석이 만든 **후보**. 확정이 아니다."""

    document_kind: DocumentKind
    variant: VariantKind
    generation: int | None
    effective_from: date | None
    supporting: tuple[Evidence, ...] = field(default_factory=tuple)
    opposing: tuple[Evidence, ...] = field(default_factory=tuple)

    @property
    def decisive_sources(self) -> frozenset[EvidenceSource]:
        return frozenset(e.source for e in self.supporting if e.is_decisive)

    @property
    def cross_verified(self) -> bool:
        """서로 다른 독립 근거가 **둘 이상** 같은 결론을 가리키는가.

        표지 한 줄만 맞고 본문이 다르면 교차검증이 아니다.
        """
        return len(self.decisive_sources) >= 2 and not self.opposing


@dataclass(frozen=True)
class IdentificationResult:
    """자동 분석의 결과. ★어떤 경우에도 `CONFIRMED` 를 만들지 않는다."""

    artifact_sha256: str
    status: IdentificationStatus
    candidates: tuple[Candidate, ...]
    quarantine_reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.status is IdentificationStatus.CONFIRMED:
            raise ValidationErr(
                "자동 분석은 CONFIRMED 를 만들 수 없습니다. 확정은 사람 검수를 거칩니다."
            )

    @property
    def needs_review(self) -> bool:
        return True  # 후보가 하나뿐이어도 사람이 본다


@dataclass(frozen=True)
class ReviewDecision:
    """사람의 검수 결정. **누가·언제·왜가 없으면 만들 수 없다.**"""

    artifact_sha256: str
    status: IdentificationStatus
    reviewer_id: str
    reason: str
    decided_at: datetime
    document_kind: DocumentKind | None = None
    variant: VariantKind | None = None
    generation: int | None = None
    effective_from: date | None = None

    def __post_init__(self) -> None:
        if self.status is IdentificationStatus.UNIDENTIFIED:
            raise ValidationErr("검수 결정은 CONFIRMED 또는 AMBIGUOUS 여야 합니다.")
        if not self.reviewer_id.strip():
            raise ValidationErr("검수자 ID가 필요합니다. 누가 확정했는지 남지 않으면 감사가 불가능합니다.")
        if not self.reason.strip():
            raise ValidationErr("검수 사유가 필요합니다.")
        if self.status is IdentificationStatus.CONFIRMED:
            missing = [
                name
                for name, v in (
                    ("document_kind", self.document_kind),
                    ("generation", self.generation),
                    ("effective_from", self.effective_from),
                )
                if v is None
            ]
            if missing:
                raise ValidationErr(
                    f"CONFIRMED 로 확정하려면 다음이 모두 필요합니다: {', '.join(missing)}"
                )
