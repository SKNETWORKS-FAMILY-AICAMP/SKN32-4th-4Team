"""보장 사전판정 API.

★HTTP 상태 코드 규칙 — **"확인 불가"는 오류가 아니다**

    200  판정했다. `verdict=needs_expert` 로 기권해도 200 이다.
         근거를 못 대서 기권한 것은 **정상 결과**이지 서버 잘못이 아니다.
    422  입력이 잘못됐다(가입일 형식 등).
    503  검색·저장소 장애. 이때만 "우리 잘못"이다.

    이 구분이 무너지면 클라이언트가 기권과 장애를 못 가린다.

★외부 에이전트가 부를 것을 전제로 만든다

    · `trace_id` 를 항상 돌려준다 — 나중에 "그 판정" 을 지목할 수 있어야 한다
    · 응답에 **무엇으로 판정했는지**(약관 버전·규칙 버전·추출기)를 담는다
    · 사례 제출(`/observations`)은 **판정 근거로 쓰지 않는다.** 통계·검증용이다
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile, status

from app.core.config import get_settings
from app.core.domain import kcd_ranges as kcd
from app.core.domain.precheck_result import PrecheckInput, PrecheckOutcome
from app.core.errors import InfraError, ValidationErr, public_error_message
from app.core.config_validation import require_insurance_idempotency_secret
from app.core.usecases import precheck
from app.core.usecases import self_pay
from app.routers._uploads import read_capped
from app.schemas.precheck import (
    AppliedPolicy,
    Citation,
    CodeAssessment,
    ExternalCaseSubmission,
    PrecheckRequest,
    PrecheckResult,
)
from app.schemas.self_pay import SelfPayCandidate, SelfPayRequest, SelfPayResponse

#: 청구 증거 파일 저장 위치. `data/obs/`는 이미 .gitignore 대상이다(진료 관련
#: 개인정보라 약관 원문보다도 더 커밋하면 안 된다).
_EVIDENCE_ROOT = Path(__file__).resolve().parents[2] / "data" / "obs" / "evidence"
_EVIDENCE_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}

router = APIRouter(prefix="/v1", tags=["precheck"])

#: ★어댑터를 고르는 것은 **조립 지점(`app/composition.py`)** 의 일이다.
#:   라우터가 직접 import 하면 "어느 저장소를 쓰는가"가 HTTP 계층에 흩어진다.
_DEPS = None


def _deps():
    global _DEPS
    if _DEPS is None:
        from app.composition import build_precheck

        _DEPS = build_precheck()
    return _DEPS


#: 자기부담금 소스도 같은 이유로 한 번만 조립해 캐시한다(§ 위 `_DEPS` 참고).
_SELF_PAY_SOURCE = None


def _self_pay_deps():
    global _SELF_PAY_SOURCE
    if _SELF_PAY_SOURCE is None:
        from app.composition import build_self_pay_source

        _SELF_PAY_SOURCE = build_self_pay_source()
    return _SELF_PAY_SOURCE

#: 판정 흐름. 매 요청마다 다시 조립하지 않는다.
_GRAPH = None


def _graph():
    global _GRAPH
    if _GRAPH is None:
        from app.workflow.precheck_graph import build

        _GRAPH = build()
    return _GRAPH


#: 약관 버전 목록은 매 요청마다 읽을 필요가 없다.
_VERSIONS = None


#: ★캐시를 만들 때의 모드 설정 파일 mtime. **프로세스가 둘이기 때문에** 필요하다.
_VERSIONS_STAMP = None


def _mode_stamp() -> float:
    """판정 모드 설정의 변경 시각. 파일이 없으면 0."""
    from app.core.domain import identification_mode

    try:
        f = identification_mode._MODE_FILE
        return f.stat().st_mtime if f.exists() else 0.0
    except OSError:
        return 0.0


def _versions():
    """판정에 쓸 약관 목록(캐시).

    ★★**모드가 바뀌면 자동으로 다시 읽는다.**

        관리 서버(8081)에서 모드를 바꿔도 고객 서버(8080)는 **다른 프로세스**라
        `invalidate_versions_cache()` 가 닿지 않는다. 실측 2026-08-04:
        엄격 모드로 바꿨는데 고객 API 는 계속 `total_policy_versions=132` 를
        보고했다 — **표시와 동작이 어긋난 상태**다. 관측 스트림에서 이미
        같은 종류의 문제를 겪었고, 같은 해법(파일을 진실의 출처로)을 쓴다.
    """
    #: ★캐시는 **어댑터가 파일 지문으로** 관리한다. 라우터가 따로 캐시를 두면
    #:   판정 경로와 표시 경로가 서로 다른 목록을 보게 된다 — 실제로 그랬다.
    loader = getattr(_deps()["policies"], "load_versions_cached", None)
    return loader() if loader else _deps()["policies"].load_versions()


def invalidate_versions_cache() -> None:
    """★판정 모드가 바뀌면 **반드시** 부른다.

    모드는 어떤 문서를 판정에 쓸지 바꾼다. 캐시를 그대로 두면 화면에는
    "엄격"이라 적혀 있는데 판정은 자동승인 결과로 계속 나온다 —
    **표시와 동작이 어긋나는 것이 이 도메인에서 가장 나쁜 상태**다.
    """
    global _VERSIONS, _VERSIONS_STAMP
    _VERSIONS = None
    _VERSIONS_STAMP = None


#: 확정 현황 캐시. 값과, 그 값을 만들 때의 입력 파일 지문.
_CONFIRMATION: dict | None = None
_CONFIRMATION_STAMP: tuple | None = None


def _confirmation_stats() -> dict:
    """확정이 **어디까지** 됐나. 분모를 함께 낸다.

    ★`total_policy_versions` 만 내보내면 그게 전량인 줄 안다.
      숫자를 인용할 때 분모가 무엇인지 함께 적는다(CLAUDE.md §1).

    ★★**캐시한다(2026-08-25).** 실측으로 드러난 문제다 —
      이 함수는 부를 때마다 원장(`confirmed_documents.jsonl` 1,248줄)과
      매니페스트를 **다시 파싱**했다. `GET /v1/support-manifest` 한 요청이
      **JSON 을 6,739회** 파싱해 **요청당 ~180ms** 를 썼고(동시 8에서 4.65 rps),
      이 함수는 판정 핫패스(`_create_precheck`)에서도 불린다.
      → `docs/reports/debugs/2026-08-25_1230_support_manifest가_요청마다_원장을_다시_파싱했다.md`

      ★캐시 무효화는 **파일 지문**으로 한다. `load_versions_cached()` 와 **같은 지문**을 쓴다 —
      입력(원장·모드·매니페스트)이 똑같기 때문이다. 프로세스가 둘이라 명시적 무효화는 닿지 않는다.
      시간 기반 TTL 은 쓰지 않는다. "언제 갱신될지 모르는 상태"를 만들지 않는다.
    """
    global _CONFIRMATION, _CONFIRMATION_STAMP
    from app.adapters import manifest_policy_resolver as mpr

    stamp = mpr._inputs_stamp()
    if _CONFIRMATION is not None and _CONFIRMATION_STAMP == stamp:
        return _CONFIRMATION

    try:
        ledger = mpr.load_ledger()
        collected = mpr.count_collected()
    except Exception:  # noqa: BLE001
        #: ★삼켜서 0 으로 보고하지 않는다. "못 셌다"와 "0건"은 다르다.
        return {"error": "확정 현황을 세지 못했습니다"}
    scopes: dict[str, int] = {}
    pending = 0
    for e in ledger.values():
        scopes[e.get("scope") or "-"] = scopes.get(e.get("scope") or "-", 0) + 1
        if "대기" in (e.get("confirmed_by") or ""):
            pending += 1
    out = {
        "confirmed": len(ledger),
        "collected": collected,
        "scopes": scopes,
        "human_signoff_pending": pending,
        "ledger": "config/confirmed_documents.jsonl",
    }
    #: ★실패(`error`)는 캐시하지 않는다 — 위에서 이미 반환했다.
    #:   못 센 것을 캐시하면 원인이 사라진 뒤에도 계속 못 셌다고 답한다.
    _CONFIRMATION, _CONFIRMATION_STAMP = out, stamp
    return out


def _confirmation_coverage() -> str:
    st = _confirmation_stats()
    if st.get("error") or not st.get("confirmed"):
        return ""
    total = st.get("collected") or 0
    pct = (st["confirmed"] / total * 100) if total else 0.0
    #: ★**마크다운을 쓰지 않는다.** 이 문장은 화면에 **그대로** 찍힌다 —
    #:   `insurance.js` 가 `textContent` 로 넣기 때문에 `**강조**` 가 별표째 보인다.
    #:   실측 2026-08-04: 화면에 `**10건(0.7%)**` 이 그대로 나왔다.
    msg = (f"⚠ 확정된 약관은 수집분 {total:,}건 중 {st['confirmed']}건({pct:.1f}%) 뿐입니다. "
           "여기 없는 약관은 판정하지 않고 기권합니다.")
    #: ★`demo` 만 이름으로 집어 말하다가 `machine_verified` 확대분(122건)이
    #:   **문장에서 통째로 빠졌다**(2026-08-04). 범위 이름이 늘 때마다 여기를 고쳐야 하면
    #:   반드시 빠뜨린다 — 있는 것을 **전부** 적는다.
    _LABEL = {"demo": "시연", "machine_verified": "기계대조", "-": "범위미상"}
    parts = [f"{_LABEL.get(k, k)} {v}건" for k, v in sorted(st.get("scopes", {}).items())]
    if parts:
        msg += f" 확정 범위 — {' · '.join(parts)}."
    if st.get("human_signoff_pending"):
        msg += (f" {st['human_signoff_pending']}건은 기계 대조로 확정됐고 "
                "사람의 최종 승인이 남아 있습니다.")
    return msg


#: ★도메인 ↔ HTTP 변환은 **여기(바깥)** 의 일이다.
#:
#:   유스케이스가 pydantic 모델을 직접 쓰면 **안쪽이 바깥을 참조**하게 된다.
#:   실제로 그랬고, `ARCH-002` 의 금지 목록에 `app.schemas` 가 없어 빠져나갔다.
#:   유스케이스는 `PrecheckOutcome`(순수 dataclass)을 돌려주고 여기서 옮긴다.


def _to_input(body: PrecheckRequest) -> PrecheckInput:
    normalized_codes: list[str] = []
    for raw in body.kcd_codes:
        code = (raw or "").strip().upper()
        if kcd.CodeRef.parse(code) is None:
            if any(sep in code for sep in ("~", "∼", "～", "-")):
                raise ValidationErr(
                    f"{code}는 개별 질병기호가 아니라 약관의 코드 범위입니다. "
                    "진료비 세부내역서나 진단서에 적힌 단일 코드(예: C34.1)를 입력하세요."
                )
            raise ValidationErr(
                f"{code or '(빈 값)'}의 형식이 올바르지 않습니다. "
                "단일 질병기호(예: F32, S72.0)를 입력하세요."
            )
        normalized_codes.append(code)
    return PrecheckInput(
        insurer=body.insurer,
        enrolled_on=body.enrolled_on,
        kcd_codes=tuple(normalized_codes),
        product_name=body.product_name,
        client_ref=body.client_ref,
        condition_text=body.condition_text,
    )


def _cite(c) -> Citation:
    return Citation(
        clause_id=c.clause_id,
        qualified_no=c.qualified_no,
        section=c.section,
        title=c.title,
        scope=c.scope,
        occurrence_id=c.occurrence_id,
        quote=c.quote,
        page_from=c.page_from,
        page_to=c.page_to,
        tier=c.tier,
    )


def _policy(p) -> AppliedPolicy:
    return AppliedPolicy(
        insurer=p.insurer,
        product_name=p.product_name,
        sale_start=p.sale_start,
        sale_end=p.sale_end,
        generation=p.generation,
        generation_label=p.generation_label,
        product_line=p.product_line,
        sha256=p.sha256,
        date_confidence=p.date_confidence,
        generation_confidence=p.generation_confidence,
        parse_status=p.parse_status,
    )


def _to_dto(o: PrecheckOutcome) -> PrecheckResult:
    return PrecheckResult(
        verdict=o.verdict,
        abstained=o.abstained,
        reason_code=o.reason_code,
        message=o.message,
        applied_policy=_policy(o.applied_policy) if o.applied_policy else None,
        per_code=[
            CodeAssessment(
                code=a.code,
                verdict=a.verdict,
                reason_code=a.reason_code,
                citations=[_cite(c) for c in a.citations],
                note=a.note,
            )
            for a in o.per_code
        ],
        citations=[_cite(c) for c in o.citations],
        #: ★근거와 **다른 목록**으로 내보낸다. 합치면 화면이 둘을 같은 무게로 그린다.
        related_clauses=[_cite(c) for c in o.related_clauses],
        related_search=o.related_search,
        candidates=[_policy(c) for c in o.candidates],
        rule_engine_version=o.rule_engine_version,
        extractor=o.extractor,
        trace_id=o.trace_id,
        warnings=o.warnings,
    )


@router.post("/prechecks", response_model=PrecheckResult)
def create_precheck(
    body: PrecheckRequest,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]{7,127}$",
        ),
    ] = None,
) -> PrecheckResult:
    return _create_precheck(
        body,
        record_knowledge_gap=True,
        idempotency_key=idempotency_key,
        channel="public-api",
    )


def create_precheck_for_registered_agent(
    body: PrecheckRequest,
    *,
    subject_ref_hash: str | None = None,
    idempotency_key: str | None = None,
) -> PrecheckResult:
    """보호 기계 채널용. KCD 원문을 knowledge-gap 로그에 복제하지 않는다."""

    return _create_precheck(
        body,
        record_knowledge_gap=False,
        idempotency_key=idempotency_key,
        channel="registered-agent",
        agent_client_id=body.client_ref,
        subject_ref_hash=subject_ref_hash,
    )


def _create_precheck(
    body: PrecheckRequest,
    *,
    record_knowledge_gap: bool,
    idempotency_key: str | None = None,
    channel: str = "public-api",
    agent_client_id: str | None = None,
    subject_ref_hash: str | None = None,
) -> PrecheckResult:
    """가입일·질병기호로 보장 여부를 미리 본다.

    ★근거 조항을 못 대면 `verdict="needs_expert"` 로 답한다(HTTP 200).
      추측해서 "보장됩니다"라고 말하지 않는다.
    """
    try:
        #: ★흐름은 **그래프가 소유한다.** 라우터가 유스케이스를 직접 부르면
        #:   같은 판단이 두 곳(라우터·그래프)에 생기고 반드시 어긋난다.
        #:   그래프는 판정을 바꾸지 않고 잇고·분기하고·재시도만 통제한다
        #:   (`docs/handoff/06_계약_Agent.md` §1).
        outcome, _state = _graph().invoke(_to_input(body))
        dto = _to_dto(outcome)

        #: ★★**확정이 부분이면 고른 판본이 진짜 최신이 아닐 수 있다.**
        #:
        #:   `resolve()` 는 「가입일 이전 판매 시작 중 **가장 늦은 것**」을 고른다.
        #:   그런데 그 '가장 늦은 것'은 **확정된 것 중에서만** 가장 늦다.
        #:   진짜 적용 판본이 아직 미확정이면 **조용히 더 오래된 판본**이 뽑히고,
        #:   응답은 그것을 100% 확정된 것과 **똑같은 확신으로** 말한다.
        #:
        #:   실측 2026-08-04 — 확정을 10건에서 132건으로 늘리자
        #:   `NH농협생명 20180101` 의 적용 판본이 **2세대에서 3세대로 바뀌었다.**
        #:   답이 바뀐 것이 아니라 **원래 3세대였는데 안 보였던 것**이다.
        #:   그때 응답의 `warnings` 는 비어 있었다 — 사용자는 알 길이 없었다.
        #:
        #:   ★그래서 판본을 골랐을 때만 붙인다. 기권했으면 이미 다른 사유를 말하고 있다.
        if dto.applied_policy is not None:
            #: ★★**판매 종료를 모르면 「계속 판매 중」으로 취급된다** — 조용한 폴백이다.
            #:
            #:   `resolve()` 의 조건은 `not v.sale_end or enrolled_on <= v.sale_end` 다.
            #:   `sale_end` 가 비면 **무조건 통과**한다. 실측 2026-08-04 — 확정 850건 중
            #:   **445건(52.4%)이 종료일 미상**이고, 그중 54건은 2018년 이전 판매개시다.
            #:
            #:   가장 늦은 판본을 고르므로 옛 것이 최신을 이기지는 않는다. 그래도
            #:   「그 시점에 정말 팔고 있었나」는 **확인된 사실이 아니다.**
            #:   모르면 모른다고 한다(CLAUDE.md §0) — 막지는 않되 **말은 한다.**
            if not (dto.applied_policy.sale_end or "").strip():
                dto.warnings = [
                    *dto.warnings,
                    f"이 약관의 판매 종료 시점을 모릅니다(판매개시 "
                    f"{dto.applied_policy.sale_start}). 가입일 당시 실제로 판매 중이었는지는 "
                    "확인된 사실이 아닙니다 — 더 나중 판본이 있었을 수 있습니다.",
                ]
            st = _confirmation_stats()
            done, total = st.get("confirmed") or 0, st.get("collected") or 0
            if total and done < total:
                dto.warnings = [
                    *dto.warnings,
                    f"확정된 약관은 보유 {total:,}건 중 {done:,}건({done / total * 100:.1f}%)입니다. "
                    "아직 확정되지 않은 약관 중에 더 나중에 판매된 판본이 있을 수 있고, "
                    "그렇다면 여기 적힌 약관은 실제 적용 판본이 아닐 수 있습니다.",
                ]
        dto, duplicate = _persist_precheck_if_enabled(
            body,
            outcome,
            dto,
            idempotency_key=idempotency_key,
            channel=channel,
            agent_client_id=agent_client_id,
            subject_ref_hash=subject_ref_hash,
        )

        #: ★★**근거를 못 대서 기권한 건은 지식갭 큐로 보낸다(2026-08-04).**
        #:
        #:   `no_evidence` 는 "약관에 이 질병기호에 대한 근거가 없다"는 뜻이다 —
        #:   정확히 **문서 보강 대상**이고, 그게 이 큐의 용도다.
        #:
        #:   ★`documents_not_confirmed` 는 **넣지 않는다.** 그건 지식이 없는 게 아니라
        #:     확정 작업이 밀린 것이고, 넣으면 같은 문장으로 큐가 넘쳐
        #:     진짜 보강 대상이 묻힌다. 그 현황은 `support-manifest` 가 이미 말한다.
        #:
        #:   ★★**저장 뒤로 옮겼다(코덱스 리뷰 지적).** 원래 그래프 직후에 불렀는데,
        #:     그러면 같은 `Idempotency-Key`로 재시도할 때마다 저장은 멱등이어도
        #:     지식갭은 매번 새로 쌓였다. `duplicate=True`(재생 요청)면 건너뛴다.
        if (
            record_knowledge_gap
            and not duplicate
            and dto.abstained
            and dto.reason_code == "no_evidence"
        ):
            from app.obs.knowledge_gaps import record_gap_safe

            record_gap_safe(
                f"[판정] {body.insurer} · {', '.join(body.kcd_codes)} — 근거 조항 없음")

        #: ★관측은 **응답을 만들고 필수 저장까지 성공한 뒤** 붙인다.
        #:   그리고 판정 자체를 바꾸지 않는다(`agent_stream` 은 표현 계층이다).
        from app.obs import agent_stream

        agent_stream.publish(
            "agent.precheck",
            client_ref=body.client_ref or "-",
            detail={"insurer": body.insurer, "code_count": len(body.kcd_codes),
                    "verdict": dto.verdict, "abstained": dto.abstained,
                    "trace_id": dto.trace_id},
        )
        return dto
    except ValidationErr as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e


def _persist_precheck_if_enabled(
    body: PrecheckRequest,
    outcome: PrecheckOutcome,
    dto: PrecheckResult,
    *,
    idempotency_key: str | None,
    channel: str,
    agent_client_id: str | None,
    subject_ref_hash: str | None,
) -> tuple[PrecheckResult, bool]:
    """명시적으로 켠 환경에서만 실제 원장 저장을 응답 전 필수 단계로 실행한다.

    반환하는 `bool`은 **재생 요청(같은 Idempotency-Key로 이미 저장됨)**이었는지다.
    호출부가 이걸로 지식갭 등 저장과 별개인 부수효과의 중복 기록을 막는다
    (코덱스 리뷰 지적 — 저장은 멱등인데 부수효과가 매번 새로 쌓이던 결함).
    """

    from datetime import datetime
    from app.core.config import get_settings

    settings = get_settings()
    if settings.PRECHECK_PERSISTENCE == "off":
        return dto, False
    if settings.PRECHECK_PERSISTENCE != "postgres":
        raise ValidationErr("알 수 없는 precheck persistence 설정입니다.")
    if channel == "registered-agent":
        from app.core.errors import ConfigError
        if not settings.AGENT_REAL_LEDGER_ENABLED:
            raise ConfigError(
                "registered-agent PostgreSQL 저장은 두 agent client 원장 동기화가 "
                "완료되고 AGENT_REAL_LEDGER_ENABLED=true일 때만 허용됩니다."
            )
        if not agent_client_id or not subject_ref_hash or not body.consent_purpose:
            raise ValidationErr(
                "registered-agent PostgreSQL 저장에는 client, subject hash, consent purpose가 필요합니다."
            )
    if not body.incident_on:
        raise ValidationErr("PostgreSQL 저장 모드에서는 incident_on이 필요합니다.")
    if not idempotency_key:
        raise ValidationErr("PostgreSQL 저장 모드에서는 Idempotency-Key가 필요합니다.")
    try:
        enrolled_on = datetime.strptime(body.enrolled_on, "%Y%m%d").date()
        incident_on = datetime.strptime(body.incident_on, "%Y%m%d").date()
    except ValueError as exc:
        raise ValidationErr("enrolled_on/incident_on은 실제 존재하는 YYYYMMDD 날짜여야 합니다.") from exc

    from db.postgres.pg_insurance_repository import PgInsuranceRepository
    from app.core.usecases.persist_precheck import PersistPrecheckCommand, persist

    result = persist(
        PersistPrecheckCommand(
            outcome=outcome,
            enrolled_on=enrolled_on,
            incident_on=incident_on,
            kcd_codes=tuple(body.kcd_codes),
            channel=channel,
            agent_client_id=agent_client_id,
            subject_ref_hash=subject_ref_hash,
            consent_purpose=body.consent_purpose,
            idempotency_key=idempotency_key,
            idempotency_secret=require_insurance_idempotency_secret(settings),
            request_snapshot=body.model_dump(mode="json"),
            response_snapshot=dto.model_dump(mode="json"),
        ),
        repository=PgInsuranceRepository.from_settings(),
    )
    return PrecheckResult.model_validate(result.response_snapshot), result.duplicate


@router.get("/support-manifest")
def support_manifest() -> dict:
    """**무엇을 지원하는지** 밝힌다.

    ★"1,367건 전부 지원"이라고 말하지 않는다. 판정에 쓸 수 있는 것만 센다.
      클라이언트가 기대를 잘못 세우면 없는 보험사를 계속 물어보게 된다.
    """
    vs = _versions()
    by_insurer: dict[str, dict] = {}
    for v in vs:
        d = by_insurer.setdefault(
            v.insurer,
            {"versions": 0, "generations": set(), "lines": set(), "earliest": "", "latest": ""},
        )
        d["versions"] += 1
        if v.generation:
            d["generations"].add(v.generation)
        if v.product_line:
            d["lines"].add(v.product_line)
        if not d["earliest"] or v.sale_start < d["earliest"]:
            d["earliest"] = v.sale_start
        if v.sale_start > d["latest"]:
            d["latest"] = v.sale_start
    from app.core.ports.precheck import REQUIRE_CONFIRMED

    #: ★확정 게이트를 껐으면 **반드시 드러낸다.**
    #:   조용히 미확정 약관으로 답하는 것이 가장 위험하다.
    notes = [
        "판정은 약관 원문 조항만을 근거로 한다.",
        "근거를 못 대면 verdict=needs_expert 로 기권한다(HTTP 200).",
        "면책 목록에 없다는 것이 보장된다는 뜻은 아니다.",
    ]
    if not REQUIRE_CONFIRMED:
        notes.insert(
            0,
            "⚠ 확정 게이트가 꺼져 있습니다(PRECHECK_ALLOW_UNCONFIRMED=1). "
            "식별이 확정되지 않은 약관도 판정에 쓰고 있습니다 — 시연용입니다.",
        )
    elif not vs:
        notes.insert(
            0,
            "⚠ 판정 가능한 약관이 0건입니다. 수집은 끝났으나 "
            "'이 파일이 무엇인가'를 사람이 확정하는 절차가 아직 남았습니다. "
            "확인 안 된 약관으로 보장 여부를 답하지 않습니다.",
        )

    #: ★★**확정 범위를 숨기지 않는다.**
    #:
    #:   0건일 때는 위 경고가 사실을 말했다. 그런데 데모용으로 10건을 확정하자
    #:   경고가 **사라지고** `total_policy_versions: 10` 만 남았다 — 읽는 사람은
    #:   전체 1,367건 중 판정 준비가 끝난 것으로 읽는다. 실제로는 **0.7%** 다.
    #:
    #:   그리고 그 10건은 `scripts.confirm.identify_documents` 의 **기계 대조**로
    #:   확정됐다. 사람의 최종 승인이 아직 남아 있다면 그것도 말해야 한다 —
    #:   감지하고 통과시키면서 신호를 안 남기면 그건 폴백이다(CLAUDE.md §0).
    coverage = _confirmation_coverage()
    if coverage:
        notes.insert(0, coverage)

    #: ★자동승인 모드면 **맨 앞에** 경고를 둔다. 뒤에 두면 안 읽힌다.
    from app.core.domain import identification_mode

    mode = identification_mode.current()
    if mode.auto_approve:
        notes.insert(0, f"⚠ {mode.as_dict()['warning']}")

    return {
        "schema_version": "v1",
        "rule_engine_version": precheck.RULE_ENGINE_VERSION,
        "require_confirmed_documents": REQUIRE_CONFIRMED,
        #: ★어느 게이트로 만든 목록인지 **항상** 함께 낸다. 숫자만 보면 오해한다.
        "identification_mode": mode.as_dict(),
        "total_policy_versions": len(vs),
        #: 판정 준비가 끝난 범위. `total_policy_versions` 만 보면 전량으로 오해한다.
        "confirmation": _confirmation_stats(),
        "insurers": {
            k: {
                "versions": d["versions"],
                "generations": sorted(d["generations"]),
                "product_lines": sorted(d["lines"]),
                "sale_start_range": [d["earliest"], d["latest"]],
            }
            for k, d in sorted(by_insurer.items())
        },
        "notes": notes,
    }


@router.post("/observations/evidence")
async def upload_observation_evidence(file: UploadFile = File(...)) -> dict[str, str]:
    """청구 증거(진료비 영수증·진단서 스캔본)를 저장하고 sha256·저장 참조를 돌려준다.

    ★판정 근거로 쓰지 않는다 — `/observations` 제출에 붙는 감사용 원본일 뿐이고,
      내용을 파싱·신뢰하지 않는다(§docs/handoff 데이터 축적 설계).
    ★같은 sha256이면 다시 쓰지 않는다(중복 저장 방지) — 이미 저장된 파일이면 그대로 재사용.
    """
    settings = get_settings()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _EVIDENCE_SUFFIXES:
        raise ValidationErr(
            f"증거 파일은 {', '.join(sorted(_EVIDENCE_SUFFIXES))} 형식만 허용됩니다."
        )
    raw = await read_capped(
        file, settings.OBSERVATION_EVIDENCE_MAX_UPLOAD_BYTES, field="증거 파일"
    )
    if not raw:
        raise ValidationErr("빈 파일은 증거로 저장할 수 없습니다.")

    sha256 = hashlib.sha256(raw).hexdigest()
    _EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    dest = _EVIDENCE_ROOT / f"{sha256}{suffix}"
    if not dest.exists():
        #: ★익명 공개 엔드포인트다 — 파일당 상한만으로는 **누적** 저장량을 못 막는다
        #:   (코덱스 리뷰 지적: 서로 다른 파일을 계속 올려 디스크를 채울 수 있음).
        #:   같은 내용 재업로드는 위에서 이미 걸러진다(디스크가 안 는다) — 여기서
        #:   막는 건 **새 내용**이 계속 들어와 총량이 느는 경우다.
        current_total = sum(
            p.stat().st_size for p in _EVIDENCE_ROOT.glob("*") if p.is_file()
        )
        if current_total + len(raw) > settings.OBSERVATION_EVIDENCE_MAX_TOTAL_BYTES:
            raise InfraError(
                "증거 저장 공간이 가득 찼습니다. 잠시 후 다시 시도하거나 "
                "고객센터로 문의해 주세요."
            )
        dest.write_bytes(raw)
    return {
        "evidence_sha256": sha256,
        #: ★`str(Path)`는 OS 구분자를 쓴다 — Windows에서 저장하면 `\`가 섞여
        #:   운영 Linux에서 그대로 못 쓴다. 저장 참조는 항상 posix로 고정한다.
        "evidence_stored_ref": dest.relative_to(_EVIDENCE_ROOT.parents[2]).as_posix(),
    }


@router.post("/observations", status_code=status.HTTP_202_ACCEPTED)
def submit_observation(
    body: ExternalCaseSubmission,
    idempotency_header: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]{7,127}$",
        ),
    ] = None,
) -> dict:
    """외부 에이전트가 실제 청구 결과를 보고한다.

    ★이 데이터는 **판정 근거가 되지 않는다.**

        약관 조항과 같은 인덱스에 넣지 않고, 같은 등급으로 인용하지도 않는다.
        검증되지 않은 남의 보고로 "보장됩니다"라고 말하면 안 되기 때문이다.
        쓰임은 둘이다 — **승인율 통계**와 **우리 판정의 사후 검증**.

    ★원본을 그대로 남기고, 정규화한 것은 append-only 로 쌓는다.

        data/external/submissions/{YYYY-MM}/{client_ref}/{ts}_{idem}.json
        data/external/events/{YYYY-MM-DD}.jsonl

      나중에 파싱 규칙이 바뀐다. 정규화한 것만 두면 "그때 뭘 받았나"를
      다시 볼 수 없다(약관 PDF 를 원본으로 두는 것과 같은 이유).

    ★`verification` 은 **클라이언트가 뭐라 보내든 `unverified`** 다.
      남이 자기 데이터를 스스로 "검증됨"이라 하면 그건 검증이 아니다.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if settings.OUTCOME_PERSISTENCE == "file":
        from app.adapters import external_submission_store as store

        try:
            res = store.store(body.model_dump())
        except InfraError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=public_error_message(e),
            ) from e
        stored = res.stored
        duplicate = res.duplicate
        idempotency_key = res.idempotency_key
        submission_id = res.submission_id
    else:
        persisted = _persist_observation_postgres(
            body, idempotency_header=idempotency_header
        )
        stored = not persisted.duplicate
        duplicate = persisted.duplicate
        idempotency_key = idempotency_header or body.idempotency_key or ""
        submission_id = persisted.submission_id

    from app.obs import agent_stream

    #: ★`track="verified_real"` — **제출된 트랙**을 말하는 것이지
    #:   "검증됐다"는 뜻이 아니다. 아래 `verification` 이 `unverified` 인 것과 모순 아니다.
    agent_stream.publish(
        "agent.observe",
        client_ref=body.client_ref,
        track="verified_real",
        detail={"outcome": body.outcome, "code_count": len(body.kcd_codes),
                "duplicate": duplicate},
    )
    return {
        "accepted": True,
        "stored": stored,
        "duplicate": duplicate,
        "idempotency_key": idempotency_key,
        "submission_id": submission_id,
        "verification": "unverified",
        "note": (
            "이미 접수된 보고입니다(재시도로 판단해 새로 쌓지 않았습니다)."
            if duplicate
            else "접수했습니다. 이 보고는 검증 전까지 통계에 반영되지 않으며, "
            "약관 조항과 같은 근거로 쓰이지 않습니다."
        ),
        "echo": {
            "client_ref": body.client_ref,
            "insurer": body.insurer,
            "outcome": body.outcome,
            "precheck_trace_id": body.precheck_trace_id,
        },
    }


