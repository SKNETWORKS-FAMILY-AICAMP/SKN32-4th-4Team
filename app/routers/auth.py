"""인증 라우터: 회원가입 / 로그인(JWT 발급) / 얼굴 2차 인증."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.security import OAuth2PasswordRequestForm
from app.auth.security import (
    STAGE_PRE2FA,
    Pre2FAChallenge,
    consume_challenge,
    create_access_token,
    get_pre2fa_challenge,
)
from app.auth.store import get_auth_store
from app.core.config import get_settings
from app.core.config_validation import require_secret_key
from app.core.errors import AuthErr, ValidationErr
from app.routers._uploads import read_capped
from app.schemas.auth import LoginResponse, SignupRequest, TokenResponse
from app.services import face_service, user_service

router = APIRouter(prefix="/auth", tags=["auth"])

# 얼굴 2차인증 대기 토큰의 짧은 유효시간(분).
_PRE2FA_EXPIRE_MIN = 5


@router.post("/signup", response_model=TokenResponse)
def signup(body: SignupRequest, store=Depends(get_auth_store)) -> TokenResponse:
    # SECRET_KEY preflight: 토큰 발급 불가 상태면 유저를 만들기 전에 실패시킨다
    # (Codex 지적: 이전엔 유저 commit 후 토큰 발급 단계에서 실패해 유저가 잔존)
    require_secret_key(get_settings())
    user = user_service.signup(store, body.username, body.password)
    return TokenResponse(access_token=create_access_token(user.username))


@router.post("/login", response_model=LoginResponse)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    store=Depends(get_auth_store),
) -> LoginResponse:
    """비밀번호 인증. 얼굴 미등록 계정은 바로 토큰 발급, 등록 계정은 얼굴 2차인증 요구.

    무폴백: 얼굴 등록 계정은 access_token을 주지 않고 pre2fa 챌린지만 준다 —
    얼굴 단계를 건너뛰고 비밀번호만으로 접근하는 경로가 없다.
    """
    user = user_service.authenticate(store, form.username, form.password)
    if face_service.has_face(store, user.id):
        challenge = create_access_token(
            user.username, stage=STAGE_PRE2FA, expires_minutes=_PRE2FA_EXPIRE_MIN
        )
        return LoginResponse(face_2fa_required=True, challenge_token=challenge)
    return LoginResponse(access_token=create_access_token(user.username))


@router.post("/login/face", response_model=TokenResponse)
async def login_face(
    image: UploadFile = File(...),
    challenge: Pre2FAChallenge = Depends(get_pre2fa_challenge),
    store=Depends(get_auth_store),
) -> TokenResponse:
    """얼굴 2차 인증 단계 — pre2fa 챌린지 토큰 + 얼굴 이미지로 최종 토큰 발급.

    게이트: 촬영 품질/단일 얼굴 → 라이브니스 → 임베딩 비교. 실패 시 토큰 없음(무폴백).
    성공 시 이 챌린지는 일회성으로 소비돼 재사용(리플레이)되지 않는다.
    """
    user = challenge.user
    image_bytes = await read_capped(image, get_settings().FACE_MAX_UPLOAD_BYTES, field="얼굴 이미지")
    if not image_bytes:
        raise ValidationErr("업로드된 이미지가 비어 있습니다.")
    if not face_service.has_face(store, user.id):
        # 등록이 사라진 예외적 상태 — 얼굴 인증 자체가 불가.
        raise ValidationErr("이 계정에는 등록된 얼굴이 없습니다.")
    face_service.verify_face(store, user, image_bytes)  # 실패 시 AuthErr/ForbiddenErr
    # 원자적 소비: 동시 요청/재사용은 여기서 정확히 하나만 통과(나머지는 401).
    if not consume_challenge(challenge.payload):
        raise AuthErr("이미 사용된 인증 챌린지입니다. 다시 로그인해주세요.")
    return TokenResponse(access_token=create_access_token(user.username))
