"""얼굴 2차 인증 테스트 (실 insightface + Silent-Face ONNX, @ml — CI 제외).

실행: pytest -m ml tests/test_face.py

주의: 라이브니스 모델은 정지 사진 크롭을 위조(spoof)로 판정하는 게 정상 동작이다(사진/화면
= 위조). 따라서 신원 매칭·토큰 흐름을 종단 검증하는 테스트는 라이브니스 임계값을 0으로 낮춰
(정지 이미지도 통과) 로직만 확인한다 — 실 웹캠 라이브 얼굴 수용은 이 환경에서 검증 불가.
품질 게이팅은 라이브니스와 별개로 항상 적용된다(t1 group photo는 face[0]만 strict 통과,
face[5]가 다른 인물로 loose 통과).
"""

from __future__ import annotations

import io
import uuid

import cv2
import numpy as np
import pytest

from app.core.config import get_settings
from app.core.config_validation import require_secret_key
from app.ml.face import analyze_face, cosine_similarity

pytestmark = pytest.mark.ml


def _crops():
    """t1에서 품질 게이트를 통과하는 크롭 생성.

    반환: A 등록샷 2장(face[0], strict 통과), A 검증샷(face[0]), B 검증샷(face[5], 타인),
    블러샷(품질 거부용).
    """
    import insightface
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    img = insightface.data.get_image("t1")
    faces = sorted(app.get(img), key=lambda f: float(f.bbox[0]))

    def crop(face, margin):
        x1, y1, x2, y2 = face.bbox
        w, h = x2 - x1, y2 - y1
        X1, Y1 = max(0, int(x1 - w * margin)), max(0, int(y1 - h * margin))
        X2 = min(img.shape[1], int(x2 + w * margin))
        Y2 = min(img.shape[0], int(y2 + h * margin))
        return img[Y1:Y2, X1:X2]

    def jpg(bgr):
        return cv2.imencode(".jpg", bgr)[1].tobytes()

    a_reg1 = jpg(crop(faces[0], 0.6))
    a_reg2 = jpg(crop(faces[0], 0.85))
    a_verify = jpg(crop(faces[0], 0.7))
    b_verify = jpg(crop(faces[5], 0.7))  # 다른 인물(loose 통과)
    blurry = jpg(cv2.GaussianBlur(crop(faces[0], 0.6), (31, 31), 0))
    return a_reg1, a_reg2, a_verify, b_verify, blurry


_A_REG1, _A_REG2, _A_VERIFY, _B_VERIFY, _BLURRY = _crops()


def test_embedding_same_person_high_similarity():
    ra1 = analyze_face(_A_REG1)
    ra2 = analyze_face(_A_VERIFY)
    assert cosine_similarity(ra1["embedding"], ra2["embedding"]) >= 0.40


def test_embedding_different_person_low_similarity():
    ra = analyze_face(_A_REG1)
    rb = analyze_face(_B_VERIFY)
    assert cosine_similarity(ra["embedding"], rb["embedding"]) < 0.40


def test_liveness_returns_valid_probability():
    r = analyze_face(_A_REG1)
    assert 0.0 <= r["live_prob"] <= 1.0
    assert isinstance(r["is_live"], bool)


def test_실제_얼굴은_기본_임계값에서_라이브니스를_통과한다():
    """★★**이 시험이 없어서 아무도 얼굴을 등록할 수 없었다(2026-08-04).**

    `_liveness_prob` 이 MiniFASNet 출력의 **0번 축**을 live 로 읽고 있었다.
    실제 순서는 `[2D-spoof, live, 3D-spoof]` 라 실제 얼굴의 0번 값은 ~0.001 이고,
    임계값이 0.50 이므로 **모든 실제 얼굴이 위조로 거부됐다.**

    ★기존 시험들은 전부 `relax_liveness`(임계값 0.0)를 쓰거나 값의 **범위만** 봤다.
      `0.001 >= 0.0` 은 참이므로 초록불이었다 — **임계값을 낮춰 통과시키는 것이
      이 결함을 정확히 가렸다.** 그래서 이 시험은 fixture 를 쓰지 않는다.
    """
    r = analyze_face(_A_REG1)
    assert r["is_live"] is True, (
        f"실제 얼굴이 위조로 거부됩니다(live_prob={r['live_prob']}). "
        "라이브니스 클래스 축이 틀렸을 수 있습니다 — MiniFASNet 은 index 1 이 live 입니다."
    )
    #: 축을 틀리면 값이 0 근처로 붙는다. 여유를 두되 "확실히 live 쪽"인지 본다.
    assert r["live_prob"] > 0.5


