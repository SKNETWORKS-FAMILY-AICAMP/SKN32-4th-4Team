"""청구 승인율 조회 API.

★합성과 실제를 **엔드포인트로 나눈다**

    `/v1/cohorts`       실제 검증 데이터. 지금 **n=0** 이다
    `/v1/demo/cohorts`  합성 데이터. 시연용

    한 엔드포인트에 `?synthetic=true` 같은 스위치를 두지 않는다.
    스위치를 빠뜨리면 섞이고, 섞이면 사용자는 구분하지 못한다.

★지금 실제 데이터는 0건이다 — 그걸 숨기지 않는다

    "검증된 사례가 없습니다" 가 정직한 답이다.
    없는 통계를 만들어 내느니 없다고 말한다.

★비율을 단정하지 않는다

    표본이 적으면 비율을 **아예 계산하지 않고**, 충분해도
    점추정이 아니라 **신뢰구간**으로 말한다. 그리고 주어를 붙인다 —
    "이 사례들의 승인 비율" 이지 "당신이 받을 확률" 이 아니다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.core.domain.insurance import DataSource, KcdCode
from app.core.errors import ValidationErr
from app.core.usecases.cohort import CohortQuery

router = APIRouter(prefix="/v1", tags=["cohort"])


def _query() -> CohortQuery:
    from app.composition import build_cohort

    return build_cohort()


def _run(source: DataSource, code: str, product_id: str, age_band: str | None) -> dict:
    if not code.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="질병기호(code)가 필요합니다.",
        )
    try:
        ans = _query().run(
            #: ★`version_label` 을 지어내지 않는다. 아직 KCD 마스터가 없으므로
            #:   클라이언트가 준 코드를 그대로 들고 다니고, 차수는 비워 둔다.
            kcd_code=KcdCode(version_label="", code=code.strip().upper(), name_ko=""),
            product_id=product_id.strip(),
            age_band=(age_band or "").strip() or None,
            data_source=source,
        )
    except ValidationErr as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    return {
        "schema_version": "v1",
        "data_source": ans.data_source.value,
        "n": ans.stats.n,
        "approved_n": ans.stats.approved_n,
        "denied_n": ans.stats.denied_n,
        "min_sample": ans.stats.min_sample,
        "min_sample_met": ans.stats.min_sample_met,
        #: ★비율과 구간은 **항상 짝으로** 나간다. 점추정만 보여주지 않는다.
        "approval_rate": ans.approval_rate,
        "approval_ci": list(ans.approval_ci) if ans.approval_ci else None,
        "headline": ans.headline,
        #: ★★등급별 내역을 **항상** 싣는다.
        #:   `admin_attested` 는 "관리자가 보고 납득했다"이지 발행처 확인이 아니다.
        #:   n 만 보면 그 차이를 알 방법이 없다.
        "by_verification": dict(ans.stats.by_verification),
        "warnings": list(ans.stats.warnings) + _attestation_note(ans.stats),
    }


def _attestation_note(stats) -> list[str]:
    """관리자 교차검증분이 섞여 있으면 **경고로 말한다.**"""
    n = dict(stats.by_verification).get("admin_attested", 0)
    if not n:
        return []
    return [
        f"이 표본 중 {n}건은 관리자 교차검증(admin_attested)으로 반영됐습니다 — "
        "보험사·발행처에 조회해 확인한 것이 아닙니다."
    ]


@router.get("/cohorts")
def cohorts(
    code: str = Query(description="질병기호. 예: F32"),
    product_id: str = Query(default="", description="상품 식별자(선택)"),
    age_band: str | None = Query(default=None, description="연령대(선택). 예: 30-39"),
) -> dict:
    """**검증된 실제** 청구 결과 집계.

    ★지금 n=0 이다. 검증 절차가 아직 없어 `verified` 증빙이 하나도 없다.
      비어 있다고 말하는 것이 정답이다.
    """
    return _run(DataSource.VERIFIED_REAL, code, product_id, age_band)


@router.get("/demo/cohorts")
def demo_cohorts(
    code: str = Query(description="질병기호. 예: F32"),
    product_id: str = Query(default=""),
    age_band: str | None = Query(default=None),
) -> dict:
    """**합성** 데이터 집계. 시연용.

    ★응답의 `data_source` 와 `warnings` 에 합성임이 반드시 표시된다.
      실제 통계와 섞이지 않도록 저장소도 폴더가 다르다.
    """
    return _run(DataSource.SYNTHETIC, code, product_id, age_band)
