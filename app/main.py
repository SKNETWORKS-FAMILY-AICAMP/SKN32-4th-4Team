"""FastAPI 진입점 — 고객/운영 분리 지원.

앱 조립: trace 미들웨어 + 예외 핸들러 + 라우터 + 정적 UI. 기동 시 자동 DB/인덱스 설정은
하지 않는다(REQ-OPS-01) — 명시적 `scripts.manage`로 준비하고 `/api/health/ready`로 확인한다.

**고객 웹 ↔ 운영 도구 분리(실제 프로덕션 패턴 축소판)**: 실무에선 고객 사이트와 관리자
대시보드를 별도 서비스/포트/서브도메인으로 나누고, 관리자 쪽은 VPN·사내망 뒤에 둔다. 여기서는
두 보험 앱과 테스트용 합집합 role을 제공한다:
  - `app` / `customer_app`: 공개 보험 API만 노출하고 운영 페이지를 차단한다.
  - `admin_app`           : 공개 보험 API + 보험 운영 API와 관리자 랜딩을 제공한다.
  - `create_app("full")`  : customer + admin 보험 라우트 합집합(테스트·로컬 조립용).

구형 FAISS RAG·지식 바운티·커머스 CS workflow는 이 조립 모듈에서 import하지 않는다.
소스 보관과 격리는 별도 legacy 절차가 담당한다.
운영 스크립트: `run_customer_server.py`(customer 8080), `run_admin_server.py`(admin 8081).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.config_validation import validate_production_persistence
from app.core.errors import ConfigError, register_exception_handlers
from app.obs.trace import TraceMiddleware
from app.routers import (
    admin,
    auth,
    chat,
    cohort,
    demo,
    face,
    health,
    precheck,
    terms,
    voice,
)

#: ★커머스 라우터(products/orders/payments)와 이름만 A2A 인 `a2a` 라우터는
#:   저장소 루트 `legacy/v3_commerce.zip` 으로 옮겼다.
#:   코드는 보존하되 **API 표면에서 뺀다** — 보험 API 문서에 `/api/products` 가
#:   섞이면 쓰는 사람이 혼란스럽다. 되살리려면 여기서 다시 import 하면 된다.

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_APP_ROLES = frozenset({"customer", "admin", "full"})

# 고객 포트에서 서빙하지 않을 운영/개발 정적 페이지·스크립트(고객 웹 노출 금지).
#: ★없어진 파일 이름이 남아 있으면 **차단이 잘 되는 것처럼 보인다.**
#:   `mcp.html`·`orders.html` 은 레거시로 갔는데 목록에 남아 있었다 —
#:   목록만 보면 "막고 있다"로 읽히지만 실은 막을 것이 없었다.
#:   `tests/test_static_ui.py` 가 목록과 실제 파일을 대조한다.
#: ★`rag.html`·`rag.js` 는 2026-08-03 에 `legacy/v6_rag_ui.zip` 으로 격리했다.
#:   커머스 RAG 화면(`/api/rag/qa`·`/api/rag/search`)이라 보험 서비스와 무관하다.
#:   **없는 파일을 차단 목록에 남기면 "막고 있다"로 읽힌다** — 막을 것이 없으므로 뺀다.
#: ★`facebench.html`·`facebench.js` 는 2026-08-04 에 `legacy/v9_facebench.zip` 으로 격리했다.
#:   **없는 파일을 차단 목록에 남기면 "막고 있다"로 읽힌다** — 막을 것이 없으므로 뺀다.
_OPS_STATIC = {
    "admin.html", "admin.js",
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 기동 시 자동 마이그레이션·색인 적재를 하지 않는다(REQ-OPS-01, Phase 2).
    # PostgreSQL 색인은 `python -m scripts.index.build_clause_index`로 명시적 적재한다.
    # 준비 상태는 GET /api/health/ready 로 확인(미준비 시 명시적으로 알림, 무폴백).
    yield


def create_app(role: str = "full") -> FastAPI:
    """허용된 보험 앱 role만 조립한다. 알 수 없는 값은 운영 표면으로 폴백하지 않는다."""
    if role not in _APP_ROLES:
        raise ConfigError(
            f"알 수 없는 앱 role입니다: {role!r}. 허용값: {sorted(_APP_ROLES)}"
        )
    settings = get_settings()
    validate_production_persistence(settings)
    suffix = {"customer": " (고객)", "admin": " (운영)", "full": ""}[role]
    app = FastAPI(
        #: 커머스 실습에서 보험 판정으로 도메인이 바뀌었다.
        title=f"{settings.BRAND_NAME}{suffix}",   #: BRAND_NAME 이 곧 프로젝트명이다
        version="0.3.0",
        lifespan=lifespan,
    )
    app.add_middleware(TraceMiddleware)

    #: ★정적 자산은 **매번 되물어 보게** 한다.
    #:
    #:   실측 2026-08-04: `/static/common.js` 를 `Cache-Control` 없이 내보내고 있었다.
    #:   그러면 브라우저가 `Last-Modified` 로 **제 마음대로 유효기간을 정한다**(heuristic).
    #:   그날 저장소 전체에서 옛 브랜드명을 지웠는데, 화면 제목에는 계속 옛 이름이 떴다 —
    #:   서버는 새 파일을 주고 있었고 브라우저가 옛 파일을 쓰고 있었다.
    #:
    #:   `no-cache` 는 "캐시 금지"가 아니라 **"쓰기 전에 반드시 확인"** 이다.
    #:   ETag 가 같으면 304 라 전송량은 그대로고, 바뀌면 즉시 반영된다.
    #:   ★버전 박은 파일명(`app.a1b2c3.js`)을 쓰면 `immutable` 이 맞지만
    #:     이 프로젝트는 파일명을 고정으로 쓰므로 그 선택지가 없다.
    @app.middleware("http")
    async def _no_stale_static(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    register_exception_handlers(app)

    # 고객 웹이 실제로 호출하는 공개 라우터(shop/video/mypage/AI상담 = 상품·주문·결제·에이전트·
    # 음성·얼굴). 이 집합만 고객 포트(8080)에 노출한다.
    app.include_router(health.router)
    app.include_router(auth.router)
    #: 보험 보장 사전판정 — 이 프로젝트의 본체다.
    app.include_router(precheck.router)
    app.include_router(cohort.router)
    #: ★합성 트랙 제출. 실제 제출(`precheck.router` 의 `/v1/observations`)과
    #:   **라우터 파일부터 갈라 둔다** — 스위치 하나로 가르면 언젠가 섞인다(§5-1).
    app.include_router(demo.router)
    #: 용어 설명 — 판정과 **다른 유스케이스**다. 응답에 verdict 가 없다.
    app.include_router(terms.router)
    #: 용어 챗봇 — ★보장 여부는 답하지 않는다. 판정 양식으로 넘긴다.
    app.include_router(chat.router)
    app.include_router(voice.router)
    app.include_router(face.router)

    # 현행 보험 운영 API만 관리자/full role에 싣는다. 구형 rag/bounty/workflow는
    # 이 모듈에서 import조차 하지 않아 보험 런타임 의존 그래프와 HTTP 표면에서 함께 분리한다.
    if role in {"admin", "full"}:
        app.include_router(admin.router)

    # 고객 앱은 운영/개발 정적 페이지를 차단(정적 마운트보다 먼저 매칭됨).
    if role == "customer":
        @app.get("/static/{filename}", include_in_schema=False)
        def _block_ops_static(filename: str):
            if filename in _OPS_STATIC:
                return PlainTextResponse("운영 도구는 관리자 포트에서 접근하세요.", status_code=404)
            target = _STATIC_DIR / filename
            if not target.is_file():
                return PlainTextResponse("Not Found", status_code=404)
            return FileResponse(str(target))

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    #: ★없는 파일을 반환하고 있었다.
    #:   `shop.html` · `index.html` 은 커머스 화면이라 `legacy/` 로 옮겼는데
    #:   여기 이름이 그대로 남아 500 이 났다. 보험 화면은 아직 없다.
    #:   **없는 것을 있는 척하지 않는다** — 무엇이 없는지 말하고 API 로 안내한다.
    #: ★보험 화면이 생겼다. 앞서 여기 `shop.html`(커머스)이 남아 500 이 났었다.
    landing = {"admin": "admin.html"}.get(role, "insurance.html")

    #: ★`llms.txt` — **에이전트가 먼저 읽는 안내문**(MVP §6).
    #:
    #:   OpenAPI 는 "어떤 엔드포인트가 있나"를 말하지만 "무엇을 조심해야 하나"는
    #:   말하지 않는다. 이 도메인에서 위험한 것은 스키마 오용이 아니라
    #:   **기권을 오류로 읽는 것**과 **합성을 실제로 읽는 것**이다. 그건 문장으로만 전할 수 있다.
    @app.get("/llms.txt", include_in_schema=False)
    def llms_txt():
        return PlainTextResponse(
            f"""# {settings.BRAND_NAME}

