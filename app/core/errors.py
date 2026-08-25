"""예외 taxonomy 및 FastAPI 예외 핸들러 등록.

RULE.md 3.2(폴백 금지)와 통합 계획서 §6을 구현한다. 오류를 삼켜 그럴듯한 가짜
결과로 대체하지 않고, 정의된 HTTP 상태 + 구조화된 본문 {ok, error_code, message}로
명확히 실패시킨다.

이 모듈은 **모듈 레벨에서 프레임워크를 import하지 않는다**(Application 계층이 예외
타입만 안전히 import할 수 있게 — Clean Architecture 경계). FastAPI 의존은
register_exception_handlers 내부에서 지연 import한다.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """애플리케이션 공통 예외 베이스."""

    http_status: int = 500
    error_code: str = "app_error"

    def __init__(self, message: str, *, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.message = message
        self.headers = headers or {}


class ConfigError(AppError):
    """설정/키 부재 등 서비스 의존성 미준비 (폴백 대신 명시적 실패)."""

    http_status = 503
    error_code = "config_error"


class InfraError(AppError):
    """DB/외부 인프라 연결 실패."""

    http_status = 503
    error_code = "infra_error"


class ArtifactMissing(InfraError):
    """**산출물이 아직 없다.** 저장소가 죽은 것이 아니다.

    ★이 둘을 가르는 것이 핵심이다(`app/core/usecases/precheck.py`) —
      「없다」는 **사실**이라 판정이 **기권**해야 하고(HTTP 200),
      「죽었다」는 **장애**라 503 으로 올려야 한다.
      재시도로 해결되는 것도 후자뿐이다. 「적재 전」을 503 으로 내보내면
      클라이언트가 잠시 뒤 다시 부르지만 결과는 영원히 같다.

    ★★**메시지 문자열로 가르던 것을 대체한다**(2026-08-25).
      `_MISSING_HINTS` 로 문구를 맞춰 보는 방식이었는데, PG 조항 저장소가
      「이 약관의 조항 기록이 없습니다」라고 **다른 문구**를 쓰는 바람에
      그 경로만 503 이 나갔다 — 같은 상황에서 파일 저장소는 200 기권이었다.
      **같은 사실에 두 응답**이 나가는 것은 계약 위반이다.
      (교체 계획은 `usecases/precheck.py` 주석에 이미 적혀 있었다.)

    상속은 `InfraError` 를 유지한다 — 유스케이스가 잡지 못하고 HTTP 까지 올라가면
    그때는 503 이 맞다. 기권으로 바꾸는 책임은 **판정 유스케이스**에 있다.
    """

    error_code = "artifact_missing"


class TransientInfraError(InfraError):
    """serialization/deadlock/일시적 자원 부족처럼 재시도 가능한 인프라 실패."""

    error_code = "transient_infra_error"

    def __init__(self, message: str, *, retry_after_seconds: int = 1):
        retry_after = max(1, int(retry_after_seconds))
        super().__init__(message, headers={"Retry-After": str(retry_after)})
        self.retry_after_seconds = retry_after


class LLMOutputError(AppError):
    """LLM 출력이 스키마 검증을 통과하지 못함 (재시도 후 최종 실패)."""

    http_status = 502
    error_code = "llm_output_error"


class ValidationErr(AppError):
    """사용자 입력 검증 실패."""

    http_status = 422
    error_code = "validation_error"


class AuthErr(AppError):
    """인증 실패 (토큰 없음/무효/만료)."""

    http_status = 401
    error_code = "auth_error"

    def __init__(self, message: str):
        super().__init__(message, headers={"WWW-Authenticate": "Bearer"})


class ForbiddenErr(AppError):
    """권한 없음 (타인 리소스 접근 등)."""

    http_status = 403
    error_code = "forbidden"


class NotFoundErr(AppError):
    """리소스 없음."""

    http_status = 404
    error_code = "not_found"


class ConflictErr(AppError):
    """리소스 충돌 (같은 멱등키·다른 payload 등)."""

    http_status = 409
    error_code = "conflict"


class RateLimitErr(AppError):
    """등록 에이전트 요청 한도 초과."""

    http_status = 429
    error_code = "rate_limit_exceeded"

    def __init__(self, message: str, *, retry_after_seconds: int = 60):
        retry_after = max(1, int(retry_after_seconds))
        super().__init__(message, headers={"Retry-After": str(retry_after)})
        self.retry_after_seconds = retry_after


_PUBLIC_MESSAGES: dict[type[AppError], str] = {
    ConfigError: "서비스 설정이 준비되지 않았습니다.",
    InfraError: "서비스 의존 시스템을 사용할 수 없습니다.",
    LLMOutputError: "응답 생성에 실패했습니다.",
}


def public_error_message(exc: AppError) -> str:
    """외부 응답에 실어도 되는 오류 문구만 반환한다.

    설정·인프라 예외에는 DSN, 파일 경로, 드라이버 메시지가 포함될 수 있다. 입력·인증
    오류는 사용자가 고칠 수 있어 원문을 유지하고, 내부 장애만 고정 문구로 바꾼다.
    """
    for error_type, message in _PUBLIC_MESSAGES.items():
        if isinstance(exc, error_type):
            return message
    return exc.message


def register_exception_handlers(app: Any) -> None:
    """AppError 계열을 정의된 HTTP 상태 + 구조화 본문으로 매핑한다.

    FastAPI는 Interface 계층 관심사이므로 여기서 지연 import한다(모듈 레벨 순수 유지).
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "ok": False,
                "error_code": exc.error_code,
                "message": public_error_message(exc),
            },
            headers=exc.headers,
        )
