"""Django BFF가 도메인 계약을 해석하지 않고 그대로 통과시키는지 검증한다."""

from __future__ import annotations

import os

import httpx
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "delivery.django_app.settings")

#: ★django 가 없는 환경에서는 **이 모듈만 건너뛴다.**
#:   import 를 그냥 두면 마커로 제외해도 **수집 단계에서 터져** 회귀검사 전체가 죽는다
#:   (2026-08-25 실측: `.venv` 에 django 미설치 → 주 회귀검사 수집 중단).
pytest.importorskip("django", reason="Django 전달 계층 미설치 환경에서는 건너뛴다")

from django.test import RequestFactory

pytestmark = pytest.mark.delivery


@pytest.fixture
def request_factory() -> RequestFactory:
    return RequestFactory()


def _inject_transport(monkeypatch, handler):
    from delivery.django_app import views

    def streaming_handler(request: httpx.Request) -> httpx.Response:
        response = handler(request)
        if not response.is_stream_consumed:
            return response
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=httpx.ByteStream(response.content),
        )

    client = httpx.Client(transport=httpx.MockTransport(streaming_handler))
    monkeypatch.setattr(views, "_http_client", client)
    return client


def test_요청과_응답을_가공하지_않는다(monkeypatch, request_factory):
    captured = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        captured["content_type"] = request.headers["content-type"]
        return httpx.Response(
            422,
            content=b'{"detail":[{"raw":true}]}',
            headers={"Content-Type": "application/problem+json", "X-Trace-ID": "upstream-trace"},
        )

    _inject_transport(monkeypatch, upstream)
    from delivery.django_app.views import passthrough

    raw_body = b'{"unvalidated": [1, 2]}'
    request = request_factory.generic(
        "PATCH",
        "/v1/prechecks?source=raw",
        data=raw_body,
        content_type="application/vnd.insurance+json",
    )
    response = passthrough(request, path="v1/prechecks")

    assert captured == {
        "method": "PATCH",
        "url": "http://127.0.0.1:8080/v1/prechecks?source=raw",
        "body": raw_body,
        "content_type": "application/vnd.insurance+json",
    }
    assert response.status_code == 422
    assert response.content == b'{"detail":[{"raw":true}]}'
    assert response.headers["Content-Type"] == "application/problem+json"
    assert response.headers["X-Trace-ID"] == "upstream-trace"


def test_기권_200을_오류로_바꾸지_않는다(monkeypatch, request_factory):
    body = b'{"verdict":"needs_expert"}'
    _inject_transport(
        monkeypatch,
        lambda request: httpx.Response(200, content=body, headers={"X-Trace-ID": "trace"}),
    )
    from delivery.django_app.views import passthrough

    response = passthrough(request_factory.get("/v1/prechecks/1"))

    assert response.status_code == 200
    assert response.content == body
    assert "Content-Type" not in response.headers


def test_중복_응답_헤더도_보존한다(monkeypatch, request_factory):
    duplicate_headers = [("Link", "</first>"), ("Link", "</second>")]
    _inject_transport(
        monkeypatch,
        lambda request: httpx.Response(200, headers=duplicate_headers),
    )
    from delivery.django_app.views import passthrough

    response = passthrough(request_factory.get("/links"))

    #: ★업스트림의 중복 헤더는 **뭉개지 않고 순서대로** 나가야 한다.
    assert [(name.lower(), value) for name, value in response.items()][:2] == [
        ("link", "</first>"),
        ("link", "</second>"),
    ]
    #: ★`Vary: Cookie` 는 Django 가 뒤에 붙인다 — `@ensure_csrf_cookie` 로 응답이
    #:   쿠키에 따라 달라지기 때문이다(HTTP 규약상 맞는 동작). **도메인 본문은 그대로다.**
    #:   이걸 「가공」으로 보고 지우면 캐시가 사용자별 응답을 섞는다.
    extra = [name.lower() for name, _ in response.items()][2:]
    assert extra in ([], ["vary"]), f"예상 못한 헤더가 붙었다: {extra}"


@pytest.mark.parametrize("status_code", [422, 503])
def test_도메인_오류를_그대로_통과시킨다(
    monkeypatch, request_factory, status_code
):
    body = f'{{"domain_status":{status_code}}}'.encode()
    _inject_transport(
        monkeypatch,
        lambda request: httpx.Response(status_code, content=body),
    )
    from delivery.django_app.views import passthrough

    response = passthrough(
        request_factory.generic(
            "POST", "/v1/domain", data=b"raw", content_type="application/octet-stream"
        )
    )

    assert response.status_code == status_code
    assert response.content == body


def test_기존_trace_id를_승계한다(monkeypatch, request_factory):
    captured = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["trace_id"] = request.headers["X-Trace-ID"]
        return httpx.Response(204, headers={"X-Trace-ID": "same-trace"})

    _inject_transport(monkeypatch, upstream)
    from delivery.django_app.views import passthrough

    response = passthrough(
        request_factory.get("/health", HTTP_X_TRACE_ID="same-trace")
    )

    assert captured["trace_id"] == "same-trace"
    assert response.headers["X-Trace-ID"] == "same-trace"


def test_trace_id가_없으면_발급해_업스트림에_보낸다(monkeypatch, request_factory):
    captured = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["trace_id"] = request.headers["X-Trace-ID"]
        return httpx.Response(200, headers={"X-Trace-ID": captured["trace_id"]})

    _inject_transport(monkeypatch, upstream)
    from delivery.django_app.views import passthrough

    response = passthrough(request_factory.get("/health"))

    assert captured["trace_id"]
    assert response.headers["X-Trace-ID"] == captured["trace_id"]


@pytest.mark.parametrize(
    ("exception", "expected_status"),
    [
        (httpx.ConnectError("down"), 502),
        (httpx.ReadTimeout("late"), 504),
    ],
)
def test_업스트림_연결과_타임아웃만_게이트웨이_오류로_만든다(
    monkeypatch, request_factory, exception, expected_status
):
    def upstream(request: httpx.Request) -> httpx.Response:
        raise exception

    _inject_transport(monkeypatch, upstream)
    from delivery.django_app.views import passthrough

    response = passthrough(request_factory.get("/health"))

    assert response.status_code == expected_status


def test_여러_요청이_같은_http_client를_재사용한다(monkeypatch, request_factory):
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(204)

    client = _inject_transport(monkeypatch, upstream)
    from delivery.django_app import views

    views.passthrough(request_factory.get("/first"))
    views.passthrough(request_factory.get("/second"))

    assert views._http_client is client
    assert calls == ["/first", "/second"]


def test_urlconf는_admin_뒤에_패스스루를_둔다():
    from delivery.django_app.urls import urlpatterns
    from delivery.django_app.views import passthrough

    assert len(urlpatterns) == 2
    assert str(urlpatterns[0].pattern) == "admin/"
    assert urlpatterns[1].callback is passthrough


def test_django_설정은_코어_설정을_단일_소스로_쓴다():
    from app.core.config import get_settings
    from delivery.django_app import settings

    core_settings = get_settings()
    assert settings.UPSTREAM_BASE_URL == core_settings.CUSTOMER_BASE_URL.rstrip("/")
    assert settings.DELIVERY_MODE == core_settings.DELIVERY_MODE
    assert settings.SECRET_KEY == core_settings.SECRET_KEY
