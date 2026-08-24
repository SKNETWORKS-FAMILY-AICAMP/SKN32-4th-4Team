"""역할(RBAC) 정의와 관리자 의존성 (Phase 9).

설계 핵심: **role은 JWT에 넣지 않는다.** 토큰에 박으면 권한 강등이 토큰 만료 전까지 반영되지
않아 stale privilege가 생긴다. `get_current_user`가 어차피 요청마다 DB에서 User를 읽으므로,
DB의 role을 신뢰하면 추가 비용 없이 항상 최신 권한이 적용된다.

무폴백: DB의 role이 허용값이 아니면 **USER로 간주하지 않고** 명시적으로 거부한다
(데이터 오염 시 조용히 권한을 주거나 뺏지 않는다).
"""

from __future__ import annotations

from fastapi import Depends

from app.auth.security import get_current_user
from app.core.errors import ForbiddenErr, InfraError
from app.db.models import User
from db.postgres.auth_repository import PgAuthStore

ROLE_USER = "USER"
ROLE_ADMIN = "ADMIN"
ROLES = frozenset({ROLE_USER, ROLE_ADMIN})


def validate_role(role: str | None) -> str:
    """역할 문자열을 검증해 반환. 알 수 없으면 InfraError(폴백 금지)."""
    if role not in ROLES:
        raise InfraError(f"알 수 없는 역할 값입니다(데이터 오염): {role!r}")
    return role


def require_admin(user: User = Depends(get_current_user)) -> User:
    """ADMIN만 통과. 미인증은 get_current_user가 401, USER는 여기서 403.

    라우터 단위 의존성으로 걸어 **fail-closed**를 강제한다(엔드포인트마다 붙이면 누락된다).
    """
    if validate_role(user.role) != ROLE_ADMIN:
        raise ForbiddenErr("관리자 권한이 필요합니다.")
    return user


def change_role(db, username: str, role: str, *, actor: str) -> dict:
    """역할을 바꾼다 — **규칙은 여기 한 벌뿐이다.**

    ★★왜 CLI 와 API 가 이 함수를 **같이** 써야 하나

        규칙이 두 곳에 있으면 **느슨한 쪽이 실질 규칙**이 된다. 이 저장소에서
        이미 두 번 겪었다(검수 근거 길이가 라우터에만 있어 CLI 가 우회했고,
        판정 목록 캐시가 라우터·그래프에 따로 있어 서로 다른 값을 봤다).
        그래서 "마지막 관리자 강등 금지"·"감사 기록"을 이 함수에 모으고
        `scripts/manage.py` 도 이것을 부른다.

    ★막는 것과 막지 않는 것

        막는다   — 마지막 ADMIN 강등(잠금 방지). 알 수 없는 역할 값.
        막지 않는다 — 관리자가 **다른 관리자를 만드는 것**. 그건 정상 운영이다.
                     자기 자신 강등도 다른 관리자가 남아 있으면 허용한다.

    ★여기 없는 것: **자기 자신을 관리자로 올리는 경로.**
        이 함수는 호출자가 이미 ADMIN 임을 전제한다(`require_admin`).
        최초 1명 부트스트랩은 CLI 로만 한다 — 화면에 그 버튼을 두면
        가입한 누구나 관리자가 된다.
    """
    from app.core.errors import NotFoundErr, ValidationErr
    from app.db.models import User as _User
    from app.obs.events import record_event

    validate_role(role)
    if isinstance(db, PgAuthStore):
        try:
            result = db.change_role(username, role, actor=actor)
        except ValueError as exc:
            raise ValidationErr(str(exc)) from exc
        if result.get("missing"):
            raise NotFoundErr(f"사용자를 찾을 수 없습니다: {username}")
        return result

    user = db.query(_User).filter(_User.username == username).first()
    if user is None:
        raise NotFoundErr(f"사용자를 찾을 수 없습니다: {username}")

    before = user.role
    if before == role:
        return {"changed": False, "username": username, "role": role,
                "message": f"변경 없음(이미 {role}): {username}"}

    if before == ROLE_ADMIN and role != ROLE_ADMIN:
        remaining = (
            db.query(_User).filter(_User.role == ROLE_ADMIN, _User.id != user.id).count()
        )
        if remaining == 0:
            raise ValidationErr("마지막 관리자는 강등할 수 없습니다(잠금 방지).")

    user.role = role
    db.commit()
    #: 감사 기록 — **누가** 바꿨는지까지 남긴다(CLI 는 actor 를 "cli" 로 넘긴다).
    record_event(db, "role_change",
                 {"username": username, "from": before, "to": role, "by": actor})
    return {"changed": True, "username": username, "role": role, "from": before,
            "message": f"역할 변경: {username} {before} → {role}"}
