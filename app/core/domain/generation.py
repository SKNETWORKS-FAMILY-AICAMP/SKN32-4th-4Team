"""실손 세대 판정 규칙 — 판매구간으로 세대를 '부여'하지 않는다.

★왜 `saleStDt` 만으로 자르면 안 되나

    제도 시행일 · 상품 판매개시일 · 약관 개정일 · 실제 계약 적용일은 **서로 다른 날짜**다.
    예를 들어 2021-07-01에 4세대 제도가 시행돼도, 어떤 상품은 그 전에 판매를 시작해
    개정 약관으로 갈아탔을 수 있고, 어떤 상품은 한참 뒤에 나왔을 수 있다.

    그래서 판매구간은 **후보를 제거하는 보조 제약**일 뿐이고,
    세대를 확정하려면 표지의 개정일·명시 라벨 같은 **문서 자체의 근거**가 필요하다.

★상품 유형과 문서 변형은 **다른 축**이다

    - `ProductType`  일반 / 노후 / 유병력자 / 여행  → **상품 라인**. 세대 축이 서로 다르다.
    - `VariantKind`  계약전환용 / 전환·재개용 / 자녀보험전환용 → **같은 상품의 문서 변형**

    한 enum에 섞으면 "유병력자 계약전환용"을 표현할 수 없다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path

from app.core.errors import ConfigError, ValidationErr


class ProductType(str, Enum):
    """실손 상품 라인. 세대 구분 축이 라인마다 다르다."""

    STANDARD = "standard"                # 일반 실손
    SENIOR = "senior"                    # 노후실손 (2014-08~)
    SIMPLIFIED_ISSUE = "simplified_issue"  # 유병력자 실손 (2018-04-02~)
    TRAVEL = "travel"                    # 여행 실손 — ★세대 구분 대상이 아니다
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GenerationProfile:
    generation: int
    label: str
    applies_to: tuple[ProductType, ...]
    effective_from: date | None
    effective_to: date | None
    sources: tuple[str, ...]

    def covers(self, when: date) -> bool:
        if self.effective_from and when < self.effective_from:
            return False
        if self.effective_to and when > self.effective_to:
            return False
        return True

    def overlaps_sale_period(self, sale_start: date | None, sale_end: date | None) -> bool:
        """판매구간과 겹치는가. ★후보를 **제거**하는 데만 쓴다."""
        if sale_start is None:
            return True
        if self.effective_to and sale_start > self.effective_to:
            return False
        if self.effective_from and sale_end and sale_end < self.effective_from:
            return False
        return True


@dataclass(frozen=True)
class ProductTypeRule:
    product_type: ProductType
    label: str
    name_markers: tuple[str, ...]
    exclude_markers: tuple[str, ...]
    launched_on: date | None

    def matches(self, product_name: str) -> bool:
        if any(x in product_name for x in self.exclude_markers):
            return False
        return any(m in product_name for m in self.name_markers)


@dataclass(frozen=True)
class GenerationRuleSet:
    """버전이 붙은 규칙셋. **출처 없이 판정하지 않는다.**"""

    schema_version: str
    compiled_at: str
    review_status: str
    profiles: tuple[GenerationProfile, ...]
    product_rules: tuple[ProductTypeRule, ...]
    #: 부분별 검토 상태. `{부분: {"status":…, "evidence_basis":…, "source_refs":[…]}}`
    review: dict = field(default_factory=dict)

    @property
    def is_reviewed(self) -> bool:
        """**전부** 검토됐는가. 미검토 규칙셋으로는 확정할 수 없다.

        ★`partial` 은 여기서 `False` 다. 부분만 검토된 것을
          "검토됨"으로 반올림하면 미검토 규칙이 확정 경로로 샌다.
          부분별 판단이 필요하면 `reviewed_for()` 를 쓴다.
        """
        return self.review_status == "reviewed"

    def reviewed_for(self, part: str) -> bool:
        """**부분별** 검토 여부.

        ★근거 등급이 필드마다 다르다(코덱스 권고 2026-08-03) —
          세대 경계는 금융위 보도자료라는 1차 출처가 있지만,
          `product_types`(노후·유병력자) marker 규칙은 아직 2차 자료다.
          하나로 뭉뚱그리면 둘 중 하나가 사실과 다르게 표시된다.

        `part` — `generation_boundaries` · `generation_end_dates` · `product_types`
        """
        if self.review_status == "reviewed":
            return True
        return (self.review or {}).get(part, {}).get("status") == "reviewed"

    def classify_product(self, product_name: str) -> tuple[ProductType, list[str]]:
        """상품명으로 라인을 정한다. 복수로 걸리면 정하지 않고 사유를 돌려준다."""
        hits = [r for r in self.product_rules if r.matches(product_name)]
        if len(hits) == 1:
            return hits[0].product_type, []
        if not hits:
            return ProductType.UNKNOWN, [f"상품 라인을 판별하지 못했습니다: {product_name!r}"]
        return ProductType.UNKNOWN, [
            f"상품 라인 후보가 복수입니다: {[h.product_type.value for h in hits]}"
        ]

    def generation_candidates(
        self,
        *,
        product_type: ProductType,
        revision_date: date | None,
        sale_start: date | None,
        sale_end: date | None,
    ) -> tuple[list[GenerationProfile], list[str]]:
        """세대 후보와 격리 사유를 만든다.

        ★`revision_date`(문서 개정일)가 없으면 후보를 **좁히지 않는다.**
        판매구간만으로 세대를 정하는 것이 정확히 우리가 막으려는 실수다.
        """
        reasons: list[str] = []
        if product_type is ProductType.TRAVEL:
            return [], ["여행 실손은 세대 구분 대상이 아닙니다."]
        if product_type is ProductType.UNKNOWN:
            return [], ["상품 라인이 확정되지 않아 세대를 판정할 수 없습니다."]

        applicable = [p for p in self.profiles if product_type in p.applies_to]
        if not applicable:
            return [], [
                f"'{product_type.value}' 라인의 세대 프로필이 규칙셋에 없습니다."
            ]

        # 판매구간은 후보 '제거'에만 쓴다.
        narrowed = [p for p in applicable if p.overlaps_sale_period(sale_start, sale_end)]
        if not narrowed:
            return [], ["판매구간과 양립하는 세대 프로필이 없습니다."]

        if revision_date is None:
            reasons.append(
                "문서에서 개정일·시행일을 찾지 못해 세대를 좁히지 않았습니다"
                f"(판매구간 기준 후보 {len(narrowed)}개)."
            )
            return narrowed, reasons

        by_doc = [p for p in narrowed if p.covers(revision_date)]
        if len(by_doc) != 1:
            reasons.append(
                f"문서 개정일({revision_date})로 좁힌 세대 후보가 {len(by_doc)}개입니다."
            )
        return by_doc or narrowed, reasons


def load_ruleset(path: Path) -> GenerationRuleSet:
    """규칙셋을 읽는다. 없거나 깨졌으면 **폴백하지 않고** `ConfigError`."""
    if not path.exists():
        raise ConfigError(f"세대 규칙셋이 없습니다: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"세대 규칙셋을 읽을 수 없습니다: {e}") from e

    def _d(v: str | None) -> date | None:
        return date.fromisoformat(v) if v else None

    try:
        profiles = tuple(
            GenerationProfile(
                generation=g["generation"],
                label=g["label"],
                applies_to=tuple(ProductType(t) for t in g["applies_to"]),
                effective_from=_d(g.get("effective_from")),
                effective_to=_d(g.get("effective_to")),
                sources=tuple(g.get("sources", ())),
            )
            for g in raw["generations"]
        )
        rules = tuple(
            ProductTypeRule(
                product_type=ProductType(key),
                label=val["label"],
                name_markers=tuple(val.get("name_markers", ())),
                exclude_markers=tuple(val.get("exclude_markers", ())),
                launched_on=_d(val.get("launched_on")),
            )
            for key, val in raw["product_types"].items()
        )
    except (KeyError, ValueError) as e:
        raise ConfigError(f"세대 규칙셋 구조가 올바르지 않습니다: {e}") from e

    missing = [p.label for p in profiles if not p.sources]
    if missing:
        raise ValidationErr(f"출처 없는 세대 프로필이 있습니다: {missing}")

    return GenerationRuleSet(
        schema_version=raw.get("schema_version", ""),
        compiled_at=raw.get("compiled_at", ""),
        review_status=raw.get("review_status", "unreviewed"),
        review=raw.get("review", {}),
        profiles=profiles,
        product_rules=rules,
    )
