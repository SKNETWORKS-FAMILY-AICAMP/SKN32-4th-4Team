"""Django Admin과 catch-all 패스스루의 라우팅 경계를 검증한다."""

from __future__ import annotations

import os

import httpx
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "delivery.django_app.settings")
os.environ.setdefault("SECRET_KEY", "test-delivery-secret")

pytest.importorskip("django", reason="Django 전달 계층 미설치 환경에서는 건너뛴다")

import django  # noqa: E402

django.setup()

from django.test import Client, override_settings  # noqa: E402

pytestmark = pytest.mark.delivery


def _streaming_client(handler) -> httpx.Client:
    """패스스루 뷰가 `iter_raw()`로 읽을 수 있는 MockTransport 응답을 만든다."""

    def streaming_handler(request: httpx.Request) -> httpx.Response:
        response = handler(request)
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=httpx.ByteStream(response.content),
        )

    return httpx.Client(transport=httpx.MockTransport(streaming_handler))


def test_전달계층_소유_model_하나만_admin에_등록한다():
    from django.apps import apps
    from django.conf import settings
    from django.contrib import admin

    from delivery.django_app.models import DeliveryAuditLog

    delivery_models = list(apps.get_app_config("django_app").get_models())

    assert delivery_models == [DeliveryAuditLog]
    assert admin.site.is_registered(DeliveryAuditLog)
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    assert str(settings.DATABASES["default"]["NAME"]).endswith("delivery.sqlite3")


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_admin_login은_업스트림을_거치지_않고_django_화면을_준다(monkeypatch):
    from delivery.django_app import views

    def unexpected_upstream(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Admin 요청이 업스트림으로 전달됐다: {request.url}")

    client = _streaming_client(unexpected_upstream)
    monkeypatch.setattr(views, "_http_client", client)

    response = Client().get("/admin/login/")

    assert response.status_code == 200
    assert response.resolver_match.namespace == "admin"
    assert response.resolver_match.url_name == "login"
    assert b'name="username"' in response.content
    assert b'name="password"' in response.content


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_admin_밖의_경로는_계속_업스트림으로_통과한다(monkeypatch):
    from delivery.django_app import views

    captured: dict[str, str] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(
            207,
            content=b"upstream-body",
            headers={"X-Upstream-Mark": "reached"},
        )

    client = _streaming_client(upstream)
    monkeypatch.setattr(views, "_http_client", client)

    response = Client().get("/v1/still-passthrough")

    assert captured == {"path": "/v1/still-passthrough"}
    assert response.status_code == 207
    assert response.content == b"upstream-body"
    assert response.headers["X-Upstream-Mark"] == "reached"