> 가입 약관 원문 조항을 근거로 보장 여부를 **사전검토**하고, 같은 조건의
> 과거 청구 결과 분포를 제공합니다. 보험금 지급을 확정하지 않습니다.

## 에이전트가 반드시 알아야 할 것

- **기권은 오류가 아니다.** `verdict="needs_expert"` + `abstained=true` 는 HTTP 200 이다.
  근거 조항을 못 댈 때 추측하지 않는 것이 설계다. 재시도해도 같은 답이 나온다.
- **면책 목록에 없다 ≠ 보장된다.** 실손 약관은 보장 대상을 질병코드로 나열하지 않고
  면책만 나열한다. 그래서 "보장됨"을 조항으로 증명할 수 없는 경우가 많다.
- **`data_source` 를 절대 무시하지 마라.** `synthetic` 은 시연용 생성 데이터다.
  `verified_real` 과 합치지 마라.
- **표본 미달이면 비율이 `null` 이다.** 직접 계산하지 마라 — 최소표본 미만에서
  비율을 말하지 않는 것이 정책이다.
- **`by_verification` 을 읽어라.** `admin_attested` 는 관리자 교차검증이며
  보험사·발행처에 조회해 확인한 것이 아니다.
- **제출은 검증이 아니다.** `/v1/observations` 로 보낸 결과는 `unverified` 로 접수되고
  검수를 거쳐야 통계에 반영된다. `verification` 을 직접 지정해도 무시된다.
