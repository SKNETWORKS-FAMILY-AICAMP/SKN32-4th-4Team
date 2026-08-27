"""회원 서비스: 가입 / 인증."""

from __future__ import annotations

from app.auth.security import hash_password, verify_password
from app.auth.user_types import AuthStore, AuthUser
from app.core.errors import AuthErr, ValidationErr
from db.postgres.auth_repository import PgAuthStore, PgUser


def signup(db: AuthStore, username: str, password: str) -> AuthUser:
    if isinstance(db, PgAuthStore):
        if db.get_user(username) is not None:
            raise ValidationErr("이미 존재하는 사용자명입니다.")
        return db.create_user(username, hash_password(password))
    #: ★레거시 SQLite 분기 **안에서만** 적재한다 — 최상단 import 는
    #:   PostgreSQL 전용 배포에도 SQLAlchemy 를 끌어온다(`app/auth/user_types.py`).
    from db.sqlite_legacy.models import User

    if db.query(User).filter(User.username == username).first():
        raise ValidationErr("이미 존재하는 사용자명입니다.")
    user = User(username=username, hashed_password=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: AuthStore, username: str, password: str) -> AuthUser:
    if isinstance(db, PgAuthStore):
        user = db.get_user(username)
        if user is None or not verify_password(password, user.hashed_password):
            raise AuthErr("사용자명 또는 비밀번호가 올바르지 않습니다.")
        return user
    #: ★레거시 SQLite 분기 **안에서만** 적재한다 — 최상단 import 는
    #:   PostgreSQL 전용 배포에도 SQLAlchemy 를 끌어온다(`app/auth/user_types.py`).
    from db.sqlite_legacy.models import User

    user = db.query(User).filter(User.username == username).first()
    if user is None or not verify_password(password, user.hashed_password):
        raise AuthErr("사용자명 또는 비밀번호가 올바르지 않습니다.")
    return user
