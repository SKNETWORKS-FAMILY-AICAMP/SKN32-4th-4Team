"""관리자가 화면에서 사용자 권한을 바꾸는 경로.

★무엇을 열고 무엇을 닫았나

    열었다 — **이미 관리자인 사람**이 다른 계정을 승격·강등하는 것.
             그동안 CLI 로만 됐고, 팀원을 추가하려면 매번 서버에 붙어야 했다.
    닫아 뒀다 — **자기 자신을 관리자로 만드는 경로.** 화면에 그 버튼을 두면
             가입한 누구나 관리자가 된다. 최초 1명 부트스트랩은 CLI 로만 한다.

★규칙이 한 곳에 있는지도 시험한다

    "마지막 관리자 강등 금지"가 CLI 와 API 에 각각 있으면 느슨한 쪽이 실질 규칙이 된다.
    이 저장소에서 이미 두 번 겪었다(검수 근거 길이, 판정 목록 캐시).
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import auth_header


@pytest.fixture
def client():
    """사용자 권한 관리 계약은 admin 보험 앱을 명시해 검증한다."""
    from fastapi.testclient import TestClient

    from app.main import admin_app

    return TestClient(admin_app)


def _mkuser(client) -> tuple[str, str]:
    u = f"u_{uuid.uuid4().hex[:8]}"
    p = "pass1234"
    client.post("/auth/signup", json={"username": u, "password": p})
    return u, p


def _set_role_direct(username: str, role: str) -> None:
    from app.db.database import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        db.query(User).filter(User.username == username).update({"role": role})
        db.commit()
    finally:
        db.close()


@pytest.fixture
def admin(client):
    """관리자 1명(부트스트랩은 DB 직접 — CLI 와 같은 자리)."""
    u, p = _mkuser(client)
    _set_role_direct(u, "ADMIN")
    return u, p, auth_header(client, u, p)


# ── 가입은 항상 일반 사용자다 ─────────────────────────────────────────────
def test_가입은_언제나_일반_사용자로_만들어진다(client):
    """★화면의 '계정 만들기' 가 관리자를 만들면 그건 권한 상승이다."""
    u, _ = _mkuser(client)

    from app.db.database import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        assert db.query(User).filter(User.username == u).first().role == "USER"
    finally:
        db.close()


def test_일반_사용자는_역할을_바꿀_수_없다(client, admin):
    """자기 자신을 올리는 것도 당연히 막힌다(라우터 전역 게이트)."""
    u, p = _mkuser(client)
    headers = auth_header(client, u, p)

    r = client.put(f"/api/admin/users/{u}/role", json={"role": "ADMIN"}, headers=headers)
    assert r.status_code == 403


def test_미인증은_401(client):
    r = client.put("/api/admin/users/whoever/role", json={"role": "ADMIN"})
    assert r.status_code == 401


# ── 관리자는 다른 계정을 올리고 내릴 수 있다 ──────────────────────────────
def test_관리자는_다른_계정을_승격하고_강등한다(client, admin):
    _au, _ap, headers = admin
    target, _ = _mkuser(client)

    r = client.put(f"/api/admin/users/{target}/role", json={"role": "ADMIN"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["changed"] is True and r.json()["role"] == "ADMIN"

    r = client.put(f"/api/admin/users/{target}/role", json={"role": "USER"}, headers=headers)
    assert r.status_code == 200 and r.json()["role"] == "USER"


def test_같은_역할로_바꾸면_변경없음으로_답한다(client, admin):
    _au, _ap, headers = admin
    target, _ = _mkuser(client)

    r = client.put(f"/api/admin/users/{target}/role", json={"role": "USER"}, headers=headers)
    assert r.status_code == 200 and r.json()["changed"] is False


def test_없는_계정은_404(client, admin):
    _au, _ap, headers = admin
    r = client.put("/api/admin/users/nobody_here/role", json={"role": "ADMIN"}, headers=headers)
    assert r.status_code == 404


def test_알_수_없는_역할은_거부한다(client, admin):
    _au, _ap, headers = admin
    target, _ = _mkuser(client)
    r = client.put(f"/api/admin/users/{target}/role", json={"role": "SUPERUSER"},
                   headers=headers)
    assert r.status_code >= 400


# ── ★잠금 방지 ───────────────────────────────────────────────────────────
def test_마지막_관리자는_강등할_수_없다(client):
    """★이걸 허용하면 아무도 대시보드에 못 들어간다 — CLI 로만 복구 가능해진다."""
    from app.db.database import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        db.query(User).update({"role": "USER"})  # 모든 관리자 해제
        db.commit()
    finally:
        db.close()

    u, p = _mkuser(client)
    _set_role_direct(u, "ADMIN")
    headers = auth_header(client, u, p)

    r = client.put(f"/api/admin/users/{u}/role", json={"role": "USER"}, headers=headers)
    assert r.status_code >= 400
    assert "마지막 관리자" in r.text


def test_관리자가_둘이면_자기_자신도_강등할_수_있다(client, admin):
    """잠금만 막으면 된다 — 필요 이상으로 막지 않는다."""
    au, _ap, headers = admin
    other, op = _mkuser(client)
    client.put(f"/api/admin/users/{other}/role", json={"role": "ADMIN"}, headers=headers)

    r = client.put(f"/api/admin/users/{au}/role", json={"role": "USER"}, headers=headers)
    assert r.status_code == 200 and r.json()["role"] == "USER"


# ── 목록 ─────────────────────────────────────────────────────────────────
def test_목록은_비밀번호_해시를_내보내지_않는다(client, admin):
    _au, _ap, headers = admin
    r = client.get("/api/admin/users", headers=headers)
    assert r.status_code == 200

    body = r.json()
    assert body["admin_count"] >= 1
    for u in body["users"]:
        assert set(u) == {"id", "username", "role", "face_registered"}
    blob = r.text.lower()
    for banned in ("password", "hashed", "$2b$", "pbkdf2"):
        assert banned not in blob


def test_규칙은_한_곳에만_있다():
    """★CLI 와 API 가 같은 함수를 쓰는지 — 두 곳이면 느슨한 쪽이 실질 규칙이 된다."""
    import io
    import pathlib
    import tokenize

    src = pathlib.Path("scripts/manage.py").read_text(encoding="utf-8")
    code = "".join(
        t.string
        for t in tokenize.generate_tokens(io.StringIO(src).readline)
        if t.type not in (tokenize.COMMENT, tokenize.STRING)
    )
    assert "change_role" in code, "CLI 가 도메인 규칙을 부르지 않습니다."
    #: CLI 가 규칙을 **자기 안에** 다시 갖고 있으면 안 된다.
    assert "마지막 관리자" not in code


# ── 얼굴 2FA 잠금 복구 ────────────────────────────────────────────────────
def test_얼굴_해제로_잠금을_풀_수_있다(client):
    """★★얼굴 2FA 는 **되돌릴 수 없는 잠금**이 될 수 있다.

    등록하면 다음 로그인부터 얼굴이 필요하고, 해제하려면 로그인해야 한다.
    카메라 없는 PC 로 옮기거나 얼굴이 안 맞으면 그 계정은 끝이다.
    실제로 겪었다(2026-08-04) — 그래서 CLI 복구 수단을 만들었다.
    """
    from scripts.manage import reset_face

    from app.db.database import SessionLocal
    from app.db.models import FaceCredential, User

    u, p = _mkuser(client)
    db = SessionLocal()
    try:
        uid = db.query(User).filter(User.username == u).first().id
        db.add(FaceCredential(user_id=uid, embedding=b"\x00" * 16))
        db.commit()
        assert db.query(FaceCredential).filter(FaceCredential.user_id == uid).count() == 1
    finally:
        db.close()

    msg = reset_face(u)
    assert "해제" in msg

    db = SessionLocal()
    try:
        assert db.query(FaceCredential).filter(FaceCredential.user_id == uid).count() == 0
        #: ★비밀번호는 건드리지 않는다 — 얼굴만 지운다.
        assert db.query(User).filter(User.username == u).first() is not None
    finally:
        db.close()

    #: 해제 후에는 비밀번호만으로 들어간다.
    r = client.post("/auth/login", data={"username": u, "password": p})
    assert r.status_code == 200 and r.json()["face_2fa_required"] is False


def test_얼굴이_없으면_해제는_그렇다고_말한다(client):
    """조용히 성공한 척하지 않는다."""
    from scripts.manage import reset_face

    u, _ = _mkuser(client)
    assert "없습니다" in reset_face(u)


def test_없는_계정_얼굴해제는_명시적으로_실패한다():
    from app.core.errors import NotFoundErr
    from scripts.manage import reset_face

    with pytest.raises(NotFoundErr):
        reset_face("nobody_here_at_all")


def test_검수근거_최소길이가_화면과_서버에서_같다():
    """★다르면 화면이 통과시킨 것을 서버가 거절해 사용자가 이유를 모른다.

    실제로 그 반대가 났다(2026-08-04) — 화면이 빈 값을 그대로 보내
    pydantic 오류가 날것으로 찍혔다.
    """
    import pathlib
    import re

    from app.adapters.external_submission_store import _MIN_BASIS_LEN

    js = pathlib.Path("app/static/admin.js").read_text(encoding="utf-8")
    m = re.search(r"const MIN_BASIS_LEN = (\d+);", js)
    assert m, "admin.js 에 MIN_BASIS_LEN 상수가 없습니다."
    assert int(m.group(1)) == _MIN_BASIS_LEN, (
        f"화면({m.group(1)})과 서버({_MIN_BASIS_LEN})의 최소 길이가 다릅니다."
    )