- **지원범위를 먼저 확인하라.** `/v1/support-manifest` 밖의 보험사를 물으면 기권만 돌아온다.
  `identification_mode.auto_approve` 가 참이면 사람 최종승인을 거치지 않은 약관으로 판정한 것이다.

## API

- `GET  /v1/support-manifest` 지원 범위·판정 모드
- `POST /v1/prechecks` 보장 사전검토 (4단 판정 + 근거 조항 인용)
- `GET  /v1/cohorts?code=` 실제 검증분 코호트
- `GET  /v1/demo/cohorts?code=` 합성(시연) 코호트
- `POST /v1/observations` 청구 결과 보고
- `GET  /openapi.json` 전체 스키마

## MCP

`python -m app.mcp.server` (stdio). 도구 4종 — `precheck` · `explain_term` ·
`cohort_stats` · `submit_observation`. 리소스 — `insurance://support-manifest` ·
`insurance://runtime-config`.

## 하지 않는 것

보험금 지급 확정 · 자동 청구 · 보험상품 권유·판매 · 의료 진단 ·
개인 예상 지급액 산정(과거 분포만 제시)
""",
            media_type="text/plain; charset=utf-8",
        )

    @app.get("/", include_in_schema=False)
    def index():
        target = _STATIC_DIR / landing if landing else None
        if target and target.is_file():
            return FileResponse(str(target))
        return PlainTextResponse(
            "올바른 보험비서 — 보장 사전판정 API\n"
            "\n"
            "  POST /v1/prechecks         보장 사전판정\n"
            "  GET  /v1/support-manifest  무엇을 지원하는지\n"
            "  GET  /docs                 API 문서\n"
            "\n"
            "웹 화면은 아직 없습니다.\n",
            status_code=200,
        )

    return app


# 모듈 기본 ASGI 표면은 최소 권한 customer다. 운영 앱은 명시적으로 `admin_app`을 띄운다.
app = create_app("customer")
customer_app = app
admin_app = create_app("admin")
