"""코호트 통계 — 표본이 적을 때 무엇을 말하고 무엇을 말하지 않는가.

★이 테스트가 지키는 것은 정확도가 아니라 **정직성**이다.
  "82.5%"라고 단정하는 순간 사용자는 그것을 실제 승인율로 받아들인다.
"""

import pytest

from app.core.domain.insurance import CohortStats, DataSource, KcdCode
from app.core.errors import ValidationErr
from app.core.usecases.cohort import CohortAnswer, CohortQuery


def _stats(n, approved, *, source=DataSource.SYNTHETIC, min_sample=30, warnings=()):
    return CohortStats(
        n=n,
        approved_n=approved,
        denied_n=n - approved,
        data_source=source,
        min_sample=min_sample,
        warnings=tuple(warnings),
    )


class _FakePort:
    """포트만 흉내낸다. DB·HTTP 없이 유스케이스를 검증한다."""

    def __init__(self, stats):
        self._s = stats
        self.calls = []

    def fetch(self, *, kcd_code, product_id, age_band, data_source):
        self.calls.append((kcd_code, product_id, age_band, data_source))
        return self._s


_KCD = KcdCode(version_label="제8차", code="F32", name_ko="우울에피소드")


def _run(stats, *, source=DataSource.SYNTHETIC, min_sample=30) -> CohortAnswer:
    q = CohortQuery(_FakePort(stats), min_sample=min_sample)
    return q.run(kcd_code=_KCD, product_id="p1", age_band=None, data_source=source)


# ── 신뢰구간 ────────────────────────────────────────────────────────────

def test_팀이_쓰지_말자고_한_문자열을_만들지_않는다():
    #: ★팀MVP6 §2-2 — "'82.5%'라고 단정하면 거짓이다"
    a = _run(_stats(40, 33))
    assert "82.5%" not in a.headline
    assert "82%" not in a.headline
    #: 대신 구간으로 말한다
    assert "95% 신뢰구간" in a.headline
    assert "68%~91%" in a.headline


def test_개인의_확률로_읽히지_않게_주어를_붙인다():
    #: ★"승인 비율 68~91%" 라고만 쓰면 "내가 그 확률로 받는다"로 읽힌다.
    #:   신뢰구간은 모수에 대한 진술이지 개인에 대한 것이 아니다.
    #:   MVP §4 비범위 — "개인 예상 지급액 제시(손해사정 유사행위 소지)"
    a = _run(_stats(40, 33))
    assert "과거 사례" in a.headline
    assert "이 사례들의 승인 비율" in a.headline
    assert "본인의 결과를 예측하지 않습니다" in a.headline


def test_표본_미달일_때도_과거_사례라고_말한다():
    a = _run(_stats(29, 24))
    assert "과거 사례" in a.headline


def test_40건_33건의_95퍼센트_구간은_대략_68에서_91():
    #: 팀 문서가 말한 "±12%p 수준"과 같은 자릿수인지 확인한다.
    #: 실측: Wilson (0.6805, 0.9125) — 관측값 0.825 기준 -14.5%p / +8.8%p.
    #: ★비대칭이다. "±12%p"라는 대칭 표현 자체가 정규근사의 흔적이다.
    lo, hi = _stats(40, 33).rate_interval()
    assert 0.67 < lo < 0.69
    assert 0.90 < hi < 0.92
    #: 관측값은 구간 안에 있다
    assert lo < 33 / 40 < hi


def test_표본이_클수록_구간이_좁아진다():
    narrow = _stats(400, 330).rate_interval()
    wide = _stats(40, 33).rate_interval()
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_구간은_0과_1을_벗어나지_않는다():
    #: ★정규근사였다면 p=1 에서 상한이 1 을 넘는다. Wilson 은 안 넘는다.
    lo, hi = _stats(30, 30).rate_interval()
    assert 0.0 <= lo <= 1.0 and 0.0 <= hi <= 1.0
    #: p=1 이면 상한은 수학적으로 정확히 1 이다(부동소수 오차 범위 내).
    assert hi == pytest.approx(1.0)
    assert f"{hi:.0%}" == "100%"
    lo0, hi0 = _stats(30, 0).rate_interval()
    assert lo0 == pytest.approx(0.0) and 0.0 <= hi0 <= 1.0
    #: ★30건 전부 승인이어도 "100% 보장됩니다"가 아니다 — 하한은 88% 근처다.
    assert 0.85 < lo < 0.90


# ── 최소표본 게이트 ─────────────────────────────────────────────────────

def test_표본_미달이면_비율도_구간도_내지_않는다():
    a = _run(_stats(29, 24))
    assert a.approval_rate is None
    assert a.approval_ci is None
    assert "표본이 적어" in a.headline
    assert "%" not in a.headline.split("표본이 적어")[0]


def test_미달이면_계산_자체를_거부한다():
    s = _stats(29, 24)
    with pytest.raises(ValidationErr):
        s.approval_rate()
    with pytest.raises(ValidationErr):
        s.rate_interval()


def test_0건은_0건_중_0건이라고_쓰지_않는다():
    a = _run(_stats(0, 0))
    assert a.headline == "검증된 사례가 없습니다"


# ── 점추정 단독 노출 차단 ───────────────────────────────────────────────

def test_점추정만_있고_구간이_없는_응답은_만들_수_없다():
    #: ★구조로 강제한다. 누가 나중에 approval_ci 를 빼먹으면 그 자리에서 터진다.
    with pytest.raises(ValidationErr):
        CohortAnswer(stats=_stats(40, 33), approval_rate=0.825, approval_ci=None, headline="x")
    with pytest.raises(ValidationErr):
        CohortAnswer(stats=_stats(40, 33), approval_rate=None, approval_ci=(0.1, 0.9), headline="x")


def test_게이트를_넘으면_둘_다_채워진다():
    a = _run(_stats(40, 33))
    assert a.approval_rate == pytest.approx(0.825)
    assert a.approval_ci is not None and len(a.approval_ci) == 2


# ── 합성/실제 분리와 편향 경고 ──────────────────────────────────────────

def test_구조적_편향_경고는_항상_붙는다():
    a = _run(_stats(40, 33))
    joined = " ".join(a.stats.warnings)
    assert "제보 편향" in joined
    assert "보험사 전체 통계가 아닙니다" in joined


def test_합성이면_합성이라고_먼저_말한다():
    a = _run(_stats(40, 33), source=DataSource.SYNTHETIC)
    assert a.stats.warnings[0].startswith("합성 데이터입니다")
    assert a.data_source is DataSource.SYNTHETIC


def test_요청한_출처와_다른_데이터가_오면_실패시킨다():
    #: ★조용히 쓰지 않는다. 합성이 실제 통계로 새는 경로를 막는다.
    with pytest.raises(ValidationErr):
        _run(_stats(40, 33, source=DataSource.SYNTHETIC), source=DataSource.VERIFIED_REAL)


def test_min_sample은_1이상이어야_한다():
    with pytest.raises(ValidationErr):
        CohortQuery(_FakePort(_stats(40, 33)), min_sample=0)
