"""인증: bcrypt 해싱 + JWT 발급/검증 + get_current_user.

SECRET_KEY는 config에서만 가져오며 미설정 시 ConfigError (하드코딩·폴백 금지).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.auth.store import get_auth_store
from app.core.config import Settings, get_settings
from app.core.config_validation import require_secret_key
from app.core.errors import AuthErr
from db.sqlite_legacy.models import User
from db.postgres.auth_repository import PgAuthStore, PgUser

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(raw: str) -> str:
    return _pwd.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return _pwd.verify(raw, hashed)


# 토큰 stage(Phase 13 얼굴 2차인증):
#   "full"   — 정상 접근 토큰(모든 보호 리소스 접근 가능)
#   "pre2fa" — 비밀번호는 통과했으나 얼굴 2차인증 대기 중인 단명 토큰(얼굴 단계에서만 유효)
STAGE_FULL = "full"
STAGE_PRE2FA = "pre2fa"

# 일회성 pre2fa 챌린지 소비 추적: jti -> 만료 epoch(초). 성공한 챌린지를 재사용(리플레이)하지
# 못하게 막는다(Codex 지적). 인메모리라 프로세스 재시작 시 초기화·멀티프로세스 미공유 —
# 데모 한계로 문서화. (프로덕션은 Redis 등 공유 저장소로 대체.)
_consumed_challenges: dict[str, float] = {}


def create_access_token(
    subject: str,
    settings: Settings | None = None,
    stage: str = STAGE_FULL,
    expires_minutes: int | None = None,
) -> str:
    settings = settings or get_settings()
    secret = require_secret_key(settings)
    minutes = expires_minutes if expires_minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    payload = {"sub": subject, "exp": expire, "stage": stage}
    # pre2fa 챌린지에는 고유 jti를 부여해 성공 후 일회성으로 소비할 수 있게 한다.
    if stage == STAGE_PRE2FA:
        payload["jti"] = uuid.uuid4().hex
    return jwt.encode(payload, secret, algorithm=settings.JWT_ALGORITHM)


def _prune_consumed(now: float) -> None:
    for jti, exp in list(_consumed_challenges.items()):
        if exp <= now:
            del _consumed_challenges[jti]


def consume_challenge(payload: dict) -> bool:
    """성공한 pre2fa 챌린지를 **원자적으로** 소비 — 이미 소비됐으면 False를 반환한다.

    검사(`in`)와 기록(`=`) 사이에 await가 없어 단일 프로세스 asyncio에선 원자적이다 → 같은
    챌린지로 들어온 동시 요청 중 정확히 하나만 True를 받는다(리플레이·경쟁 차단). 멀티프로세스·
    재시작 환경은 공유 저장소(Redis 등)가 필요 — 인메모리 데모 한계로 문서화.

    jti 없는 토큰은 소비 대상이 아니다(호출부가 이미 fail-closed로 거부) → False.
    """
    jti = payload.get("jti")
    if not jti:
        return False
    now = datetime.now(timezone.utc).timestamp()
    _prune_consumed(now)
    if jti in _consumed_challenges:
        return False
    exp = payload.get("exp")
    # 토큰 만료 시점까지만 보관하면 충분(만료 후엔 디코드 자체가 실패).
    _consumed_challenges[jti] = float(exp) if exp else now + 600.0
    return True


def _ensure_challenge_unused(payload: dict) -> None:
    """이미 소비된 챌린지를 **조기 거부**(UX용 빠른 실패). 최종 원자적 소비는 consume_challenge."""
    jti = payload.get("jti")
    if not jti:
        return
    now = datetime.now(timezone.utc).timestamp()
    _prune_consumed(now)
    if jti in _consumed_challenges:
        raise AuthErr("이미 사용된 인증 챌린지입니다. 다시 로그인해주세요.")


def _decode(token: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    secret = require_secret_key(settings)
    try:
        return jwt.decode(token, secret, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise AuthErr("토큰이 유효하지 않거나 만료되었습니다.") from exc


def decode_token(token: str, settings: Settings | None = None) -> str:
    """토큰에서 username(sub)을 추출. 무효/만료면 AuthErr."""
    payload = _decode(token, settings)
    username = payload.get("sub")
    if not username:
        raise AuthErr("토큰에 사용자 정보가 없습니다.")
    return username


def _lookup_user(store: Session | PgAuthStore, username: str) -> User | PgUser | None:
    if isinstance(store, PgAuthStore):
        return store.get_user(username)
    return store.query(User).filter(User.username == username).first()


def _user_from_token(
    token: str, store: Session | PgAuthStore, *, require_stage: str | None
) -> User | PgUser:
    payload = _decode(token)
    username = payload.get("sub")
    if not username:
        raise AuthErr("토큰에 사용자 정보가 없습니다.")
    # stage 미포함 토큰(구 버전/테스트)은 full로 간주(하위호환).
    stage = payload.get("stage", STAGE_FULL)
    if require_stage is not None and stage != require_stage:
        raise AuthErr("이 토큰으로는 요청한 작업을 수행할 수 없습니다.")
    user = _lookup_user(store, username)
    if user is None:
        raise AuthErr("사용자를 찾을 수 없습니다.")
    return user


def get_current_user(
    token: str = Depends(oauth2_scheme),
    store: Session | PgAuthStore = Depends(get_auth_store),
) -> User | PgUser:
    """정상 접근 토큰만 허용 — pre2fa 단명 토큰은 거부(얼굴 단계 전용)."""
    payload = _decode(token)
    if payload.get("stage", STAGE_FULL) == STAGE_PRE2FA:
        raise AuthErr("얼굴 2차 인증이 완료되지 않았습니다.")
    username = payload.get("sub")
    if not username:
        raise AuthErr("토큰에 사용자 정보가 없습니다.")
    user = _lookup_user(store, username)
    if user is None:
        raise AuthErr("사용자를 찾을 수 없습니다.")
    return user


def get_pre2fa_user(
    token: str = Depends(oauth2_scheme),
    store: Session | PgAuthStore = Depends(get_auth_store),
) -> User | PgUser:
    """얼굴 2차인증 단계 전용 — pre2fa stage 토큰만 허용."""
    return _user_from_token(token, store, require_stage=STAGE_PRE2FA)


class Pre2FAChallenge:
    """pre2fa 챌린지 검증 결과 — 사용자 + 원본 payload(성공 시 소비용 jti 포함)."""

    __slots__ = ("user", "payload")

    def __init__(self, user: User | PgUser, payload: dict) -> None:
        self.user = user
        self.payload = payload


def get_pre2fa_challenge(
    token: str = Depends(oauth2_scheme),
    store: Session | PgAuthStore = Depends(get_auth_store),
) -> Pre2FAChallenge:
    """얼굴 2차인증 단계 전용 — pre2fa 토큰만 허용 + 이미 소비된 챌린지 거부(일회성).

    라우터는 인증 성공 시 `consume_challenge(challenge.payload)`로 이 챌린지를 소비해야 한다.
    """
    payload = _decode(token)
    if payload.get("stage", STAGE_FULL) != STAGE_PRE2FA:
        raise AuthErr("이 토큰으로는 요청한 작업을 수행할 수 없습니다.")
    # fail-closed: 정상 발급된 pre2fa 챌린지는 항상 jti를 갖는다. jti 없는 pre2fa 토큰은
    # 일회성 소비 추적이 불가능하므로 허용하지 않는다(폴백 금지).
    if not payload.get("jti"):
        raise AuthErr("인증 챌린지 형식이 올바르지 않습니다. 다시 로그인해주세요.")
    _ensure_challenge_unused(payload)
    username = payload.get("sub")
    if not username:
        raise AuthErr("토큰에 사용자 정보가 없습니다.")
    user = _lookup_user(store, username)
    if user is None:
        raise AuthErr("사용자를 찾을 수 없습니다.")
    return Pre2FAChallenge(user, payload)