def _persist_observation_postgres(
    body: ExternalCaseSubmission,
    *,
    idempotency_header: str | None,
    channel: str = "public-api",
    agent_client_id: str | None = None,
):
    from datetime import datetime

    from db.postgres.pg_insurance_repository import PgInsuranceRepository
    from app.core.config import get_settings
    from app.core.usecases.persist_outcome import PersistOutcomeCommand, persist

    body_key = (body.idempotency_key or "").strip()
    header_key = (idempotency_header or "").strip()
    if body_key and header_key and body_key != header_key:
        raise ValidationErr("본문과 헤더의 observation idempotency key가 다릅니다.")
    key = header_key or body_key
    required = {
        "client_ref": body.client_ref,
        "precheck_trace_id": body.precheck_trace_id,
        "precheck_idempotency_key": body.precheck_idempotency_key,
        "claimed_on": body.claimed_on,
        "decided_on": body.decided_on,
        "evidence_doc_type": body.evidence_doc_type,
        "evidence_sha256": body.evidence_sha256,
        "evidence_stored_ref": body.evidence_stored_ref,
        "idempotency_key": key,
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise ValidationErr(
            "PostgreSQL outcome 저장에 필요한 값이 없습니다: " + ", ".join(missing)
        )
    try:
        claimed_on = datetime.strptime(body.claimed_on or "", "%Y%m%d").date()
        decided_on = datetime.strptime(body.decided_on or "", "%Y%m%d").date()
    except ValueError as exc:
        raise ValidationErr("claimed_on/decided_on은 실제 존재하는 YYYYMMDD여야 합니다.") from exc

    request_snapshot = body.model_dump(
        mode="json",
        exclude={"idempotency_key", "precheck_idempotency_key"},
    )
    settings = get_settings()
    return persist(
        PersistOutcomeCommand(
            precheck_trace_id=body.precheck_trace_id or "",
            precheck_idempotency_key=body.precheck_idempotency_key or "",
            claimed_on=claimed_on,
            decided_on=decided_on,
            outcome=body.outcome,
            outcome_reason=body.outcome_reason,
            claimed_amount=body.claimed_amount,
            paid_amount=body.paid_amount,
            evidence_doc_type=body.evidence_doc_type or "",
            evidence_sha256=body.evidence_sha256 or "",
            evidence_stored_ref=body.evidence_stored_ref or "",
            idempotency_key=key,
            idempotency_secret=require_insurance_idempotency_secret(settings),
            client_ref=body.client_ref,
            channel=channel,
            agent_client_id=agent_client_id,
            request_snapshot=request_snapshot,
        ),
        repository=PgInsuranceRepository.from_settings(),
    )


@router.get("/catalog/products")
def catalog_products(
    insurer: str = Query(..., min_length=1, description="보험사명. **필수** — 전체 목록은 주지 않는다"),
    q: str = Query(default="", description="상품명 일부(2자 이상)"),
    enrolled_on: str = Query(default="", description="가입일 YYYYMMDD. 주면 그 시점 것만"),
    limit: int = Query(default=10, ge=1, le=30),
) -> dict:
    """상품명 **검색**. 전체 목록이 아니다.

    ★★**「우리가 지원하는 상품 전부」로 읽히면 안 된다.**

        확정 약관 건수는 여기 박아두지 않는다 — 원장이 바뀔 때마다 낡는다
        (실측: 850/1,367→1,353/1,362→1,354/… 로 하루 안에도 갱신됐다). 지금 값은
        `config/confirmed_documents.jsonl` 의 `identification=="confirmed"` 건수를
        판정대상 분모와 함께 직접 세거나 `GET /v1/support-manifest` 를 본다.
        확정 안 된 나머지는 아직 「이 파일이 무엇인가」를 확정하지 못한 것이지
        **없는 상품이 아니다.** 전량을 내려주면 「목록에 없으면 미지원」으로
        읽힌다 — 그건 거짓이다.

        그래서 **보험사를 필수로 받고**, 검색어로 좁히고, 상한을 둔다.
        응답에 「전체 목록이 아니다」를 항상 싣는다.

    ★같은 상품명이 **여러 판본**으로 존재한다(실측 140종·421건 = 확정분의 절반).
      그래서 이름을 골라도 판본은 **가입일이 정한다.** `versions` 로 그 사실을 보인다 —
      「골랐으니 내 약관을 특정했다」고 믿게 두면 안 된다.
    """
    import collections

    vs = [v for v in _versions() if v.insurer == insurer]
    if not vs:
        #: ★"없다"와 "확정 전이다"를 섞지 않는다.
        return {"insurer": insurer, "matched": 0, "items": [],
                "note": (f"'{insurer}' 로 확정된 약관이 없습니다. 약관을 보유하지 않았다는 뜻이 "
                         "아니라 아직 확정 절차가 남았다는 뜻입니다.")}

    needle = _norm_for_search(q)
    if needle and len(needle) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="검색어는 2자 이상 입력하세요(전체 목록은 제공하지 않습니다).",
        )
    pool = [v for v in vs if not needle or needle in _norm_for_search(v.product_name)]
    if enrolled_on.strip():
        pool = [v for v in pool
                if v.sale_start <= enrolled_on
                and (not v.sale_end or enrolled_on <= v.sale_end)]

    #: 같은 이름은 하나로 묶고 **판본 수를 함께** 낸다.
    grouped: dict[str, list] = collections.defaultdict(list)
    for v in pool:
        grouped[v.product_name].append(v)
    items = [
        {
            "product_name": name,
            "versions": len(rows),
            "sale_start_range": [min(r.sale_start for r in rows),
                                 max(r.sale_start for r in rows)],
            "generations": sorted({r.generation for r in rows if r.generation}),
            "product_lines": sorted({r.product_line for r in rows if r.product_line}),
        }
        for name, rows in grouped.items()
    ]
    items.sort(key=lambda x: (-x["versions"], x["product_name"]))
    total = len(items)
    return {
        "insurer": insurer,
        #: ★거른 뒤에도 **분모를 준다.** 없으면 검색 결과가 전량으로 보인다.
        "matched": total,
        "shown": min(total, limit),
        "confirmed_for_insurer": len(vs),
        "items": items[:limit],
        #: ★**마크다운을 쓰지 않는다.** 화면이 `textContent` 로 넣어 별표가 그대로 보인다
        #:   — 같은 실수를 `_confirmation_coverage()` 에서 이미 했다(2026-08-04).
        "note": ("전체 상품 목록이 아니라 확정된 약관에서 검색된 후보입니다. "
                 "목록에 없다고 그 상품이 없는 것은 아닙니다 — 아직 확정 절차가 남은 약관이 있습니다. "
                 "상품명을 골라도 적용 판본은 가입일이 정합니다."),
    }


