"""인증 테스트: 가입/로그인/토큰."""

from tests.conftest import auth_header


def test_signup_and_login(client, unique_user):
    username, password = unique_user()
    r = client.post("/auth/signup", json={"username": username, "password": password})
    assert r.status_code == 200
    assert r.json()["token_type"] == "bearer"

    r2 = client.post("/auth/login", data={"username": username, "password": password})
    assert r2.status_code == 200
    assert r2.json()["access_token"]


def test_duplicate_signup_rejected(client, unique_user):
    username, password = unique_user()
    client.post("/auth/signup", json={"username": username, "password": password})
    r = client.post("/auth/signup", json={"username": username, "password": password})
    assert r.status_code == 422  # ValidationErr


def test_wrong_password_401(client, unique_user):
    username, password = unique_user()
    client.post("/auth/signup", json={"username": username, "password": password})
    r = client.post("/auth/login", data={"username": username, "password": "wrong"})
    assert r.status_code == 401


#: ★보호 엔드포인트로 `/api/orders` 를 쓰던 것을 `/api/admin/*` 으로 바꿨다.
#:   주문 라우터는 커머스라 `legacy/` 로 옮겼다. 인증 검사 자체는 도메인과 무관하므로
#:   현행에 남아 있는 보호 라우터로 검증한다.
def test_invalid_token_401():
    from fastapi.testclient import TestClient

    from app.main import admin_app

    r = TestClient(admin_app).get(
        "/api/admin/events", headers={"Authorization": "Bearer invalid.token.here"}
    )
    assert r.status_code == 401


def test_protected_route_without_token_401():
    from fastapi.testclient import TestClient

    from app.main import admin_app

    r = TestClient(admin_app).get("/api/admin/events")
    assert r.status_code == 401
