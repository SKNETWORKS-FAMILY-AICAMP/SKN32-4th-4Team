"""Phase 9 — RBAC / 관리자 / 지식보강 큐.

TEST-RBAC-401/403/200, TEST-RBAC-STALE-001, TEST-ADMIN-BOOTSTRAP-001,
TEST-MIGRATE-ROLE-001, TEST-KGAP-001, 관리자 라우터 fail-closed 가드레일.
"""

from __future__ import annotations

import pytest

from app.auth.roles import ROLE_ADMIN, ROLE_USER, validate_role
from app.core.errors import InfraError, NotFoundErr, ValidationErr
from tests.conftest import auth_header

#: ★`/api/admin/orders` 는 제거했다(커머스 잔재, v4.0 §2-3). 대신 보험 대시보드가
#:   실제로 쓰는 경로를 넣는다 — 목록이 낡으면 새 엔드포인트의 권한 검사를 놓친다.
_ADMIN_PATHS = ["/api/admin/agents", "/api/admin/events", "/api/admin/index",
                "/api/admin/knowledge-gaps", "/api/admin/demo/queue",
                "/api/admin/cohort-summary"]


@pytest.fixture
def client():
    """이 파일은 보험 운영 API 계약을 검증하므로 admin 앱을 명시해 사용한다."""
    from fastapi.testclient import TestClient

    from app.main import admin_app

    return TestClient(admin_app)


def _set_role(username: str, role: str) -> None:
    from app.db.database import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == username).first()
        u.role = role
        db.commit()
    finally:
        db.close()

# --- 역할 검증(무폴백) -----------------------------------------------------
def test_unknown_role_is_rejected_not_defaulted():
    assert validate_role(ROLE_USER) == ROLE_USER
    assert validate_role(ROLE_ADMIN) == ROLE_ADMIN
    for bad in ("root", "", None, "admin"):  # 소문자도 허용 안 함
        with pytest.raises(InfraError):
            validate_role(bad)


# --- TEST-RBAC-401/403/200 -------------------------------------------------
@pytest.mark.parametrize("path", _ADMIN_PATHS)
def test_rbac_401_without_token(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", _ADMIN_PATHS)
def test_rbac_403_for_normal_user(client, unique_user, path):
    u, p = unique_user()
    headers = auth_header(client, u, p)  # 가입 기본 role = USER
    assert client.get(path, headers=headers).status_code == 403


@pytest.mark.parametrize("path", _ADMIN_PATHS)
def test_rbac_200_for_admin(client, unique_user, path):
    u, p = unique_user()
    headers = auth_header(client, u, p)
    _set_role(u, ROLE_ADMIN)
    assert client.get(path, headers=headers).status_code == 200


def test_new_user_defaults_to_user_role(client, unique_user):
    u, p = unique_user()
    auth_header(client, u, p)
    from app.db.database import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        assert db.query(User).filter(User.username == u).first().role == ROLE_USER
    finally:
        db.close()


# --- TEST-RBAC-STALE-001: 토큰에 role을 넣지 않은 설계의 핵심 이점 ----------
def test_demotion_takes_effect_immediately_with_same_token(client, unique_user):
    """토큰 재발급 없이도 강등이 즉시 반영된다(role이 토큰에 없기 때문)."""
    u, p = unique_user()
    headers = auth_header(client, u, p)
    _set_role(u, ROLE_ADMIN)
    assert client.get("/api/admin/agents", headers=headers).status_code == 200

    _set_role(u, ROLE_USER)  # 같은 토큰 그대로 사용
    assert client.get("/api/admin/agents", headers=headers).status_code == 403


# --- 관리자 라우터 fail-closed 가드레일 ------------------------------------
def test_every_admin_route_is_fail_closed():
    """`/api/admin/*` 모든 라우트가 require_admin 의존성을 갖는지 정적 확인.

    엔드포인트마다 수동으로 붙이는 방식은 새 엔드포인트에서 누락된다 → 라우터 전역
    의존성을 쓰고, 그 사실을 여기서 고정한다.
    """
    from app.auth.roles import require_admin
    from app.routers.admin import router

    assert router.dependencies, "관리자 라우터에 전역 의존성이 없습니다"
    dep_calls = [d.dependency for d in router.dependencies]
    assert require_admin in dep_calls
    for route in router.routes:
        assert route.path.startswith("/api/admin"), f"관리자 prefix 밖 라우트: {route.path}"


# --- TEST-ADMIN-BOOTSTRAP-001 ----------------------------------------------
def test_promote_demote_cli_is_explicit_and_idempotent(client, unique_user):
    from scripts.manage import set_role

    u, p = unique_user()
    auth_header(client, u, p)

    assert "USER → ADMIN" in set_role(u, ROLE_ADMIN)
    assert "변경 없음" in set_role(u, ROLE_ADMIN)  # 멱등
    assert "ADMIN → USER" in set_role(u, ROLE_USER)


def test_promote_unknown_user_raises():
    from scripts.manage import set_role

    with pytest.raises(NotFoundErr):
        set_role("존재하지_않는_사용자", ROLE_ADMIN)


def test_cannot_demote_last_admin(client, unique_user):
    """마지막 관리자를 강등하면 잠금(lockout)이 되므로 거부한다."""
    from app.db.database import SessionLocal
    from app.db.models import User
    from scripts.manage import set_role

    # 기존 ADMIN을 모두 USER로 내려 깨끗한 상태를 만든다
    db = SessionLocal()
    try:
        for admin in db.query(User).filter(User.role == ROLE_ADMIN).all():
            admin.role = ROLE_USER
        db.commit()
    finally:
        db.close()

    u, p = unique_user()
    auth_header(client, u, p)
    set_role(u, ROLE_ADMIN)  # 유일한 관리자
    with pytest.raises(ValidationErr):
        set_role(u, ROLE_USER)
    # 강등이 거부됐으므로 여전히 ADMIN이어야 한다(상태가 어중간하게 바뀌지 않음)
    db = SessionLocal()
    try:
        assert db.query(User).filter(User.username == u).first().role == ROLE_ADMIN
    finally:
        db.close()


# --- TEST-MIGRATE-ROLE-001 -------------------------------------------------
def test_migrate_adds_role_column_idempotently(tmp_path):
    """create_all은 기존 테이블에 컬럼을 추가하지 않는다 → 명시적 추가가 동작해야 한다."""
    from sqlalchemy import create_engine, inspect, text

    from scripts.manage import _add_missing_columns, _verify_schema

    db_file = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:  # role 없는 '구' 스키마를 만든다
        conn.execute(
            text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(50), "
                "hashed_password VARCHAR(255))"
            )
        )
        conn.execute(text("INSERT INTO users (username, hashed_password) VALUES ('old','x')"))

    assert "role" not in {c["name"] for c in inspect(engine).get_columns("users")}
    assert _add_missing_columns(engine) == ["users.role"]
    _verify_schema(engine)  # 사후 검증 통과해야 함

    with engine.begin() as conn:
        assert conn.execute(text("SELECT role FROM users")).scalar() == "USER"  # 기존 행 = USER
    assert _add_missing_columns(engine) == []  # 두 번째 호출은 멱등