@router.post("/self-pay", response_model=SelfPayResponse)
def get_self_pay(body: SelfPayRequest) -> SelfPayResponse:
    """승인된 표 사실로만 자기부담금을 계산한다.

    ★사람이 승인한 사실이 정확히 하나로 좁혀질 때만 계산한다(`app/core/usecases/self_pay.py`).
      후보가 여럿이거나 없으면 `found`/`ambiguous`로만 답하고 금액은 `null`이다 —
      모르면 모른다고 한다(CLAUDE.md §0). 이 계산은 참고용이며 실제 심사·지급 판단이 아니다.
    """
    try:
        source = _self_pay_deps()
        result = self_pay.lookup(
            policy_version_sha=body.policy_version_sha,
            plan=body.plan,
            service=body.service,
            institution=body.institution,
            coverage=body.coverage,
            source=source,
        )
    except ValidationErr as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except InfraError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=public_error_message(e),
        ) from e

    if not result.matches or result.ambiguous:
        return SelfPayResponse(
            policy_version_sha=result.policy_version_sha,
            found=bool(result.matches),
            ambiguous=result.ambiguous,
            reason=result.reason,
            candidates=[
                SelfPayCandidate(
                    candidate_id=f.candidate_id,
                    institution=f.institution,
                    coverage=f.coverage,
                    formula=f.formula,
                    page=f.page,
                )
                for f in result.matches
            ],
        )

    try:
        calc = self_pay.calculate(result, eligible_expense_won=body.eligible_expense_won)
    except ValidationErr as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e

    return SelfPayResponse(
        policy_version_sha=result.policy_version_sha,
        found=True,
        ambiguous=False,
        reason=result.reason,
        eligible_expense_won=calc.eligible_expense_won,
        deductible_won=calc.deductible_won,
        formula=calc.formula,
    )


