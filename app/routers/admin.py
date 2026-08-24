"""관리자 라우터 (Phase 9) — **전부 ADMIN 전용**.

fail-closed 설계: 권한 검사를 엔드포인트마다 붙이면 새 엔드포인트에서 빠뜨리기 쉽다.
그래서 `APIRouter(dependencies=[Depends(require_admin)])`로 **라우터 단위**로 강제하고,
`/api/admin/*` 전 라우트가 이 의존성을 갖는지 가드레일 테스트로 고정한다.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.roles import require_admin
from app.auth.store import get_auth_store
from app.core.config import get_settings
from app.db.models import KnowledgeGap, RunEvent
from app.obs.store import get_ops_store
from db.postgres.ops_repository import PgOpsStore

# 라우터 전역 fail-closed — 여기 추가되는 모든 엔드포인트가 자동으로 ADMIN 전용이 된다.
router = APIRouter(
    prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)

#: ★`/orders` 는 제거했다(2026-08-04). 커머스 잔재이고 v4.0 §2-3 폐기 대상이다.
#:   화면에서만 숨기면 죽은 엔드포인트가 남아 다음 사람이 또 판단해야 한다.
#:   보관본은 `legacy/v3_commerce.zip`.


class DemoVerifyRequest(BaseModel):
    """합성 제출 승격 요청.

    ★`verification_method` 를 클라이언트가 고르지 못하게 한다 —
      이 경로로 들어온 것은 정의상 `admin_review` 다. 고르게 하면
      화면에서 `simulated` 를 눌러 놓고 "관리자가 검수했다"고 말할 수 있다.
    """

    submission_id: str = Field(min_length=1)


@router.get("/events")
def admin_list_events(
    store: Session | PgOpsStore = Depends(get_ops_store),
    trace_id: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    """관측 이벤트(run_events) 조회 — trace 상관 추적용.

    detail은 "요약만, 원문·PII 금지" 관례(RunEvent 독스트링)지만 자유 문자열이라 그 관례가
    깨질 수 있다. knowledge-gaps와 같은 이유로, 마스킹이 실제로 값을 바꾸면(=관례 위반 증거)
    응답은 안전하게 가리되 조용히 덮지 않고 감사기록을 남긴다(RULE.md 무폴백).
    """
    if isinstance(store, PgOpsStore):
        rows = store.list_events(trace_id=trace_id, kind=kind, limit=limit)
    else:
        q = store.query(RunEvent)
        if trace_id:
            q = q.filter(RunEvent.trace_id == trace_id)
        if kind:
            q = q.filter(RunEvent.kind == kind)
        rows = q.order_by(RunEvent.id.desc()).limit(limit).all()

    from app.obs.events import record_event
    from app.obs.pii import mask_pii

    out = []
    for e in rows:
        raw_detail = e.detail if isinstance(e.detail, str) else json.dumps(
            e.detail, ensure_ascii=False, sort_keys=True
        )
        masked = mask_pii(raw_detail)
        if masked != raw_detail:
            # detail 요약-only 불변식 위반 탐지 — 조용히 고치고 넘어가지 않고 신호를 남긴다.
                record_event(store, "run_event_unmasked_detected", {"event_id": e.id})
        out.append({"id": e.id, "trace_id": e.trace_id, "kind": e.kind, "detail": masked})
    return out


#: /index가 노출할 필드 화이트리스트. check_readiness()를 그대로 흘리면 내부 경로·구성이
#: 과다 노출될 수 있다(Codex 지적) → 명시한 것만 내보낸다.
_INDEX_FIELDS = ("ready", "db_tables_ready", "clause_index_ready", "missing_tables")


@router.get("/index")
def admin_index_status() -> dict:
    """보험 조항 인덱스·DB 준비 상태(읽기 전용, 허용 필드만)."""
    from app.obs.readiness import check_readiness

    status = check_readiness()
    return {k: status[k] for k in _INDEX_FIELDS if k in status}


@router.get("/report")
def admin_report(store: Session | PgOpsStore = Depends(get_ops_store)) -> Response:
    """현재 대시보드 데이터(준비상태·코호트·이벤트·지식갭)를 요약한 PDF 보고서.

    서버에도 `docs/generated_reports/`에 스냅샷을 저장하고, 같은 PDF를 다운로드로 반환한다.
    라우터 전역 require_admin으로 보호됨.
    """
    from datetime import datetime

    from app.services.admin_report import build_admin_report_pdf, save_admin_report

    now = datetime.now()
    save_admin_report(store)  # 서버 보관용 스냅샷
    pdf = build_admin_report_pdf(store, generated_at=now)
    filename = f"admin_report_{now.strftime('%Y%m%d_%H%M%S')}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/knowledge-gaps")
def admin_list_knowledge_gaps(
    store: Session | PgOpsStore = Depends(get_ops_store),
    resolved: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    """지식보강 큐 — 근거 없어 답하지 못한 질문(PII 마스킹 후 저장된 형태).

    출력 시 다시 mask_pii를 거는 것은 "혹시 몰라서 조용히 덮는" 폴백이 되면 안 된다
    (RULE.md 무폴백 — 이상 상태를 발견하면 침묵하지 말 것). 그래서 마스킹 전/후 값이
    다르면 **저장 시 마스킹이 뚫렸다는 뜻**이므로, 응답은 안전하게 가리되(PII 미유출)
    그 사실을 run_events에 감사기록으로 남겨 조용히 덮지 않는다.
    """
    from app.obs.events import record_event
    from app.obs.pii import mask_pii

    if isinstance(store, PgOpsStore):
        rows = store.list_knowledge_gaps(resolved=resolved, limit=limit)
    else:
        q = store.query(KnowledgeGap)
        if resolved is not None:
            q = q.filter(KnowledgeGap.resolved == resolved)
        rows = q.order_by(KnowledgeGap.id.desc()).limit(limit).all()

    out = []
    for g in rows:
        masked = mask_pii(g.question)
        if masked != g.question:
            # 쓰기 경로 불변식 위반 탐지 — 조용히 고치고 넘어가지 않고 신호를 남긴다.
            record_event(store, "kgap_unmasked_detected", {"gap_id": g.id})
        out.append(
            {"id": g.id, "question": masked, "trace_id": g.trace_id, "resolved": g.resolved}
        )
    return out


#: SSE 꼬리읽기 주기. 화면 체감(≤0.5초)과 파일 stat 비용 사이의 절충.
_STREAM_POLL_S = 0.4


# ── 에이전트 관측 ─────────────────────────────────────────────────────────
#
# ★"접속 중"이라고 말하지 않는다. HTTP 는 연결을 유지하지 않으므로 우리가 아는 것은
#   **마지막 요청이 언제였나** 뿐이다. 화면에도 `idle_s` 로 표시한다.


@router.get("/agents")
def admin_agents() -> dict:
    """붙어 있는 에이전트 요약 + 최근 상호작용.

    ★출처를 밝힌다 — 이 목록은 **인메모리 링버퍼**라 재시작하면 사라진다.
      "기록이 없다"가 "그런 일이 없었다"는 뜻이 아니다(감사는 `run_events`).
    """
    from app.obs import agent_stream

    return {
        "agents": agent_stream.agents(),
        "recent": agent_stream.recent(limit=50),
        "volatile": True,
        "note": "관측용 append-only 파일입니다(고객·운영 두 프로세스가 공유). "
                "감사 기록은 run_events 입니다 — 여기 없다고 그런 일이 없었던 것은 아닙니다.",
    }


@router.get("/agents/stream")
async def admin_agents_stream() -> StreamingResponse:
    """에이전트 상호작용 SSE. 대시보드 타임라인이 이걸 구독한다.

    ★**파일 꼬리읽기**다. 고객 서버(8080)와 운영 서버(8081)는 다른 프로세스라
      인메모리 큐로는 고객 쪽 트래픽이 대시보드에 안 보인다(실측으로 잡았다).
    """
    from app.obs import agent_stream

    async def gen():
        #: 붙자마자 최근 것을 한 번 밀어준다(빈 화면으로 시작하지 않게).
        yield agent_stream.sse({"kind": "_snapshot", "items": agent_stream.recent(30)})
        offset = agent_stream.current_offset()
        idle = 0.0
        while True:
            await asyncio.sleep(_STREAM_POLL_S)
            offset, events = agent_stream.read_from(offset)
            if events:
                idle = 0.0
                for ev in events:
                    yield agent_stream.sse(ev)
                continue
            idle += _STREAM_POLL_S
            if idle >= 15:
                idle = 0.0
                #: ★주석 프레임으로 연결을 살려 둔다. 프록시가 유휴 연결을 끊는다.
                yield ": keep-alive\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 합성(데모) 트랙 검수 ──────────────────────────────────────────────────
#
# ★★경로에 `demo` 가 들어가는 것이 **의도**다. 실제 트랙에는 이 API 가 없다.
#   실제 사례의 승격은 증빙 검수 절차가 정해진 뒤에 별도로 만든다 —
#   지금 만들면 "관리자가 눌렀다"가 "검증됐다"로 둔갑한다.


@router.get("/demo/queue")
def admin_demo_queue(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    """합성 트랙 검수 대기 목록. **승격되지 않은 것만** 나온다."""
    from app.adapters import demo_submission_store as demo

    return {
        "data_source": "synthetic",
        "counts": demo.counts(),
        "pending": demo.pending(limit=limit),
    }


@router.post("/demo/verifications", status_code=status.HTTP_201_CREATED)
def admin_demo_verify(body: DemoVerifyRequest, user=Depends(require_admin)) -> dict:
    """합성 제출 하나를 승격한다 → 이 순간 합성 코호트 `n` 이 +1 된다.

    ★승격 방법을 응답에 그대로 실어 보낸다(`verification_method`).
      이 숫자가 어떻게 생겼는지 나중에 설명할 수 있어야 한다.
    """
    from app.adapters import demo_submission_store as demo
    from app.obs import agent_stream

    event = demo.promote(
        body.submission_id, method=demo.METHOD_ADMIN, actor=getattr(user, "username", "admin")
    )
    agent_stream.publish(
        "admin.verify",
        client_ref=str(event.get("client_ref") or "-"),
        track="synthetic",
        detail={"submission_id": event["submission_id"], "outcome": event.get("outcome"),
                "method": event["verification_method"]},
    )
    return {"promoted": True, "event": event, "counts": demo.counts()}


# ── 사용자·권한 관리 ──────────────────────────────────────────────────────
#
# ★★**"관리자 가입" 폼은 만들지 않는다.**
#
#   누구나 가입해서 스스로 관리자가 되는 화면은 권한 상승 그 자체다.
#   그래서 **최초 관리자 1명의 부트스트랩은 CLI 로만** 한다
#   (`python -m scripts.manage promote <username>`).
#
#   다만 그 뒤까지 CLI 로만 두는 것은 과했다 — 팀원을 추가하려면 매번
#   서버에 붙어야 했다. **이미 관리자인 사람이 다른 사람을 올리는 것**은
#   정상 운영이므로 화면에서 할 수 있게 한다. 규칙(마지막 관리자 강등 금지·감사)은
#   `app.auth.roles.change_role` 한 곳에 있고 CLI 도 같은 함수를 쓴다.


class RoleChangeRequest(BaseModel):
    role: str = Field(description="ADMIN 또는 USER")


@router.get("/users")
def admin_list_users(store=Depends(get_auth_store)) -> dict:
    """계정 목록(요약 필드만). **비밀번호 해시는 절대 내보내지 않는다.**"""
    if get_settings().AUTH_PERSISTENCE == "postgres":
        from db.postgres.auth_repository import PgAuthStore

        store = PgAuthStore.from_settings()
        rows = store.list_users()
        face_ids = {str(user.id) for user in rows if store.get_face(user.id) is not None}
        admins = sum(1 for user in rows if user.role == "ADMIN")
        return {
            "users": [
                {
                    "id": user.id,
                    "username": user.username,
                    "role": user.role,
                    "face_registered": str(user.id) in face_ids,
                }
                for user in rows
            ],
            "admin_count": admins,
            "note": "최초 관리자 부트스트랩은 CLI 전용입니다: python -m scripts.manage promote <username>",
        }
    db = store
    from app.db.models import FaceCredential, User

    rows = db.query(User).order_by(User.id).all()
    face_ids = {c.user_id for c in db.query(FaceCredential).all()}
    admins = sum(1 for u in rows if u.role == "ADMIN")
    return {
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "face_registered": u.id in face_ids,
            }
            for u in rows
        ],
        "admin_count": admins,
        #: ★화면이 "마지막 관리자"를 미리 알아야 강등 버튼을 잠글 수 있다.
        #:   서버도 거부하지만, 눌러 보고 실패하는 것보다 못 누르게 하는 게 낫다.
        "note": "최초 관리자 부트스트랩은 CLI 전용입니다: python -m scripts.manage promote <username>",
    }


@router.put("/users/{username}/role")
def admin_change_user_role(
    username: str,
    body: RoleChangeRequest,
    store=Depends(get_auth_store),
    user=Depends(require_admin),
) -> dict:
    """다른 계정의 역할을 바꾼다. **관리자만** 부를 수 있다(라우터 전역 게이트).

    ★마지막 관리자 강등은 거부된다(잠금 방지) — 규칙은 도메인에 있다.
    """
    from app.auth.roles import change_role
    from app.obs import agent_stream

    actor = getattr(user, "username", "admin")
    result = change_role(store, username, body.role.strip().upper(), actor=actor)
    if result["changed"]:
        agent_stream.publish(
            "role.change", client_ref=actor,
            detail={"username": username, "to": result["role"],
                    "from": result.get("from")},
        )
    return result


# ── 판정 모드(문서 확정 게이트) ───────────────────────────────────────────
#
# ★이 스위치는 **어떤 약관으로 판정할지**를 바꾼다. 시뮬레이션 제어와 성격이 완전히 다르다 —
#   저쪽은 합성 데이터를 만들 뿐이지만, 이쪽은 **고객이 받는 답**을 바꾼다.
#   그래서 바꾼 사람·시각을 파일에 남기고, 감사 로그와 관측 스트림에 모두 알린다.


class PrecheckModeRequest(BaseModel):
    """`true` 면 기계 대조까지만 끝난 문서도 판정에 쓴다(시연)."""

    auto_approve: bool


@router.get("/precheck-mode")
def admin_precheck_mode() -> dict:
    from app.core.domain import identification_mode
    from app.routers import precheck as precheck_router

    state = identification_mode.current().as_dict()
    #: ★모드만 보여주면 "켜면 몇 건이 되는지"를 모른다. 판단에 필요한 수를 함께 낸다.
    state["stats"] = precheck_router._confirmation_stats()
    state["usable_now"] = len(precheck_router._versions())
    return state


@router.put("/precheck-mode")
def admin_set_precheck_mode(
    body: PrecheckModeRequest,
    store: Session | PgOpsStore = Depends(get_ops_store),
    user=Depends(require_admin),
) -> dict:
    """판정 모드를 바꾼다. **캐시를 반드시 비운다.**

    ★표시와 동작이 어긋나는 것을 막는 자리다 — 화면에는 "엄격"이라 적혀 있는데
      판정은 캐시된 자동승인 목록으로 계속 나가면 그게 최악이다.
    """
    from app.core.domain import identification_mode
    from app.obs import agent_stream
    from app.obs.events import record_event
    from app.routers import precheck as precheck_router

    actor = getattr(user, "username", "admin")
    mode = (identification_mode.MACHINE_MATCH if body.auto_approve
            else identification_mode.HUMAN_SIGNOFF)
    state = identification_mode.set_mode(mode, actor=actor)
    precheck_router.invalidate_versions_cache()

    #: 감사 기록(DB)과 관측 스트림(화면) 양쪽에 남긴다 — 성격이 다른 두 독자다.
    record_event(store, "precheck_mode_changed", {"mode": state.mode, "by": actor})
    agent_stream.publish("mode.change", client_ref=actor,
                         detail={"mode": state.mode, "auto_approve": state.auto_approve})

    out = state.as_dict()
    out["usable_now"] = len(precheck_router._versions())
    return out


# ── 실제 트랙 증빙 검수 ───────────────────────────────────────────────────
#
# ★★**이름을 `verified` 라고 하지 않는다.**
#
#   부트캠프 범위에서 발행처 API 확인은 불가능하다. 관리자가 할 수 있는 것은
#   **교차검증**뿐이고, 그것을 `verified` 라 부르면 "보험사가 그렇게 결정했음이
#   확인됐다"는 뜻이 되어 버린다. 실제로 확인된 것은 "관리자가 보고 납득했다"이다.
#   그래서 등급 이름을 `admin_attested` 로 둔다 — 계획서 §3 이 지적한
#   "정합성 검증을 사실성 검증으로 부르지 않는다"와 같은 계열이다.


class RealVerifyRequest(BaseModel):
    submission_id: str = Field(min_length=1)
    #: ★관리자가 무엇을 근거로 납득했는지. 비워 둘 수 없다 —
    #:   근거 없는 승격은 나중에 설명할 수 없다.
    basis: str = Field(min_length=5)


@router.get("/verifications/queue")
def admin_real_queue(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    """실제 트랙 검수 대기 목록."""
    from app.core.config import get_settings

    if get_settings().OUTCOME_PERSISTENCE == "postgres":
        from db.postgres.pg_insurance_repository import PgInsuranceAdminRepository
        from app.core.usecases.verify_evidence import pending

        rows = pending(limit=limit, repository=PgInsuranceAdminRepository.from_settings())
        return {
            "data_source": "verified_real",
            "counts": {"pending": len(rows)},
            "pending": rows,
            "note": "PostgreSQL 원장 증빙입니다. 관리자 교차검증 전에는 코호트에 반영되지 않습니다.",
        }

    from app.adapters import external_submission_store as store

    return {
        "data_source": "verified_real",
        "counts": store.counts(),
        "pending": store.pending(limit=limit),
        "note": "관리자 교차검증으로만 승격됩니다(admin_attested). 발행처 확인이 아닙니다.",
    }


@router.post("/verifications", status_code=status.HTTP_201_CREATED)
def admin_real_verify(body: RealVerifyRequest, user=Depends(require_admin)) -> dict:
    """실제 제보 하나를 **관리자 교차검증**으로 승격한다.

    ★승격해도 등급은 `admin_attested` 다. 코호트 응답은 이 등급의 건수를
      따로 세어 보여준다 — "n=5" 를 "5건 발행처 확인됨"으로 읽지 않게.
    """
    from app.obs import agent_stream

    from app.core.config import get_settings

    if get_settings().OUTCOME_PERSISTENCE == "postgres":
        from dataclasses import asdict

        from db.postgres.pg_insurance_repository import PgInsuranceAdminRepository
        from app.core.usecases.verify_evidence import attest

        result = attest(
            submission_id=body.submission_id,
            basis=body.basis,
            admin_login=getattr(user, "username", ""),
            repository=PgInsuranceAdminRepository.from_settings(),
        )
        event = asdict(result)
        agent_stream.publish(
            "admin.attest",
            client_ref="-",
            track="verified_real",
            detail={
                "submission_id": result.submission_id,
                "outcome": result.decision,
                "method": result.verification_method,
            },
        )
        return {"attested": True, "event": event, "counts": {}}

    from app.adapters import external_submission_store as store

    event = store.attest(
        body.submission_id, basis=body.basis, actor=getattr(user, "username", "admin")
    )
    agent_stream.publish(
        "admin.attest", client_ref=str(event.get("client_ref") or "-"),
        track="verified_real",
        detail={"submission_id": event["submission_id"], "outcome": event.get("outcome"),
                "method": event["verification_method"]},
    )
    return {"attested": True, "event": event, "counts": store.counts()}


# ── 시뮬레이션 제어 ───────────────────────────────────────────────────────
#
# ★경로에 `demo` 가 들어간다. 이 제어가 만들 수 있는 것은 **합성 데이터뿐**이다.
#   실제 트랙을 만들거나 지우는 버튼은 없다 — 있으면 언젠가 눌린다.


class SimulationStartRequest(BaseModel):
    """시뮬레이션 파라미터. 상한은 어댑터가 검증한다(화면·CLI 가 같은 규칙을 쓰도록)."""

    agents: int = Field(default=12, ge=1)
    cases: int = Field(default=3, ge=1)
    #: 비우면 8종 무작위. 좁히면 한 코호트에 표본이 몰려 최소표본 게이트를 넘는 장면을 만든다.
    codes: list[str] = Field(default_factory=list)
    delay_ms: int = Field(default=150, ge=0, le=5000)
    auto_verify: bool = False
    #: ★고정 시드. 발표 때마다 숫자가 달라지면 "그때 그 화면"을 다시 못 만든다.
    seed: int = 20260804
    #: 비우면 설정값(`CUSTOMER_BASE_URL`). 가상 에이전트는 **실제 HTTP 로** 붙는다.
    base: str = ""


@router.get("/demo/simulation")
def admin_simulation_status() -> dict:
    from app.adapters import demo_simulator

    return demo_simulator.status()


@router.post("/demo/simulation")
def admin_simulation_start(body: SimulationStartRequest) -> dict:
    """시작. 이미 실행 중이면 409 — 조용히 덮어쓰지 않는다."""
    from app.adapters import demo_simulator
    from app.core.config import get_settings
    from app.obs import agent_stream

    base = body.base.strip() or get_settings().CUSTOMER_BASE_URL
    state = demo_simulator.start(
        base=base, agents=body.agents, cases=body.cases, codes=body.codes,
        delay_ms=body.delay_ms, auto_verify=body.auto_verify, seed=body.seed,
    )
    agent_stream.publish(
        "sim.start", client_ref="simulator", track="synthetic",
        detail={"agents": body.agents, "cases": body.cases,
                "auto_verify": body.auto_verify, "base": base},
    )
    return state


@router.delete("/demo/simulation")
def admin_simulation_stop() -> dict:
    """정지 **요청**. 루프가 다음 건에서 스스로 멈춘다(강제 종료 없음)."""
    from app.adapters import demo_simulator
    from app.obs import agent_stream

    state = demo_simulator.stop()
    agent_stream.publish("sim.stop", client_ref="simulator", track="synthetic",
                         detail={"requested": True})
    return state


@router.post("/demo/reset")
def admin_demo_reset(user=Depends(require_admin)) -> dict:
    """합성 트랙을 비운다.

    ★★**실제 트랙은 이 API 가 지울 수 없다.** 어댑터가 합성 경로만 알고 있다.
    """
    from app.adapters import demo_simulator
    from app.obs import agent_stream

    result = demo_simulator.reset()
    agent_stream.publish(
        "demo.reset", client_ref=getattr(user, "username", "admin"),
        track="synthetic", detail={"removed": result["removed"]},
    )
    return result


@router.get("/cohort-summary")
def admin_cohort_summary(code: str = Query(default="S72.0")) -> dict:
    """두 트랙을 **나란히** 보여준다 — 화면에서 섞이지 않게 라벨을 붙여서.

    ★두 값을 합치지 않는다. 합계 필드를 만들지 않는다.
    """
    from app.core.domain.insurance import DataSource, KcdCode
    from app.composition import build_cohort

    q = build_cohort()
    kc = KcdCode(version_label="", code=code.strip().upper(), name_ko="")
    out = {}
    for src in (DataSource.VERIFIED_REAL, DataSource.SYNTHETIC):
        ans = q.run(kcd_code=kc, product_id="", age_band=None, data_source=src)
        out[src.value] = {
            "n": ans.stats.n,
            "approved_n": ans.stats.approved_n,
            "denied_n": ans.stats.denied_n,
            "min_sample": ans.stats.min_sample,
            "min_sample_met": ans.stats.n >= ans.stats.min_sample,
            "approval_rate": ans.approval_rate,
            "approval_ci": list(ans.approval_ci) if ans.approval_ci else None,
            "headline": ans.headline,
        }
    return {"code": kc.code, "tracks": out}


@router.get("/kcd-codes")
def admin_kcd_codes(
    kind: str | None = Query(default=None, description="exclude · exception · mention"),
    chapter: str | None = Query(default=None, description="장 이름 일부"),
    q: str | None = Query(default=None, description="표기 검색(예: F04)"),
) -> dict:
    """**우리 약관에 실제로 등장하는 질병기호** 목록.

    ★★**KCD 사전이 아니다.** 코드→질병명 표를 우리는 갖고 있지 않다(약 2만 항목).
      `F32` 가 「우울에피소드」라고 말할 근거가 없으므로 **말하지 않는다.**
      말할 수 있는 것은 「F 는 정신·행동 장」과 「약관이 이 코드를 면책으로 쓴다」까지다.
      화면도 그렇게 적어야 한다 — 「질병기호 전체 표」라고 부르면 거짓이 된다.

    ★미리 만들어 둔 파일을 읽는다. 확정 약관 전량 스캔은 약 100초라 요청마다 못 돈다.
      **파일이 없으면 없다고 말한다** — 빈 목록으로 때우면 「등장하는 코드가 없다」로 읽힌다.
    """
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "data" / "exports" / "kcd_catalog.json"
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("질병기호 목록이 아직 만들어지지 않았습니다. "
                    "`python -m scripts.eval.kcd_catalog` 를 먼저 실행하세요."),
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items") or []
    total = len(items)
    if kind:
        items = [x for x in items if x.get("kind") == kind]
    if chapter:
        items = [x for x in items if chapter in (x.get("chapter") or "")]
    if q:
        needle = q.strip().upper()
        items = [x for x in items if needle in (x.get("range") or "").upper()]
    return {
        **{k: v for k, v in data.items() if k != "items"},
        #: ★거른 뒤에도 **전체 수를 함께** 낸다. 안 그러면 필터 결과가 전량으로 보인다.
        "matched": len(items),
        "total_ranges": total,
        "items": items,
    }


# ─────────────────────────────────────────────────────────────────────────
# 조항 의미검색 — **운영서버 전용**. 고객앱(8080)에는 이 라우터가 실리지 않는다.
#
# ★왜 여기인가 (2026-08-04)
#   조항 벡터 색인이 122,772조각 적재돼 있는데 **서비스 어디서도 조회하지 않았다.**
#   리랭커도 커머스 RAG 에만 붙어 있어 보험 쪽엔 재정렬할 대상이 없었다.
#   먼저 검색 호출부를 만들고, 리랭킹은 플래그 뒤에 둔다.
#
# ★**판정이 아니다.** 여기 결과는 근거 후보다. 보장 여부는 `/v1/prechecks` 가 정한다.
#   응답에 verdict 류 필드를 만들지 않는 이유다(코덱스 지적).
# ─────────────────────────────────────────────────────────────────────────

#: 4B 리랭커는 GPU 를 통째로 쓴다. 겹쳐 돌면 OOM 이라 문 앞에서 막는다.
#: ★`Semaphore(1)` 로 **박아** 뒀었다. 그러면 `CLAUSE_RERANK_CONCURRENCY` 는
#:   설정에만 있고 아무 일도 안 하는 값이 된다 — 고쳐도 안 바뀌는 손잡이다
#:   (코덱스 지적 2026-08-05). 설정에서 읽되, 한 번 만든 뒤엔 재사용한다.
_RERANK_GATE: "asyncio.Semaphore | None" = None


def _rerank_gate() -> asyncio.Semaphore:
    global _RERANK_GATE
    if _RERANK_GATE is None:
        from app.core.config import get_settings

        _RERANK_GATE = asyncio.Semaphore(max(1, get_settings().CLAUSE_RERANK_CONCURRENCY))
    return _RERANK_GATE


class ClauseSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=512)
    #: ★범위를 안 주면 전역이다. 전역은 `allow_global` 로 **따로** 열어야 한다 —
    #:   서로 다른 상품·세대 조항이 섞이면 그럴듯하지만 틀린 결과가 나온다.
    scope_sha256s: list[str] | None = None
    allow_global: bool = False
    final_k: int = Field(default=8, ge=1, le=50)
    candidate_k: int | None = Field(default=None, ge=1, le=200)
    rerank: bool = False


@router.post("/clause-search")
async def admin_clause_search(body: ClauseSearchRequest) -> dict:
    """조항 근거 후보를 찾는다. 라우터 전역 `require_admin` 으로 보호된다."""
    from app.adapters import clause_query_embedder
    from db.postgres.pgvector_index import get_conn
    from app.composition import build_clause_search_deps
    from app.core.config import get_settings
    from app.core.errors import InfraError, ValidationErr
    from app.core.usecases import clause_search

    st = get_settings()
    reranker = None
    if body.rerank:
        #: ★꺼져 있는데 요청이 오면 **조용히 무시하지 않는다.** 무시하면 부르는 쪽은
        #:   재정렬된 결과를 받았다고 믿는다(코덱스 지적).
        if not st.INSURANCE_CLAUSE_RERANK_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=("조항 리랭킹이 꺼져 있습니다(INSURANCE_CLAUSE_RERANK_ENABLED=false). "
                        "끈 채로 재정렬한 척하지 않습니다."),
            )
        from app.adapters.reranker import CrossEncoderReranker

        #: ★생성이 `try` 밖에 있었다. 무게추 적재·설정 오류가 **500** 으로 나가
        #:   「서버 버그」처럼 보였다(코덱스 지적). 리랭커를 못 쓰는 것은 503 이다.
        _build = lambda: CrossEncoderReranker(
            st.RERANKER_MODEL,
            device=st.RERANKER_DEVICE,
            batch_size=st.RERANKER_BATCH_SIZE,
            #: ★조항 전용 절단값을 쓴다. 커머스 768 을 그대로 쓰면
            #:   후보가 같은 앞부분만 남아 `constant scores` 로 멈추는 질의가 나온다.
            max_length=st.CLAUSE_RERANK_MAX_LENGTH,
            dtype=st.RERANKER_DTYPE,
            trust_remote_code=st.RERANKER_TRUST_REMOTE_CODE,
        )
        try:
            reranker = _build()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"리랭커를 준비하지 못했습니다: {type(exc).__name__}: {exc}",
            ) from exc

    def _run():
        with get_conn() as conn:
            return clause_search.search(
                **build_clause_search_deps(),
                conn=conn,
                embedder=clause_query_embedder.build(),
                query=body.query,
                scope_sha256s=body.scope_sha256s,
                allow_global=body.allow_global,
                final_k=body.final_k,
                candidate_k=body.candidate_k,
                reranker=reranker,
                max_candidates=st.CLAUSE_RERANK_MAX_CANDIDATES,
                score_body=st.CLAUSE_RERANK_SCORE_BODY,
                score_chars=st.CLAUSE_RERANK_SCORE_CHARS,
            )

    try:
        if reranker is None:
            result = await asyncio.to_thread(_run)
        else:
            async with _rerank_gate():
                result = await asyncio.to_thread(_run)
    except ValidationErr as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except clause_search.RerankUnavailable as exc:
        #: ★벡터 순서로 되돌려 200 을 주지 않는다. 실패는 실패로 보인다.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"리랭킹에 실패했습니다(원래 순서로 되돌리지 않습니다): {exc}",
        ) from exc
    except InfraError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return {
        "schema_version": "clause-search-v1",
        "reranked": result.reranked,
        "provenance": result.provenance,
        #: 본문이 없어 뺀 조각 수. 0 이 아니면 적재가 반쪽이라는 신호다.
        "dropped_incomplete": result.dropped_incomplete,
        #: 채점 본문이 비어 재정렬에서 뺀 수. 다른 본문으로 대신 채점하지 않는다.
        "dropped_unscorable": result.dropped_unscorable,
        "settings": {"score_body": st.CLAUSE_RERANK_SCORE_BODY,
                     "max_length": st.CLAUSE_RERANK_MAX_LENGTH,
                     "rerank_enabled": st.INSURANCE_CLAUSE_RERANK_ENABLED},
        "hits": [
            {
                "clause_id": h.clause_id,
                "insurer": h.insurer,
                "section": h.section,
                "qualified_no": h.qualified_no,
                "title": h.title,
                "page_from": h.page_from,
                "page_to": h.page_to,
                "distance": h.distance,
                "sha256": h.sha256,
            }
            for h in result.hits
        ],
        "_주의": "근거 후보입니다. 보장 여부 판정이 아닙니다 — 판정은 /v1/prechecks 가 합니다.",
    }
