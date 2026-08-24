"""MCP 서버 — 보험 사전검토를 에이전트에게 **화면 없이** 연다.

실행: `python -m app.mcp.server` (stdio 전송)

★도구는 **4개**다

    `precheck`            보장 사전검토
    `explain_term`        약관 원문 기반 LLM 용어 설명
    `cohort_stats`        코호트 통계 조회
    `submit_observation`  청구 결과 보고

    10종 parity·A2A 위임은 후순위다. 이 셋이 데모 루프를 정확히 덮는 최소 집합이고,
    "AI 에이전트가 화면 없이 직접 쓴다"는 포지셔닝의 **유일한 증거**다.
    증거 없는 포지셔닝은 발표에서 그냥 말이 된다.

★★**REST 라우터 함수를 그대로 부른다.** 로직을 두 벌 만들지 않는다.

    커머스 시절 MCP 서버는 서비스·도구를 각자 래핑했고, 그래서 `TEST-MCP-PARITY-001`
    이라는 별도 시험이 필요했다. 여기서는 아예 **같은 함수**를 부른다 —
    파생 구현이 없으므로 어긋날 자리가 없다. 라우터와 MCP 는 둘 다
    Interface 계층이므로 이 의존은 안쪽을 향하지 않는다.

    라우터는 실패를 `HTTPException` 으로 던진다. MCP 에는 HTTP 가 없으므로
    **구조화된 오류 dict** 로 바꿔 준다 — 상태코드를 그대로 실어서
    에이전트가 "기권(200)"과 "장애(503)"를 여전히 구분할 수 있게 한다.

★기권을 오류로 만들지 않는다

    `verdict="needs_expert"` 는 **정상 결과**다. MCP 도구가 이걸 예외로 던지면
    에이전트는 재시도 루프에 빠진다. 그대로 결과로 돌려준다.

★이 서버는 별도 프로세스다

    FastAPI lifespan 이 없다. DB·인덱스를 자동으로 만들지 않는다 —
    준비가 안 됐으면 준비 안 됐다고 실패한다(무폴백).
"""

from __future__ import annotations

import sys
from typing import Annotated

from pydantic import Field

