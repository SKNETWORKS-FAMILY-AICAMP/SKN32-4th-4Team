"""코호트 통계 유스케이스 — 팀 MVP 기능 2·3.

기능 2: "이전 지급받은 고객의 데이터로 청구승인율을 보여준다" (33건/40건)
기능 3: "네 케이스가 다른 곳에서는 이랬다" — 연령대 등으로 묶은 유사 케이스

★이 유스케이스가 막아야 하는 것

1. **표본이 적은데 비율을 단정하는 것.** 40건의 33건은 82.5%로 보이지만
   95% 신뢰구간이 ±12%p 수준이다. 방어를 두 겹으로 둔다 —
   최소표본 미달이면 **비율 자체를 내지 않고**, 게이트를 넘겨도
   **점추정 대신 구간**으로 말한다(`"82.5%"` 가 아니라 `"68~91%"`).
   ★게이트만으로는 부족하다. 넘는 순간 다시 단정하게 되기 때문이다.
2. **생존 편향을 숨기는 것.** 지급받은 사람이 부지급 당한 사람보다 훨씬 많이 제보한다.
   승인율이 실제보다 높게 나오며, 이것은 **사후 보정이 불가능한 구조적 편향**이다.
   보정하는 척하지 않고 경고로 남긴다.
3. **합성 데이터를 실제라고 말하는 것.** ``data_source`` 는 응답에서 절대 생략하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.core.domain.insurance import CohortStats, DataSource, KcdCode
from app.core.errors import ValidationErr
from app.core.ports.insurance import CohortStatsPort

#: 이 표본수 미만이면 비율을 공개하지 않는다.
#: 값 자체는 정책이며, 실제 데이터를 보기 전에는 확정할 수 없다(현재는 잠정치).
DEFAULT_MIN_SAMPLE = 30

#: 우리 표본에 구조적으로 존재하며 보정할 수 없는 편향.
_STRUCTURAL_WARNINGS = (
    "제보 편향: 지급받은 사람이 부지급 사례보다 더 많이 제보하는 경향이 있어 "
    "승인 비율이 실제보다 높게 나타날 수 있습니다.",
    "이 수치는 우리가 수집·검증한 표본 기준이며 보험사 전체 통계가 아닙니다.",
)


@dataclass(frozen=True)
class CohortAnswer:
    """화면·API 어디로 나가든 이 값들이 함께 나간다."""

    stats: CohortStats
    #: ★**우리 표본에서 관측된** 비율. 모집단 추정치가 아니다.
    #: 표본이 충분할 때만 채워지고, 채워지면 ``approval_ci`` 도 반드시 함께 채워진다.
    approval_rate: float | None
    #: ★95% 신뢰구간. ``approval_rate`` 와 **항상 짝으로** 나간다.
    #: 점추정만 보여주면 읽는 쪽은 그것을 '실제 승인율'로 받아들인다.
    approval_ci: tuple[float, float] | None
    #: 사람이 읽는 한 줄. **비율을 단정하지 않는다** — 건수와 구간으로 말한다.
    headline: str

    @property
    def data_source(self) -> DataSource:
        return self.stats.data_source

    def __post_init__(self) -> None:
        #: ★구조로 강제한다 — 점추정만 있고 구간이 없는 응답을 만들 수 없다.
        if (self.approval_rate is None) != (self.approval_ci is None):
            raise ValidationErr(
                "approval_rate 와 approval_ci 는 항상 함께 있거나 함께 없어야 합니다."
            )


class CohortQuery:
    """코호트 통계 조회."""

    def __init__(self, stats_port: CohortStatsPort, *, min_sample: int = DEFAULT_MIN_SAMPLE) -> None:
        if min_sample < 1:
            raise ValidationErr("min_sample 은 1 이상이어야 합니다.")
        self._stats = stats_port
        self._min_sample = min_sample

    def run(
        self,
        *,
        kcd_code: KcdCode,
        product_id: str,
        age_band: str | None,
        data_source: DataSource,
    ) -> CohortAnswer:
        """한 번의 조회는 **하나의 ``data_source`` 만** 본다.

        합성과 실제를 한 응답에서 합치지 않는다. 두 값을 모두 보고 싶으면
        호출을 두 번 하고, 화면에서도 따로 표시한다.
        """
        stats = self._stats.fetch(
            kcd_code=kcd_code,
            product_id=product_id,
            age_band=age_band,
            data_source=data_source,
        )

        if stats.data_source is not data_source:
            # 요청한 출처와 다른 데이터가 돌아오면 조용히 쓰지 않고 실패시킨다.
            raise ValidationErr(
                f"요청한 출처와 반환된 출처가 다릅니다: "
                f"{data_source.value} != {stats.data_source.value}"
            )

        warnings = tuple(stats.warnings) + _STRUCTURAL_WARNINGS
        if data_source is DataSource.SYNTHETIC:
            warnings = ("합성 데이터입니다. 실제 지급 통계가 아닙니다.",) + warnings

        #: ★필드를 하나씩 옮겨 적는 재구성이라 **새 필드를 빠뜨리기 쉽다.**
        #:   실제로 `by_verification` 을 추가했더니 어댑터는 채웠는데 여기서
        #:   흘려서 화면에 `{}` 로 나갔다(2026-08-04). `replace` 로 바꿔
        #:   "바꾸는 것만 적고 나머지는 그대로 간다"를 구조로 만든다.
        enriched = replace(stats, min_sample=self._min_sample, warnings=warnings)

        rate = enriched.approval_rate() if enriched.min_sample_met else None
        ci = enriched.rate_interval() if enriched.min_sample_met else None
        return CohortAnswer(
            stats=enriched,
            approval_rate=rate,
            approval_ci=ci,
            headline=_headline(enriched),
        )


def _headline(stats: CohortStats) -> str:
    """사람이 읽는 한 줄.

    ★**점추정을 단정하지 않는다.**

        고치기 전에는 게이트를 넘으면 ``"… (82.5%)"`` 를 붙였다.
        그런데 그 문자열은 `팀MVP6 §2-2` 가 **쓰지 말자고 정한 바로 그 표현**이다.
        최소표본으로 막아도 게이트를 넘는 순간 같은 문제가 돌아온다.

        그래서 비율 대신 **구간**을 말한다. 40건의 33건은 "82.5%"가 아니라
        **"68~91%"** 이고, 표본이 적다는 사실이 경고가 아니라 숫자로 드러난다.

    ★**주어를 반드시 붙인다 — 이건 개인의 확률이 아니다.**

        "승인 비율 68%~91%" 라고만 쓰면 사용자는 **"내가 68~91% 확률로 받는다"** 로 읽는다.
        신뢰구간은 **모수(과거 사례들의 승인 비율)** 에 대한 진술이지 개인에 대한 것이 아니다.

        개인 확률로 읽히는 순간 `MVP 기능선정 §4` 의 비범위
        (*"개인 예상 지급액 제시 — 손해사정 유사행위 소지. 과거 분포만 표시"*)를 넘는다.
        그래서 문구에 **"과거 사례 중"** 을 박아 둔다.
    """
    if stats.n <= 0:
        #: ★0건을 "0건 중 0건 승인"이라고 쓰지 않는다. 비어 있다고 말한다.
        return "검증된 사례가 없습니다"
    base = f"검증된 과거 사례 {stats.n}건 중 {stats.approved_n}건 승인 · {stats.denied_n}건 부지급"
    if not stats.min_sample_met:
        return f"{base} (표본이 적어 비율은 표시하지 않습니다)"
    lo, hi = stats.rate_interval()
    #: ★"승인 비율"이 아니라 "과거 사례의 승인 비율". 주어가 사용자가 아님을 못박는다.
    return (
        f"{base} (이 사례들의 승인 비율은 {lo:.0%}~{hi:.0%} 범위로 추정됩니다"
        f" · 95% 신뢰구간 · 본인의 결과를 예측하지 않습니다)"
    )