def test_라이브니스_클래스_축이_상수로_고정돼_있다():
    """★축 번호가 코드 여기저기 흩어지면 또 틀린다. 한 곳에만 둔다."""
    from app.ml.face import _LIVE_CLASS

    assert _LIVE_CLASS == 1, "MiniFASNet 의 live 클래스는 index 1 입니다."


def test_quality_gate_rejects_blurry_on_register():
    from app.core.errors import ValidationErr

    with pytest.raises(ValidationErr):
        analyze_face(_BLURRY, strict=True)  # 흐림 → 등록 엄격 게이트 거부


@pytest.fixture
def relax_liveness():
    """정지 사진도 라이브니스를 통과하도록 임계값을 0으로 (신원/토큰 로직만 검증)."""
    s = get_settings()
    old = s.FACE_LIVENESS_THRESHOLD
    s.FACE_LIVENESS_THRESHOLD = 0.0
    yield
    s.FACE_LIVENESS_THRESHOLD = old


def _signup_login(client):
    u = f"face_{uuid.uuid4().hex[:8]}"
    client.post("/auth/signup", json={"username": u, "password": "pass1234"})
    r = client.post("/auth/login", data={"username": u, "password": "pass1234"})
    return u, r.json()["access_token"]


def _reg_files(*blobs):
    return [("images", (f"face{i}.jpg", io.BytesIO(b), "image/jpeg")) for i, b in enumerate(blobs)]


def _img_file(b):
    return {"image": ("face.jpg", io.BytesIO(b), "image/jpeg")}


def test_no_face_user_logs_in_without_2fa(client):
    u, token = _signup_login(client)
    assert token
    r = client.post("/auth/login", data={"username": u, "password": "pass1234"})
    assert r.json()["face_2fa_required"] is False


def test_face_register_requires_auth(client):
    r = client.post("/api/face/register", files=_reg_files(_A_REG1))
    assert r.status_code == 401


