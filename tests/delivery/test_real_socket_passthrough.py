"""★**실제 소켓으로** 전달 계층을 왕복시킨다.

이 파일이 따로 있는 이유:

`RequestFactory` 와 `django.test.Client` 는 **WSGI 환경을 흉내만 낸다.**
그래서 `tests/delivery/test_django_passthrough.py` 의 단위 테스트 38건이 전부 통과하는
상태에서 **모든 요청이 500** 이고 **모든 POST 가 403** 이었다(실측 2026-08-26).

  - 500 — WSGI 서버는 본문 없는 GET 에도 `CONTENT_LENGTH` 를 넘긴다. 그게 헤더로
    실려 나가 httpx 가 `LocalProtocolError: bad Content-Length` 로 죽었다.
    `RequestFactory` 는 그 헤더를 **만들지 않는다.**
  - 403 — CSRF 토큰 쿠키를 아무도 발급하지 않아 클라이언트가 토큰을 **얻을 수 없었다.**
    단위 테스트는 뷰를 직접 불러 미들웨어를 안 태우므로 보이지 않았다.

→ 두 결함 다 **소켓을 열어야만** 드러난다. 여기서 그걸 연다.

리포트: docs/reports/debugs/2026-08-26_전달계층_소켓없이는_안_보이는_결함2종.md
"""

from __future__ import annotations

import json
import os
import threading
from wsgiref.simple_server import WSGIRequestHandler, make_server

import httpx
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "delivery.django_app.settings")
os.environ.setdefault("SECRET_KEY", "test-delivery-secret")

pytest.importorskip("django", reason="Django 전달 계층 미설치 환경에서는 건너뛴다")

import django  # noqa: E402
from django.test import override_settings  # noqa: E402

#: ★`django.setup()` 없이는 CSRF 실패 화면이 `AppRegistryNotReady` 로 터진다.
#:   `runserver` 는 이걸 대신 해 주므로 실제 기동에서는 안 보인다.
django.setup()

pytestmark = pytest.mark.delivery

UPSTREAM_MARK_HEADER = "X-Upstream-Mark"
UPSTREAM_MARK = "from-fastapi"


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        #: 테스트 출력에 접속 로그를 섞지 않는다.
        return


def _serve(app):
    """앱을 임시 포트에 띄우고 (base_url, 종료함수) 를 준다."""
    server = make_server("127.0.0.1", 0, app, handler_class=_QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop() -> None:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return f"http://127.0.0.1:{server.server_port}", stop


def _stub_upstream(environ, start_response):
    """받은 요청을 그대로 되비추는 가짜 FastAPI."""
    length = int(environ.get("CONTENT_LENGTH") or 0)
    body = environ["wsgi.input"].read(length) if length else b""
    payload = json.dumps(
        {
            "method": environ["REQUEST_METHOD"],
            "path": environ["PATH_INFO"],
            #: ★`latin-1` 로 읽는다 — 바이트를 **한 글자씩 그대로** 되비추기 위해서다.
            #:   utf-8 이면 PNG 첫 바이트(0x89)에서 스텁이 먼저 죽어
            #:   「전달 계층이 500」으로 잘못 읽힌다(실측 2026-08-26).
            "body": body.decode("latin-1"),
            #: ★전달 계층이 프레이밍 헤더를 걷어냈는지 업스트림 쪽에서 확인한다.
            "saw_host": environ.get("HTTP_HOST", ""),
        }
    ).encode("utf-8")
    start_response(
        "200 OK",
        [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(payload))),
            (UPSTREAM_MARK_HEADER, UPSTREAM_MARK),
        ],
    )
    return [payload]


@pytest.fixture
def live_delivery():
    """가짜 업스트림 + 실제 소켓 위의 Django 전달 계층."""
    from delivery.django_app import views

    upstream_url, stop_upstream = _serve(_stub_upstream)
    original = views.UPSTREAM_BASE_URL
    views.UPSTREAM_BASE_URL = upstream_url
    try:
        from django.core.handlers.wsgi import WSGIHandler

        with override_settings(ALLOWED_HOSTS=["127.0.0.1"]):
            delivery_url, stop_delivery = _serve(WSGIHandler())
            try:
                yield delivery_url, upstream_url
            finally:
                stop_delivery()
    finally:
        views.UPSTREAM_BASE_URL = original
        stop_upstream()


def test_본문없는_GET이_500이_되지_않는다(live_delivery):
    """★회귀: `CONTENT_LENGTH` 를 그대로 넘겨 모든 요청이 500 이었다."""
    delivery_url, _ = live_delivery
    response = httpx.get(f"{delivery_url}/api/health", timeout=10)

    assert response.status_code == 200, response.text
    assert json.loads(response.text)["method"] == "GET"


def test_업스트림이_전달계층의_Host를_받지_않는다(live_delivery):
    """`Host` 는 프레이밍 헤더다 — 업스트림 주소로 새로 붙어야 한다."""
    delivery_url, upstream_url = live_delivery
    response = httpx.get(f"{delivery_url}/api/health", timeout=10)

    seen = json.loads(response.text)["saw_host"]
    assert seen == upstream_url.removeprefix("http://")
    assert seen != delivery_url.removeprefix("http://")


def test_CSRF_토큰을_받을_수_있다(live_delivery):
    """★회귀: 토큰 쿠키가 없어 **모든 POST 가 영구히 403** 이었다."""
    delivery_url, _ = live_delivery
    with httpx.Client(base_url=delivery_url, timeout=10) as client:
        client.get("/api/health")
        assert client.cookies.get("csrftoken"), "CSRF 토큰 쿠키가 발급되지 않았다"