def _norm_for_search(s: str) -> str:
    """검색용 정규화 — 공백·기호·대소문자를 지운다."""
    import re
    import unicodedata

    return re.sub(r"[^0-9a-z가-힣]", "", unicodedata.normalize("NFKC", s or "").lower())


@router.get("/catalog/codes")
def catalog_codes(
    q: str = Query(default="", description="표기 검색(예: F04)"),
    chapter: str = Query(default="", description="장 이름 일부"),
    limit: int = Query(default=60, ge=1, le=200),
) -> dict:
    """**약관에 등장한 질병코드·범위.** 입력 도우미다.

    ★★**「입력 가능한 코드 목록」이 아니다.** 아무 KCD 코드나 입력할 수 있고,
      여기 없는 코드도 판정이 정상 처리한다(「면책 조항에는 없습니다」가 유효한 답).

      실측 2026-08-04 — 흔한 청구 코드가 이 목록에 **없다**:
        K02.0 치아우식 · F32 우울  → 구체적 항목 있음
        S72.0 골절 · K29.7 위염 · H25.9 백내장 · N20.0 신장결석 → 없음
      이 목록에 든 것은 **약관이 콕 집어 말한 코드**(정신질환·임신출산·치과·비만·
      요실금 등)뿐이다. 그래서 목록에서 자기 코드를 못 찾은 사용자가
      「지원 안 하나 보다」라고 읽고 **입력을 포기**하는 것이 이 기능의 주된 위험이다.
      → 화면과 응답이 **「목록에 없는 코드도 입력할 수 있습니다」를 항상** 말한다.

    ★★**면책·예외 라벨을 고객에게 내보내지 않는다**(코덱스 권고).
      「F04~F99 면책」만 보면 F32 가 예외(급여분 보상)라는 것을 놓쳐
      **정당한 청구를 포기**할 수 있다. 보장 여부는 판정 API 가 근거와 함께 답한다.
      관리자용 `/api/admin/kcd-codes` 는 라벨을 그대로 내보낸다 — 용도가 다르다.
    """
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "data" / "exports" / "kcd_catalog.json"
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="질병코드 목록이 아직 준비되지 않았습니다. 코드는 직접 입력하실 수 있습니다.",
        )
    data = json.loads(path.read_text(encoding="utf-8"))

    #: ★같은 표기가 쓰임(면책/예외/언급)별로 나뉘어 있다. 라벨을 빼는 이상
    #:   **한 줄로 합친다** — 안 합치면 같은 코드가 두 번 보여 혼란만 준다.
    merged: dict[str, dict] = {}
    for x in data.get("items") or []:
        cur = merged.setdefault(x["range"], {
            "code": x["range"],
            "chapter": x.get("chapter", ""),
            "policies": 0,
            # 약관에는 C30~C39 같은 범위도 등장하지만, 개인의 진단서에 적힌
            # 상병코드는 C34.1 같은 단일 코드다. 범위를 입력 도우미가 그대로
            # 판정 입력에 넣으면 정상적인 단일 코드 검증에서 실패한다.
            "input_allowed": kcd.CodeRef.parse(x["range"]) is not None,
        })
        cur["policies"] = max(cur["policies"], x.get("documents", 0))
    items = list(merged.values())
    total = len(items)

    if q.strip():
        needle = q.strip().upper()
        items = [x for x in items if needle in x["code"].upper()]
    if chapter.strip():
        items = [x for x in items if chapter.strip() in x["chapter"]]
    items.sort(key=lambda x: (-x["policies"], x["code"]))

    return {
        "scanned_policies": data.get("scanned_policies", 0),
        "built_at": data.get("built_at", ""),
        #: ★거른 뒤에도 분모를 준다.
        "total_codes": total,
        "matched": len(items),
        "shown": min(len(items), limit),
        "items": items[:limit],
        "notes": [
            "이 목록은 확정된 약관에서 실제로 언급된 코드·범위입니다.",
            "★목록에 없는 코드도 그대로 입력하실 수 있습니다.",
            "코드가 목록에 있다고 보장되는 것이 아니고, 없다고 보장이 안 되는 것도 아닙니다.",
            "가장 정확한 방법은 진료비 세부내역서나 진단서에 적힌 상병코드를 그대로 넣는 것입니다.",
        ],
    }
