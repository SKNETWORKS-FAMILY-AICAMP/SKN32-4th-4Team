"""스크레이퍼 전용 메트릭 경로 — **토큰으로만 연다.**

★왜 따로 두는가

    `/api/admin/metrics` 는 관리자 라우터에 있어 **로그인을 요구**한다.
    Prometheus 는 로그인 흐름을 못 탄다. 그렇다고 관리자 라우터를 무인증으로
    열면 운영 지표 전체가 노출된다.

    그래서 **읽기 전용 · 메트릭 전용 토큰**을 따로 둔다. 이 토큰이 새도
    할 수 있는 것은 메트릭 조회뿐이고, 관리자 계정과는 아무 관계가 없다.

★★**토큰이 비어 있으면 이 경로는 존재하지 않는다.** 「설정 안 했으니 다 열어 준다」는
  가장 나쁜 기본값이다. 안 켰으면 404 — 무인증으로 새지 않는다(무폴백).

★비교는 `secrets.compare_digest` 로 한다. `==` 로 하면 앞자리부터 달라지는 지점이
  응답 시간에 남아, 토큰을 한 글자씩 맞춰 갈 수 있다(타이밍 공격).

★★★고객 앱(:8080)에는 이 라우터를 싣지 않는다. 운영 앱에만 붙인다.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Header, HTTPException, Response, status

router = APIRouter(tags=["metrics"])

#: 스크레이퍼가 보낼 헤더. `Authorization: Bearer <토큰>` 도 받는다.
_HEADER = "X-Metrics-Token"


def _presented(x_metrics_token: str | None, authorization: str | None) -> str | None:
    if x_metrics_token:
        return x_metrics_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


@router.get("/metrics-scrape")
def metrics_scrape(
    x_metrics_token: str | None = Header(default=None, alias=_HEADER),
    authorization: str | None = Header(default=None),
) -> Response:
    """토큰이 맞으면 메트릭을 낸다.

    Prometheus 설정 예:

        scrape_configs:
          - job_name: insurance-admin
            metrics_path: /metrics-scrape
            static_configs: [{targets: ["127.0.0.1:8081"]}]
            authorization: {type: Bearer, credentials: "<METRICS_SCRAPE_TOKEN>"}
    """
    from app.core.config import get_settings
    from app.obs.metrics import CONTENT_TYPE, render_metrics

    expected = (get_settings().METRICS_SCRAPE_TOKEN or "").strip()
    if not expected:
        #: ★안 켰으면 **없는 경로**다. 「설정 안 했으니 열어 준다」로 흐르지 않는다.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    got = _presented(x_metrics_token, authorization)
    if not got or not secrets.compare_digest(got, expected):
        #: 토큰이 틀렸다는 것 외에 아무 정보도 주지 않는다.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="메트릭 토큰이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Response(content=render_metrics(), media_type=CONTENT_TYPE)
