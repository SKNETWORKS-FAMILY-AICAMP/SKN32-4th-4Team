"""보험 customer/admin 앱의 HTTP 표면을 정확한 method+path 집합으로 고정한다."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import ConfigError
from app.main import admin_app, app, create_app, customer_app


_COMMON = frozenset(
    {
        ("GET", "/"),
        ("GET", "/api/face/backend"),
        ("GET", "/api/face/status"),
        ("GET", "/api/health"),
        ("GET", "/api/health/llm"),
        ("GET", "/api/health/ready"),
        ("GET", "/docs"),
        ("GET", "/docs/oauth2-redirect"),
        ("GET", "/llms.txt"),
        ("GET", "/openapi.json"),
        ("GET", "/redoc"),
        ("GET", "/v1/catalog/codes"),
        ("GET", "/v1/catalog/products"),
        ("GET", "/v1/chat/terms"),
        ("GET", "/v1/cohorts"),
        ("GET", "/v1/demo/cohorts"),
        ("GET", "/v1/support-manifest"),
        ("GET", "/v1/terms/explain"),
        ("POST", "/api/face/benchmark"),
        ("POST", "/api/face/register"),
        ("POST", "/api/voice/stt"),
        ("POST", "/api/voice/tts"),
        ("POST", "/auth/login"),
        ("POST", "/auth/login/face"),
        ("POST", "/auth/signup"),
        ("POST", "/v1/chat"),
        ("POST", "/v1/demo/observations"),
        ("POST", "/v1/observations"),
        ("POST", "/v1/observations/evidence"),
        ("POST", "/v1/prechecks"),
        ("PUT", "/api/face/backend"),
        ("DELETE", "/api/face/register"),
    }
)

_ADMIN = frozenset(
    {
        ("DELETE", "/api/admin/demo/simulation"),
        ("GET", "/api/admin/agents"),
        ("GET", "/api/admin/agents/stream"),
        ("GET", "/api/admin/cohort-summary"),
        ("GET", "/api/admin/demo/queue"),
        ("GET", "/api/admin/demo/simulation"),
        ("GET", "/api/admin/events"),
        ("GET", "/api/admin/index"),
        #: ★관리자 **로그인 밖**에 있는 유일한 경로다. Prometheus 가 긁어야 해서
        #:   `/api/admin/*` 아래에 못 둔다 — 그쪽 의존성을 타면 401 이다.
        #:   대신 전용 토큰이 있어야 열리고(`METRICS_SCRAPE_TOKEN`),
        #:   토큰을 안 정하면 **404** 다. 고객 앱에는 안 실린다.
        ("GET", "/metrics-scrape"),
        ("GET", "/api/admin/kcd-codes"),
        ("GET", "/api/admin/knowledge-gaps"),
        ("GET", "/api/admin/metrics"),
        ("GET", "/api/admin/precheck-mode"),
        ("GET", "/api/admin/report"),
        ("GET", "/api/admin/users"),
        ("GET", "/api/admin/verifications/queue"),
        ("POST", "/api/admin/clause-search"),
        ("POST", "/api/admin/demo/reset"),
        ("POST", "/api/admin/demo/simulation"),
        ("POST", "/api/admin/demo/verifications"),
        ("POST", "/api/admin/verifications"),
        ("PUT", "/api/admin/precheck-mode"),
        ("PUT", "/api/admin/users/{username}/role"),
    }
)


def _surface(fastapi_app: FastAPI) -> frozenset[tuple[str, str]]:
    return frozenset(
        (method, route.path)
        for route in fastapi_app.routes
        for method in (getattr(route, "methods", None) or ())
        if method not in {"HEAD", "OPTIONS"}
    )


def test_default_asgi_app_is_the_customer_app() -> None:
    assert app is customer_app


def test_unknown_app_role_is_rejected_before_it_can_open_routes() -> None:
    for role in ("custmer", "", "ADMIN", "typo-role", None):
        with pytest.raises(ConfigError):
            create_app(role)  # type: ignore[arg-type]


def test_customer_method_path_snapshot() -> None:
    assert _surface(customer_app) == _COMMON | {("GET", "/static/{filename}")}


def test_admin_method_path_snapshot() -> None:
    assert _surface(admin_app) == _COMMON | _ADMIN


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/rag/qa"),
        ("post", "/api/workflow/ticket"),
        ("get", "/api/bounty/grades"),
    ],
)
@pytest.mark.parametrize("insurance_app", [customer_app, admin_app])
def test_legacy_routes_are_absent_from_both_insurance_apps(
    insurance_app: FastAPI, method: str, path: str
) -> None:
    response = getattr(TestClient(insurance_app), method)(path)
    assert response.status_code == 404
