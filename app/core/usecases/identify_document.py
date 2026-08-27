"""문서 식별 유스케이스 — 후보를 만들고, 모호하면 격리한다.

★이 유스케이스가 절대 하지 않는 것: **확정.**
자동 분석은 `Candidate` 와 격리 사유만 만든다. `CONFIRMED` 는 사람 검수만 만들 수 있다
(`app/core/domain/document_identification.py` 의 `IdentificationResult.__post_init__`).

격리 기준(Codex 협의 채택) — 하나라도 걸리면 `AMBIGUOUS`:
- 문서종류 근거가 충돌하거나 후보가 복수
- 세대 후보가 0개 또는 2개 이상
- 표지 상품명·상품코드가 카탈로그와 불일치
- 시행일과 판매구간이 양립하지 않음
- 추출 품질 미달(암호화·손상·텍스트 부족)
- 필수 근거(문서종류·상품·시행일) 중 하나가 없음
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.domain.document_identification import (
    Artifact,
    Candidate,
    DocumentKind,
    Evidence,
    EvidenceSource,
    ExtractionQuality,
    IdentificationResult,
    SourceOccurrence,
    VariantKind,
)
from app.core.domain.insurance import IdentificationStatus
from app.core.errors import ValidationErr

#: 문서종류 판정에 쓸 표지·목차·본문 표지어.
_KIND_MARKERS: dict[DocumentKind, tuple[str, ...]] = {
    DocumentKind.POLICY_TERMS: ("보험약관", "보통약관", "특별약관", "약관"),
    DocumentKind.PRODUCT_SUMMARY: ("상품요약서",),
    DocumentKind.BUSINESS_METHOD: ("사업방법서",),
}
#: 표지 변형 표기. 삼성화재 실물 표지에서 확인된 값이다.
_VARIANT_MARKERS: dict[VariantKind, tuple[str, ...]] = {
    VariantKind.CONTRACT_CONVERSION: ("[계약전환용]", "계약전환용"),
    VariantKind.CONVERSION_RESUME: ("[전환·재개용]", "[전환.재개용]", "전환·재개용"),
    VariantKind.CHILD_CONVERSION: ("[자녀보험전환용]", "자녀보험전환용"),
}


@dataclass(frozen=True)
class ExtractedEvidence:
    """바깥(PDF 어댑터)이 뽑아 주는 것. 해석은 여기서 하지 않는다."""

    artifact_sha256: str
    page_count: int
    text_length: int
    cover_text: str
    toc_text: str
    body_sample: str
    quality: ExtractionQuality


def _kinds_in(text: str) -> set[DocumentKind]:
    found: set[DocumentKind] = set()
    for kind, markers in _KIND_MARKERS.items():
        if any(m in text for m in markers):
            found.add(kind)
    # '보험약관'은 '약관'을 포함하므로, 더 구체적인 표지어가 있으면 그쪽을 남긴다.
    if DocumentKind.POLICY_TERMS in found and len(found) > 1:
        specific = {DocumentKind.PRODUCT_SUMMARY, DocumentKind.BUSINESS_METHOD} & found
        if specific and not any(
            m in text for m in ("보험약관", "보통약관", "특별약관")
        ):
            found.discard(DocumentKind.POLICY_TERMS)
    return found


def _variant_in(text: str) -> set[VariantKind]:
    return {k for k, ms in _VARIANT_MARKERS.items() if any(m in text for m in ms)}


class IdentifyDocument:
    """추출 근거를 교차해 후보를 만든다. 확정은 하지 않는다."""

    #: 이보다 텍스트가 적으면 추출 실패로 본다(스캔본·암호화 가능성).
    MIN_TEXT_LENGTH = 2_000

    def run(
        self,
        *,
        artifact: Artifact,
        occurrences: tuple[SourceOccurrence, ...],
        evidence: ExtractedEvidence,
    ) -> IdentificationResult:
        if not occurrences:
            raise ValidationErr("출처 정보가 없는 파일은 식별할 수 없습니다.")
        if artifact.sha256 != evidence.artifact_sha256:
            raise ValidationErr("아티팩트와 추출 근거의 해시가 다릅니다.")

        reasons: list[str] = []
        supporting: list[Evidence] = []
        opposing: list[Evidence] = []

        # ── 품질 ────────────────────────────────────────────────
        if evidence.quality is not ExtractionQuality.OK:
            reasons.append(f"추출 품질 미달({evidence.quality.value})")
        elif evidence.text_length < self.MIN_TEXT_LENGTH:
            reasons.append(f"추출 텍스트 부족({evidence.text_length}자)")

        # ── 문서종류: 표지·목차·본문을 각각 독립으로 본다 ──────────
        by_source = {
            EvidenceSource.COVER: _kinds_in(evidence.cover_text),
            EvidenceSource.TOC: _kinds_in(evidence.toc_text),
            EvidenceSource.BODY: _kinds_in(evidence.body_sample),
        }
        for src, kinds in by_source.items():
            for k in kinds:
                supporting.append(
                    Evidence(
                        source=src,
                        field="document_kind",
                        value=k.value,
                        excerpt=_excerpt_for(src, evidence),
                    )
                )
        voted = [k for k in DocumentKind if sum(k in s for s in by_source.values()) >= 2]
        if len(voted) == 1:
            kind = voted[0]
        elif len(voted) > 1:
            kind = DocumentKind.UNKNOWN
            reasons.append(f"문서종류 후보가 복수입니다: {[k.value for k in voted]}")
        else:
            kind = DocumentKind.UNKNOWN
            reasons.append("문서종류를 지지하는 독립 근거가 2개 미만입니다.")

        # ── 변형 ────────────────────────────────────────────────
        variants = _variant_in(evidence.cover_text)
        if len(variants) == 1:
            variant = next(iter(variants))
            supporting.append(
                Evidence(EvidenceSource.COVER, "variant", variant.value, evidence.cover_text[:120])
            )
        elif len(variants) > 1:
            variant = VariantKind.UNKNOWN
            reasons.append(f"변형 표기가 복수입니다: {[v.value for v in variants]}")
        else:
            variant = VariantKind.STANDARD

        # ── 카탈로그 대조 ───────────────────────────────────────
        names = {o.product_name for o in occurrences}
        if len(names) > 1:
            reasons.append(f"같은 파일이 서로 다른 상품에 연결되어 있습니다: {sorted(names)[:3]}")
        for o in occurrences[:1]:
            supporting.append(
                Evidence(EvidenceSource.CATALOG, "product_name", o.product_name, o.product_code)
            )
            core = _core_name(o.product_name)
            if core and core not in evidence.cover_text:
                opposing.append(
                    Evidence(
                        EvidenceSource.COVER,
                        "product_name",
                        f"표지에서 '{core}' 미확인",
                        evidence.cover_text[:120],
                    )
                )
                reasons.append("표지 상품명이 카탈로그와 일치하지 않습니다.")

        # ★세대는 여기서 정하지 않는다 — 제도 시행일·판매개시일·개정일이 다른 날짜라
        #   판매구간만으로 확정하면 오판이 난다. 세대 프로필이 준비되면 그때 채운다.
        reasons.append("세대 판정 규칙셋이 아직 없습니다(판매구간만으로 확정하지 않음).")

        candidate = Candidate(
            document_kind=kind,
            variant=variant,
            generation=None,
            effective_from=None,
            supporting=tuple(supporting),
            opposing=tuple(opposing),
        )
        status = (
            IdentificationStatus.AMBIGUOUS if reasons else IdentificationStatus.UNIDENTIFIED
        )
        return IdentificationResult(
            artifact_sha256=artifact.sha256,
            status=status,
            candidates=(candidate,),
            quarantine_reasons=tuple(reasons),
        )


def _excerpt_for(src: EvidenceSource, ev: ExtractedEvidence) -> str:
    text = {
        EvidenceSource.COVER: ev.cover_text,
        EvidenceSource.TOC: ev.toc_text,
        EvidenceSource.BODY: ev.body_sample,
    }.get(src, "")
    return text[:120].replace("\n", " ")


def _core_name(product_name: str) -> str:
    """상품명에서 대조에 쓸 핵심어를 뽑는다. 괄호·수식어는 표지 표기가 달라 빼고 본다."""
    head = product_name.split("(")[0]
    for token in ("무배당", "삼성화재", "다이렉트"):
        head = head.replace(token, "")
    return head.strip()
