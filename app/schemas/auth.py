"""인증 DTO.

★원래 `app/schemas/commerce.py` 에 커머스 DTO 와 섞여 있었다.
  커머스를 `legacy/` 로 옮기면서 **인증만 남겨** 분리했다 —
  로그인·회원가입은 도메인과 무관하게 계속 쓴다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=4, max_length=100)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginResponse(BaseModel):
    """로그인 응답 — 얼굴 미등록이면 access_token 발급, 등록됐으면 얼굴 2차인증 요구.

    face_2fa_required=True인 경우 access_token은 없고 challenge_token(단명 pre2fa)만 있다.
    프론트는 challenge_token으로 `/auth/login/face`를 호출해 최종 access_token을 받는다.
    """

    face_2fa_required: bool = False
    access_token: str | None = None
    token_type: str = "bearer"
    challenge_token: str | None = None
