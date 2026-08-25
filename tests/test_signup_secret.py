"""signup SECRET_KEY preflight 회귀 테스트 (Codex 지적).

SECRET_KEY가 없으면 signup은 503으로 실패하고 유저를 생성하지 않아야 한다.
"""

import uuid

from app.core.config import Settings
from app.core.config_validation import require_secret_key
from app.core.errors import ConfigError


def test_require_secret_key_raises_without_key():
    s = Settings(_env_file=None, SECRET_KEY=None)
    try:
        require_secret_key(s)
        assert False, "ConfigError 기대"
    except ConfigError:
        pass


def test_signup_preflight_no_user_created(monkeypatch):
    """SECRET_KEY 미설정 시 signup preflight가 유저 생성 전에 실패한다."""
    from db.sqlite_legacy.connection import SessionLocal
    from db.sqlite_legacy.models import User
    from app.routers import auth as auth_router

    # get_settings를 secret 없는 설정으로 대체
    no_secret = Settings(_env_file=None, SECRET_KEY=None)
    monkeypatch.setattr(auth_router, "get_settings", lambda: no_secret)

    username = f"nosecret_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        before = db.query(User).filter(User.username == username).count()
        raised = False
        try:
            #: ★`app.schemas.commerce` 는 레거시 격리 때 사라졌다.
            #:   `SignupRequest` 는 커머스가 아니라 인증 스키마라 `app.schemas.auth` 로 갔는데
            #:   이 테스트만 옛 경로를 붙들고 있어 격리 이후 계속 실패하고 있었다.
            from app.schemas.auth import SignupRequest

            #: ★인자 이름이 `db` → `store` 로 바뀌었다(PgAuthStore 도 받게 되면서).
            #:   `db=` 로 부르면 `TypeError` 가 나는데, 이 시험은 `ConfigError` 만
            #:   잡으므로 **다른 이유로 실패한 것을 통과로 착각**할 뻔했다.
            #:   위치인자로 넘겨 이름 변경에 흔들리지 않게 한다.
            auth_router.signup(SignupRequest(username=username, password="pass1234"), db)
        except ConfigError:
            raised = True
        assert raised
        after = db.query(User).filter(User.username == username).count()
        assert before == after == 0
    finally:
        db.close()