def test_full_2fa_flow_match_and_mismatch(client, relax_liveness):
    u, token = _signup_login(client)
    hdr = {"Authorization": f"Bearer {token}"}

    # 다중 이미지 등록(품질 통과분 평균)
    r = client.post("/api/face/register", files=_reg_files(_A_REG1, _A_REG2), headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["registered"] is True and r.json()["shots_used"] >= 1

    # 로그인 → 얼굴 2차인증 요구
    r = client.post("/auth/login", data={"username": u, "password": "pass1234"})
    body = r.json()
    assert body["face_2fa_required"] is True and body.get("access_token") is None
    ch_hdr = {"Authorization": f"Bearer {body['challenge_token']}"}

    # 같은 사람 → 토큰 발급
    r = client.post("/auth/login/face", files=_img_file(_A_VERIFY), headers=ch_hdr)
    assert r.status_code == 200 and r.json()["access_token"]

    # 타인(품질 통과) → 불일치 401
    r2 = client.post("/auth/login", data={"username": u, "password": "pass1234"})
    ch2 = {"Authorization": f"Bearer {r2.json()['challenge_token']}"}
    r = client.post("/auth/login/face", files=_img_file(_B_VERIFY), headers=ch2)
    assert r.status_code == 401


def test_pre2fa_challenge_is_single_use(client, relax_liveness):
    """성공한 pre2fa 챌린지 토큰은 일회성 — 재사용(리플레이) 시 거부해야 한다(Codex 지적)."""
    u, token = _signup_login(client)
    hdr = {"Authorization": f"Bearer {token}"}
    client.post("/api/face/register", files=_reg_files(_A_REG1, _A_REG2), headers=hdr)

    r = client.post("/auth/login", data={"username": u, "password": "pass1234"})
    challenge = r.json()["challenge_token"]
    ch_hdr = {"Authorization": f"Bearer {challenge}"}

    # 1회차: 성공 → 최종 토큰
    r1 = client.post("/auth/login/face", files=_img_file(_A_VERIFY), headers=ch_hdr)
    assert r1.status_code == 200 and r1.json()["access_token"]

    # 2회차: 같은 챌린지 재사용 → 소비됨 → 401
    r2 = client.post("/auth/login/face", files=_img_file(_A_VERIFY), headers=ch_hdr)
    assert r2.status_code == 401


def test_benchmark_requires_admin(client):
    """벤치마크(3모델 실측)는 관리자 전용 — 미인증 401(무인증 모델연산 DoS 표면 차단)."""
    files = {
        "image_a": ("a.jpg", io.BytesIO(_A_VERIFY), "image/jpeg"),
        "image_b": ("b.jpg", io.BytesIO(_B_VERIFY), "image/jpeg"),
    }
    r = client.post("/api/face/benchmark", files=files)
    assert r.status_code == 401


def test_consume_challenge_is_atomic_single_use():
    """consume_challenge는 원자적 test-and-set — 처음만 True, 재소비/무-jti는 False."""
    import time as _t

    from app.auth.security import consume_challenge

    exp = _t.time() + 300
    payload = {"jti": uuid.uuid4().hex, "exp": exp}
    assert consume_challenge(payload) is True    # 최초 소비
    assert consume_challenge(payload) is False   # 재소비(리플레이/동시요청) 차단
    assert consume_challenge({"exp": exp}) is False  # jti 없으면 소비 대상 아님(fail-closed)


def test_challenge_consume_loser_path_returns_401(client, relax_liveness, monkeypatch):
    """소비 경쟁의 패배 경로(consume_challenge=False)는 500(NameError)이 아닌 401이어야 한다.

    get_pre2fa_challenge(조기거부)를 통과하고 verify까지 성공했지만 소비 시점에 이미 소비된
    상황을 강제 — auth.py의 패배 경로가 AuthErr(401)를 올바로 던지는지 회귀 방지.
    """
    u, token = _signup_login(client)
    hdr = {"Authorization": f"Bearer {token}"}
    client.post("/api/face/register", files=_reg_files(_A_REG1, _A_REG2), headers=hdr)
    r = client.post("/auth/login", data={"username": u, "password": "pass1234"})
    ch = {"Authorization": f"Bearer {r.json()['challenge_token']}"}
    monkeypatch.setattr("app.routers.auth.consume_challenge", lambda payload: False)
    r = client.post("/auth/login/face", files=_img_file(_A_VERIFY), headers=ch)
    assert r.status_code == 401


def test_pre2fa_without_jti_is_rejected(client):
    """jti 없는 pre2fa 토큰은 fail-closed로 거부 — 일회성 추적 불가 시 통과시키지 않음(폴백 금지)."""
    from datetime import datetime, timedelta, timezone

    from jose import jwt

    from app.auth.security import STAGE_PRE2FA

    s = get_settings()
    tok = jwt.encode(
        {"sub": "someone", "stage": STAGE_PRE2FA,
         "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        require_secret_key(s), algorithm=s.JWT_ALGORITHM,
    )
    r = client.post(
        "/auth/login/face", files=_img_file(_A_VERIFY),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 401


def test_face_register_rejects_too_many_images(client):
    """등록 이미지 장수 상한(합산 DoS 차단) — 상한 초과는 디코드 전에 422로 거부."""
    u, token = _signup_login(client)
    hdr = {"Authorization": f"Bearer {token}"}
    n = get_settings().FACE_MAX_ENROLL_IMAGES + 1
    files = [("images", (f"f{i}.jpg", io.BytesIO(b"x"), "image/jpeg")) for i in range(n)]
    r = client.post("/api/face/register", files=files, headers=hdr)
    assert r.status_code == 422


def test_pre2fa_token_rejected_by_protected_endpoint(client, relax_liveness):
    u, token = _signup_login(client)
    hdr = {"Authorization": f"Bearer {token}"}
    client.post("/api/face/register", files=_reg_files(_A_REG1, _A_REG2), headers=hdr)
    r = client.post("/auth/login", data={"username": u, "password": "pass1234"})
    challenge = r.json()["challenge_token"]
    r = client.get("/api/face/status", headers={"Authorization": f"Bearer {challenge}"})
    assert r.status_code == 401