def test_토큰을_붙인_POST는_그대로_통과한다(live_delivery):
    delivery_url, _ = live_delivery
    with httpx.Client(base_url=delivery_url, timeout=10) as client:
        client.get("/api/health")
        response = client.post(
            "/v1/prechecks",
            content=b'{"insurer":"x"}',
            headers={
                "Content-Type": "application/json",
                "X-CSRFToken": client.cookies["csrftoken"],
            },
        )

    assert response.status_code == 200, response.text
    echoed = json.loads(response.text)
    assert echoed["method"] == "POST"
    assert echoed["body"] == '{"insurer":"x"}'
    assert response.headers[UPSTREAM_MARK_HEADER] == UPSTREAM_MARK


def test_토큰없는_POST는_403이고_도메인에_닿지_않는다(live_delivery):
    """403 은 설계대로다(계획서 D3·§1.3). 다만 **업스트림까지 가지 않아야** 한다."""
    delivery_url, _ = live_delivery
    response = httpx.post(f"{delivery_url}/v1/prechecks", json={"insurer": "x"}, timeout=10)

    assert response.status_code == 403
    assert UPSTREAM_MARK_HEADER not in response.headers


def test_미들웨어가_붙인_헤더가_사라지지_않는다():
    """★회귀: `items()` 가 업스트림 헤더만 돌려줘 뒤에 붙은 헤더가 **조용히 증발**했다."""
    from delivery.django_app.views import PassthroughResponse

    response = PassthroughResponse(b"{}", 200, [("Content-Type", "application/json")])
    response.headers["X-Content-Type-Options"] = "nosniff"

    assert ("X-Content-Type-Options", "nosniff") in response.items()
    assert ("Content-Type", "application/json") in response.items()


def test_업스트림_중복헤더는_뭉개지_않는다():
    from delivery.django_app.views import PassthroughResponse

    raw = [("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")]
    response = PassthroughResponse(b"", 204, raw)

    assert list(response.items()) == raw


def test_multipart_업로드가_500이_되지_않는다(live_delivery):
    """★회귀: CSRF 검사가 스트림을 먼저 소비해 **모든 업로드가 500** 이었다.

    `RawPostDataException: You cannot access body after reading from request's data stream`
    JSON POST 는 멀쩡했으므로 **업로드 경로를 따로 밟아야만** 드러난다.
    """
    delivery_url, _ = live_delivery
    with httpx.Client(base_url=delivery_url, timeout=10) as client:
        client.get("/api/health")
        response = client.post(
            "/v1/observations/evidence",
            files={"file": ("f.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")},
            headers={"X-CSRFToken": client.cookies["csrftoken"]},
        )

    assert response.status_code == 200, response.text
    echoed = json.loads(response.text)
    assert echoed["method"] == "POST"
    #: 업스트림이 본문을 **그대로** 받았는지 — 경계와 파일 바이트가 살아 있어야 한다.
    assert "f.png" in echoed["body"]
    assert "\x89PNG" in echoed["body"]


def test_전달계층이_업스트림보다_엄격한_문지기가_되지_않는다():
    """업로드 상한이 업스트림보다 낮으면 **도메인이 낸 적 없는 거절**이 나간다.

    Django 기본값은 2.5MB 라 그대로 두면 8~16MB 업로드가 전달 계층에서 잘린다.
    """
    from django.conf import settings

    from app.core.config import get_settings

    core = get_settings()
    upstream_cap = max(
        core.FACE_MAX_UPLOAD_BYTES,
        core.VOICE_MAX_UPLOAD_BYTES,
        core.OBSERVATION_EVIDENCE_MAX_UPLOAD_BYTES,
    )
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE > upstream_cap, (
        f"전달 계층 상한 {settings.DATA_UPLOAD_MAX_MEMORY_SIZE} 이 "
        f"업스트림 한도 {upstream_cap} 보다 낮거나 같다"
    )


def test_미들웨어가_지운_헤더는_되살아나지_않는다():
    """수정은 반영되고 삭제도 반영돼야 한다 — 예전 구현은 여기서 `KeyError` 로 죽었다."""
    from delivery.django_app.views import PassthroughResponse

    response = PassthroughResponse(
        b"", 200, [("Content-Type", "application/json"), ("X-Gone", "1")]
    )
    del response.headers["X-Gone"]
    response.headers["Content-Type"] = "text/plain"

    names = [name.lower() for name, _ in response.items()]
    assert "x-gone" not in names
    assert ("Content-Type", "text/plain") in response.items()


def test_keepalive_대조군_스위치가_실제로_연결재사용을_끈다(monkeypatch):
    """L1-c 대조군(계획서 §6.1) — 껐을 때 **정말 꺼지는지** 확인한다.

    ★측정 스위치가 실제로는 아무것도 안 바꾸면, 그 대조군 수치는 **거짓말**이 된다.
      「껐는데 안 느려졌다」는 결론이 나오고 그게 W7 무용론의 근거가 된다.
    """
    import importlib

    from delivery.django_app import views

    from delivery.django_app import settings as delivery_settings

    def limits_for(enabled: bool) -> int:
        #: ★`views` 를 패치해도 소용없다 — reload 가 settings 에서 **다시 읽어** 덮어쓴다.
        #:   값이 실제로 오는 곳을 패치해야 한다.
        monkeypatch.setattr(delivery_settings, "UPSTREAM_KEEPALIVE", enabled)
        reloaded = importlib.reload(views)
        try:
            return reloaded._http_client._transport._pool._max_keepalive_connections
        finally:
            monkeypatch.undo()
            importlib.reload(views)

    assert limits_for(False) == 0
    assert limits_for(True) > 0