# --- TEST-KGAP-001: 지식보강 큐 --------------------------------------------
def test_pii_is_masked_before_storing():
    from app.obs.pii import mask_pii

    text = "제 이메일 hong@example.com 이고 010-1234-5678 입니다. 주문 OA1B2C3D4E5F 확인해주세요"
    masked = mask_pii(text)
    assert "hong@example.com" not in masked and "[EMAIL]" in masked
    assert "010-1234-5678" not in masked and "[PHONE]" in masked
    assert "OA1B2C3D4E5F" not in masked and "[ORDER_NO]" in masked


def test_knowledge_gap_can_only_be_created_through_masking_path():
    """구조적 강제: `KnowledgeGap(...)` 생성은 마스킹을 하는 모듈에서만 일어나야 한다.

    마스킹 함수가 있어도 우회 생성이 가능하면 무의미하다(Codex 지적) → 정적 스캔으로 고정.
    """
    import pathlib
    import re as _re

    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    allowed = {"knowledge_gaps.py", "models.py"}  # 정의/적재 지점만 허용
    offenders = []
    for py in app_dir.rglob("*.py"):
        if py.name in allowed:
            continue
        if _re.search(r"\bKnowledgeGap\s*\(", py.read_text(encoding="utf-8")):
            offenders.append(str(py.relative_to(app_dir)))
    assert offenders == [], f"마스킹을 우회한 KnowledgeGap 생성: {offenders}"


def test_admin_report_requires_admin_and_returns_pdf(client, unique_user):
    """요약보고서: 미인증 401, 일반 403, 관리자는 PDF(200). 폰트 없는 비Windows는 503 허용."""
    assert client.get("/api/admin/report").status_code == 401  # 미인증
    u, p = unique_user()
    headers = auth_header(client, u, p)
    assert client.get("/api/admin/report", headers=headers).status_code == 403  # 일반 사용자
    _set_role(u, ROLE_ADMIN)
    r = client.get("/api/admin/report", headers=headers)
    assert r.status_code in (200, 503)  # 503 = 한글 폰트 미설치(무폴백 ConfigError)
    if r.status_code == 200:
        assert r.headers["content-type"] == "application/pdf"
        assert r.content[:4] == b"%PDF"


def test_admin_index_exposes_only_allowlisted_fields(client, unique_user):
    u, p = unique_user()
    headers = auth_header(client, u, p)
    _set_role(u, ROLE_ADMIN)
    body = client.get("/api/admin/index", headers=headers).json()
    assert set(body) <= {"ready", "db_tables_ready", "clause_index_ready", "missing_tables"}
    assert "hint" not in body  # 내부 경로·명령이 담긴 필드는 노출하지 않는다


def test_masks_international_phone_and_lowercase_order_no():
    from app.obs.pii import mask_pii

    assert "[PHONE]" in mask_pii("연락처는 +82 10 1234 5678 입니다")
    assert "[ORDER_NO]" in mask_pii("주문 oa1b2c3d4e5f 확인")