# Windows 콘솔 stdio 를 UTF-8 로(한글 도구 설명·인자 안전)
if sys.platform == "win32":  # pragma: no cover - 플랫폼 분기
    for _stream in (sys.stdin, sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8")

from mcp.server.fastmcp import FastMCP  # noqa: E402  (UTF-8 재설정 후 임포트)

from app.core.config import get_settings  # noqa: E402

mcp = FastMCP("올바른 보험비서")


def _call(fn, *args, **kwargs) -> dict:
    """라우터 함수를 부르고, HTTP 예외를 **구조화된 오류**로 바꾼다.

    ★오류를 예외로 던지지 않고 dict 로 돌려주는 이유 — 에이전트는 분기해야 한다.
      예외는 "실패했다"만 알려주지만 dict 는 **무엇을 해야 하는지**를 담는다.
    """
    from fastapi import HTTPException
    from app.core.errors import AppError
    from pydantic import ValidationError

    try:
        out = fn(*args, **kwargs)
    except HTTPException as e:
        return {
            "ok": False,
            "http_status": e.status_code,
            "error": str(e.detail),
            #: ★422(입력 잘못)와 503(우리 장애)을 구분해 준다 — 재시도 여부가 다르다.
            "retryable": e.status_code >= 500,
        }
    except AppError as e:
        return {
            "ok": False,
            "http_status": e.http_status,
            "error_code": e.error_code,
            "error": e.message,
            "retryable": e.http_status >= 500,
        }
    except ValidationError as e:
        return {
            "ok": False,
            "http_status": 422,
            "error": str(e),
            "retryable": False,
        }
    return out.model_dump() if hasattr(out, "model_dump") else out


@mcp.tool()
def precheck(
    insurer: Annotated[str, Field(description="보험사명. 예: NH농협생명")],
    enrolled_on: Annotated[str, Field(description="가입일 YYYYMMDD. 예: 20160301")],
    kcd_codes: Annotated[list[str], Field(description="질병분류기호(KCD) 목록. 예: ['N39.4']")],
    product_name: Annotated[str, Field(description="상품명(알면 후보가 좁혀진다)")] = "",
) -> dict:
    """가입 약관 기준으로 보장 여부를 미리 본다.

    판정은 4단이다 — `likely_covered` / `unlikely` / `needs_documents` / `needs_expert`.
    근거 조항을 못 대면 `needs_expert` 로 **기권한다. 이것은 오류가 아니다.**
    응답의 `abstained`·`reason_code` 로 분기하라. 면책 목록에 없다는 것이
    보장된다는 뜻은 아니다.
    """
    from app.routers import precheck as router
    from app.schemas.precheck import PrecheckRequest

    return _call(
        lambda: router.create_precheck(
            PrecheckRequest(
            insurer=insurer,
            enrolled_on=enrolled_on,
            kcd_codes=kcd_codes,
            product_name=product_name or None,
            client_ref="mcp",
            )
        )
    )


@mcp.tool()
def explain_term(
    message: Annotated[str, Field(description="보험 약관 용어 질문. 예: '통원 뜻'")],
    insurer: Annotated[str, Field(description="보험사로 검색 범위를 좁힐 때만 입력")] = "",
) -> dict:
    """검색된 약관 원문을 인용하고 선택된 LLM으로 쉬운 설명을 만든다.

    `insurance://runtime-config`의 `llm_provider`가 실제 사용 provider다.
    보장 여부 질문은 여기서 답하지 않고 `next_action="precheck_form"`을 반환한다.
    보장 판정은 반드시 `precheck` 도구를 사용하라.
    """
    from app.routers import chat as router

    return _call(
        lambda: router.chat_turn(
            router.ChatRequest(message=message, insurer=insurer or None)
        )
    )


@mcp.tool()
def cohort_stats(
    code: Annotated[str, Field(description="질병분류기호. 예: S72.0")],
    data_source: Annotated[
        str, Field(description="'verified_real'(실제 검증분) 또는 'synthetic'(합성 데모)")
    ] = "verified_real",
    product_id: Annotated[str, Field(description="상품 식별자(선택)")] = "",
    age_band: Annotated[str, Field(description="연령대(선택). 예: 40대")] = "",
) -> dict:
    """같은 조건의 과거 청구 결과 분포를 조회한다.

    ★`data_source` 를 **반드시** 확인하라. `synthetic` 은 시연용 생성 데이터이지
      실제 지급 통계가 아니다. 두 값을 합치지 마라.
    ★표본이 `min_sample` 미만이면 `approval_rate` 가 `null` 이다 —
      비율을 계산해 주지 않는 것이 정상이다. 직접 계산하지 마라.
    ★`by_verification` 에 검증 등급별 건수가 있다. `admin_attested` 는
      관리자 교차검증이지 **발행처 확인이 아니다.**
    """
    from app.core.domain.insurance import DataSource
    from app.routers import cohort as router

    try:
        src = DataSource(data_source)
    except ValueError:
        return {
            "ok": False,
            "http_status": 422,
            "error": f"data_source 는 'verified_real' 또는 'synthetic' 이어야 합니다: {data_source!r}",
            "retryable": False,
        }
    return _call(router._run, src, code, product_id, age_band or None)


@mcp.tool()
def submit_observation(
    client_ref: Annotated[str, Field(description="보고하는 에이전트의 자기 식별자")],
    insurer: Annotated[str, Field(description="보험사명")],
    outcome: Annotated[str, Field(description="paid / denied / partial / pending")],
    kcd_codes: Annotated[list[str], Field(description="질병분류기호 목록")] = [],  # noqa: B006
    enrolled_on: Annotated[str, Field(description="가입일 YYYYMMDD(선택)")] = "",
    outcome_reason: Annotated[str, Field(description="보험사가 밝힌 사유(선택)")] = "",
    precheck_trace_id: Annotated[
        str, Field(description="이 결과와 대조할 사전검토의 trace_id(선택)")
    ] = "",
) -> dict:
    """실제 청구 결과를 보고한다.

    ★접수돼도 **통계에 바로 반영되지 않는다.** 등급은 언제나 `unverified` 이고,
      검수를 거쳐야 코호트에 들어간다. `verification="confirmed"` 를 보내도 무시된다 —
      남이 자기 데이터를 스스로 검증됐다고 하면 그건 검증이 아니다.
    ★같은 내용을 다시 보내면 `duplicate: true` 로 답한다. 재시도가 통계를 부풀리지 않는다.
    """
    from app.routers import precheck as router
    from app.schemas.precheck import ExternalCaseSubmission

    return _call(
        lambda: router.submit_observation(
            ExternalCaseSubmission(
                client_ref=client_ref,
                insurer=insurer,
                enrolled_on=enrolled_on,
                kcd_codes=kcd_codes,
                outcome=outcome,
                outcome_reason=outcome_reason,
                precheck_trace_id=precheck_trace_id or None,
            )
        )
    )


@mcp.resource("insurance://support-manifest")
def support_manifest() -> dict:
    """**무엇을 지원하는지.** 도구를 부르기 전에 이걸 먼저 읽어라.

    지원 범위 밖 보험사를 물으면 기권만 돌아온다. `identification_mode` 도 함께 있다 —
    `auto_approve` 가 참이면 사람 최종승인을 거치지 않은 약관으로 판정한 것이다.
    """
    from app.routers import precheck as router

    return _call(router.support_manifest)


@mcp.resource("insurance://runtime-config")
def runtime_config() -> dict:
    """이 서버가 무엇으로 도는지(키·시크릿 제외).

    ★화이트리스트로만 내보낸다. `get_settings()` 를 통째로 흘리면
      키·경로가 따라 나간다.
    """
    s = get_settings()
    return {
        "brand": s.BRAND_NAME,
        "llm_provider": s.LLM_PROVIDER,
        "tools": ["precheck", "explain_term", "cohort_stats", "submit_observation"],
        "notes": [
            "판정은 약관 원문 조항만을 근거로 한다.",
            "근거를 못 대면 needs_expert 로 기권한다 — 오류가 아니다.",
            "코호트 응답의 data_source 를 절대 무시하지 마라.",
        ],
    }


if __name__ == "__main__":  # pragma: no cover - 프로세스 진입점
    mcp.run()
