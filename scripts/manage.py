"""명시적 운영 명령(REQ-OPS-01) — 기동 시 자동설정을 대체.

사용:
    python -m scripts.manage migrate   # 테이블 생성(멱등)
    python -m scripts.manage ready      # readiness 상태 출력

무폴백: 각 명령은 성공/실패를 명확히 보고하며, 실패를 삼키지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys


def _open_auth_store():
    """Open the configured auth store; SQLite is available only as legacy mode."""
    from app.core.config import get_settings

    settings = get_settings()
    if settings.AUTH_PERSISTENCE == "postgres":
        from db.postgres.auth_repository import PgAuthStore

        return PgAuthStore.from_settings()
    if not settings.SQLITE_LEGACY_ENABLED:
        raise RuntimeError("SQLite legacy persistence is disabled")
    from db.sqlite_legacy.connection import SessionLocal

    return SessionLocal()


def _open_ops_store():
    """Open the configured observability store; no implicit SQLite fallback."""
    from app.core.config import get_settings

    settings = get_settings()
    if settings.OPS_PERSISTENCE == "postgres":
        from db.postgres.ops_repository import PgOpsStore

        return PgOpsStore.from_settings()
    if not settings.SQLITE_LEGACY_ENABLED:
        raise RuntimeError("SQLite legacy persistence is disabled")
    from db.sqlite_legacy.connection import SessionLocal

    return SessionLocal()


def _close_store(store) -> None:
    close = getattr(store, "close", None)
    if close is not None:
        close()


def _add_missing_columns(engine) -> list[str]:
    """기존 테이블에 빠진 컬럼을 멱등하게 추가한다.

    `create_all`은 **없는 테이블만** 만들고 기존 테이블에 컬럼을 추가하지 않는다. 그래서
    Phase 9의 `users.role`처럼 나중에 생긴 컬럼은 기존 DB에 반영되지 않아 조용히 깨진다.
    Alembic 없이 가는 대신 여기서 명시적으로 추가하고, 아래에서 **사후 검증**한다.
    """
    from sqlalchemy import inspect, text

    added: list[str] = []
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return added  # create_all이 새로 만들었으면 스키마가 이미 최신
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "role" not in columns:
        with engine.begin() as conn:
            # 기존 사용자는 전부 USER. 관리자 자동 승격은 하지 않는다.
            conn.execute(
                text("ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'USER'")
            )
            conn.execute(text("UPDATE users SET role = 'USER' WHERE role IS NULL"))
        added.append("users.role")
    return added


def _verify_schema(engine) -> None:
    """마이그레이션 사후 검증 — 기대 컬럼이 실제로 있는지 확인하고, 없으면 명시적 실패."""
    from sqlalchemy import inspect

    from app.core.errors import InfraError

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "role" not in columns:
        raise InfraError("마이그레이션 후에도 users.role 컬럼이 없습니다(스키마 검증 실패).")


def cmd_migrate() -> None:
    from app.core.config import get_settings

    settings = get_settings()
    if (
        settings.AUTH_PERSISTENCE == "postgres"
        or settings.OPS_PERSISTENCE == "postgres"
        or not settings.SQLITE_LEGACY_ENABLED
    ):
        raise RuntimeError(
            "현재 설정은 PostgreSQL 운영 모드입니다. SQLite 레거시 migrate를 실행하지 "
            "말고 scripts/db/apply.py로 PostgreSQL 마이그레이션을 적용하세요."
        )

    from db.sqlite_legacy.connection import Base, engine

    # 현행 보험 모델만 Base에 등록되도록 import
    import db.sqlite_legacy.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    added = _add_missing_columns(engine)
    _verify_schema(engine)
    if added:
        print(f"[migrate] 컬럼 추가: {', '.join(added)}")
    print("[migrate] 테이블 생성·스키마 검증 완료(멱등).")


def cmd_ready() -> None:
    from app.obs.readiness import check_readiness

    status = check_readiness()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if not status["ready"]:
        sys.exit(1)  # 미준비는 비정상 종료(무폴백)


def set_role(username: str, role: str) -> str:
    """사용자 역할을 변경한다(멱등). **규칙은 `app.auth.roles.change_role` 한 벌뿐이다.**

    ★전에는 이 함수가 규칙(마지막 관리자 강등 금지·감사 기록)을 **직접** 들고 있었다.
      그러다 관리자 화면에서도 역할을 바꾸게 되면서 규칙이 두 곳이 될 뻔했다 —
      두 곳이면 느슨한 쪽이 실질 규칙이 된다. 그래서 도메인으로 옮기고 여기서는 부른다.

    부트스트랩은 여전히 **이 CLI 하나뿐**이다. 최초 관리자를 화면에서 만들 수 있게 하면
    가입한 누구나 관리자가 된다(권한 상승). 그 뒤의 추가는 관리자가 화면에서 한다.
    """
    from app.auth.roles import change_role

    db = _open_auth_store()
    try:
        return change_role(db, username, role, actor="cli")["message"]
    finally:
        _close_store(db)


def cmd_promote(username: str) -> None:
    from app.auth.roles import ROLE_ADMIN

    print(f"[promote] {set_role(username, ROLE_ADMIN)}")


def cmd_demote(username: str) -> None:
    from app.auth.roles import ROLE_USER

    print(f"[demote] {set_role(username, ROLE_USER)}")


def purge_gaps(days: int) -> int:
    """보존기간이 지난 지식보강 큐 항목을 파기한다. 삭제 건수를 반환."""
    from datetime import datetime, timedelta, timezone

    from db.postgres.ops_repository import PgOpsStore

    store = _open_ops_store()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        if isinstance(store, PgOpsStore):
            return store.purge_knowledge_gaps(cutoff)

        from db.sqlite_legacy.models import KnowledgeGap

        sqlite_cutoff = cutoff.replace(tzinfo=None)
        deleted = (
            store.query(KnowledgeGap)
            .filter(KnowledgeGap.created_at < sqlite_cutoff)
            .delete(synchronize_session=False)
        )
        store.commit()
        return deleted
    finally:
        _close_store(store)


def cmd_purge_gaps(days: int) -> None:
    print(f"[purge-gaps] {purge_gaps(days)}건 파기(보존 {days}일).")


def reset_face(username: str) -> str:
    """등록된 얼굴을 지운다 — **잠금 복구 수단**.

    ★★왜 필요한가 — 얼굴 2FA 는 **되돌릴 수 없는 잠금**이 될 수 있다.

        얼굴을 등록하면 다음 로그인부터 반드시 얼굴이 필요하다. 그런데
        카메라가 없는 PC 로 옮기거나, 조명·외모가 바뀌어 임계값을 못 넘거나,
        남의 얼굴로 잘못 등록하면 **그 계정으로는 영영 못 들어간다.**
        해제하려면 로그인해야 하고, 로그인하려면 얼굴이 필요하다.

        실제로 겪었다(2026-08-04): 검증용으로 등록한 샘플 얼굴 때문에
        `demo_admin` 로그인이 2차 인증에서 멈췄다. DB 를 직접 건드려 풀었는데,
        **그건 운영 절차가 아니라 응급처치**다. 명령으로 만들어 둔다.

    ★비밀번호는 건드리지 않는다. 얼굴만 지운다 — 지운 뒤에는
      비밀번호만으로 로그인되고, 원하면 다시 등록하면 된다.
    """
    from app.core.errors import NotFoundErr
    from app.obs.events import record_event
    from db.postgres.auth_repository import PgAuthStore

    db = _open_auth_store()
    try:
        if isinstance(db, PgAuthStore):
            user = db.get_user(username)
            if user is None:
                raise NotFoundErr(f"사용자를 찾을 수 없습니다: {username}")
            n = 1 if db.get_face(user.id) is not None else 0
            if n:
                db.delete_face(user.id)
                record_event(db, "face_reset", {"username": username, "by": "cli"})
            return (
                f"얼굴 등록 해제: {username} (이제 비밀번호로 로그인)"
                if n
                else f"등록된 얼굴이 없습니다: {username}"
            )

        from db.sqlite_legacy.models import FaceCredential, User

        user = db.query(User).filter(User.username == username).first()
        if user is None:
            raise NotFoundErr(f"사용자를 찾을 수 없습니다: {username}")
        n = db.query(FaceCredential).filter(FaceCredential.user_id == user.id).delete()
        db.commit()
        if n == 0:
            return f"등록된 얼굴이 없습니다: {username}"
        #: 감사 기록 — 인증 수단을 없앤 것은 반드시 남는다.
        record_event(db, "face_reset", {"username": username, "by": "cli"})
        return f"얼굴 등록 해제: {username} (이제 비밀번호로 로그인)"
    finally:
        _close_store(db)


def cmd_face_reset(username: str) -> None:
    print(f"[face-reset] {reset_face(username)}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="운영 관리 명령")
    parser.add_argument(
        "command",
        choices=["migrate", "ready", "promote", "demote", "face-reset",
                 "purge-gaps"],
    )
    parser.add_argument("target", nargs="?",
                        help="promote/demote/face-reset 의 username")
    parser.add_argument("--days", type=int, default=90, help="purge-gaps 보존기간(일)")
    args = parser.parse_args(argv)

    if args.command in ("promote", "demote", "face-reset"):
        if not args.target:
            parser.error(f"{args.command}에는 username이 필요합니다.")
        {"promote": cmd_promote, "demote": cmd_demote,
         "face-reset": cmd_face_reset}[args.command](args.target)
        return
    if args.command == "purge-gaps":
        cmd_purge_gaps(args.days)
        return
    {"migrate": cmd_migrate, "ready": cmd_ready}[
        args.command
    ]()


if __name__ == "__main__":
    main()