def test_card_regex_does_not_partially_mask_longer_digit_runs():
    """17자리 이상 숫자열의 앞 16자리만 [CARD]로 치환되면 나머지가 노출된다."""
    from app.obs.pii import mask_pii

    long_run = "1234 5678 9012 3456 789"  # 19자리
    assert "[CARD]" not in mask_pii(long_run)
    assert "[CARD]" in mask_pii("카드 1234 5678 9012 3456 입니다")  # 정상 16자리는 마스킹


def test_events_endpoint_masks_on_output_and_flags_anomaly(client, unique_user):
    """`/api/admin/events`도 knowledge-gaps와 같은 결함이 있었다(전수 재점검으로 발견).

    detail이 "요약만" 관례를 어기고 원문 PII를 담고 있으면, 응답은 안전하게 가리되
    조용히 덮지 않고 감사기록(run_event_unmasked_detected)을 남겨야 한다.
    """
    from app.db.database import SessionLocal
    from app.db.models import RunEvent

    db = SessionLocal()
    try:  # record_event를 거치지 않고 직접 삽입(관례 위반 재현)
        leaked = db.execute(
            RunEvent.__table__.insert().values(
                trace_id="t9", kind="test_kind", detail="원문 leak2@example.com 남음"
            )
        )
        db.commit()
        event_id = leaked.inserted_primary_key[0]
    finally:
        db.close()

    u, p = unique_user()
    headers = auth_header(client, u, p)
    _set_role(u, ROLE_ADMIN)
    body = client.get("/api/admin/events", headers=headers).json()

    assert all("leak2@example.com" not in row["detail"] for row in body)  # (1) 안전

    db = SessionLocal()
    try:  # (2) 정직: 감사 이벤트로 남는다
        events = db.query(RunEvent).filter(RunEvent.kind == "run_event_unmasked_detected").all()
        assert any(f'"event_id": {event_id}' in e.detail for e in events)
    finally:
        db.close()


def test_knowledge_gaps_endpoint_masks_on_output_and_flags_anomaly(client, unique_user):
    """저장 시 마스킹을 우회한 데이터가 있으면 (1) 출력은 안전하게 가리되 (2) 조용히
    덮지 않고 감사기록(run_events)을 남긴다 — 무조건 재마스킹만 하면 그 자체가
    "이상 상태를 조용히 고치는" 폴백이 되므로, 발견 사실을 반드시 신호로 남겨야 한다."""
    from app.db.database import SessionLocal
    from app.db.models import KnowledgeGap as KG
    from app.db.models import RunEvent

    db = SessionLocal()
    try:  # 마스킹을 거치지 않고 직접 삽입(우회 상황 재현)
        leaked = db.execute(
            KG.__table__.insert().values(question="원문 leak@example.com 남음", trace_id="t1")
        )
        db.commit()
        gap_id = leaked.inserted_primary_key[0]
        events_before = db.query(RunEvent).filter(RunEvent.kind == "kgap_unmasked_detected").count()
    finally:
        db.close()

    u, p = unique_user()
    headers = auth_header(client, u, p)
    _set_role(u, ROLE_ADMIN)
    body = client.get("/api/admin/knowledge-gaps", headers=headers).json()

    # (1) 안전: API 응답에 원문이 없다
    assert all("leak@example.com" not in row["question"] for row in body)

    # (2) 정직: 조용히 고쳐 넘어가지 않고 감사 이벤트로 남긴다
    db = SessionLocal()
    try:
        events_after = db.query(RunEvent).filter(RunEvent.kind == "kgap_unmasked_detected").all()
        assert len(events_after) == events_before + 1
        assert f'"gap_id": {gap_id}' in events_after[-1].detail
    finally:
        db.close()


def test_properly_masked_gap_does_not_trigger_anomaly_event(client, unique_user):
    """정상적으로 마스킹돼 저장된 데이터는 이상 신호를 만들지 않는다(노이즈 방지).

    다른 테스트가 남긴 마스킹-안 된 행이 DB에 계속 남아 조회 때마다 계속 이벤트를 내는
    것은 **의도된 동작**(고치지 않고 방치되면 볼 때마다 알림)이라 전역 카운트로는 격리가
    안 된다 — 이 테스트가 만든 gap_id를 특정해 검증한다.
    """
    from app.db.database import SessionLocal
    from app.db.models import KnowledgeGap as KG
    from app.db.models import RunEvent
    from app.obs.pii import mask_pii

    db = SessionLocal()
    try:
        row = KG(question=mask_pii("hong@example.com 관련 질문"), trace_id="t2")
        db.add(row)
        db.commit()
        gap_id = row.id
    finally:
        db.close()

    u, p = unique_user()
    headers = auth_header(client, u, p)
    _set_role(u, ROLE_ADMIN)
    client.get("/api/admin/knowledge-gaps", headers=headers)

    db = SessionLocal()
    try:
        events = db.query(RunEvent).filter(RunEvent.kind == "kgap_unmasked_detected").all()
        assert not any(f'"gap_id": {gap_id}' in e.detail for e in events)
    finally:
        db.close()
