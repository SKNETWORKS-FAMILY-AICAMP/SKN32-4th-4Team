"""얼굴 2차 인증 서비스(Phase 13) — 등록/검증 + 게이트 순서 + 감사·시도 제한.

게이트 순서(Codex 권고): 촬영 품질/단일 얼굴 → 라이브니스 → 임베딩 비교 → 통과.
어느 단계든 실패하면 토큰 없이 실패시키고, 라이브니스·매칭 실패는 **동일한 일반 메시지**로
반환(정보 노출 최소화)하며 서버에는 사유별 감사 이벤트를 남긴다. 얼굴 실패 시 비밀번호만으로
폴백하지 않는다(무폴백).
"""

from __future__ import annotations

import time

from app.auth.user_types import AuthStore, AuthUser

from app.core.config import get_settings
from app.core.errors import AuthErr, ForbiddenErr, ValidationErr
from app.ml.face import (
    analyze_face,
    average_embeddings,
    cosine_similarity,
    embedding_from_bytes,
    embedding_to_bytes,
)
from app.obs.events import record_event
from db.postgres.auth_repository import PgAuthStore, PgFaceCredential, PgUser

# 일반화된 실패 메시지(라이브니스/매칭 어느 쪽이 실패했는지 노출하지 않음).
_GENERIC_FAIL = "얼굴 인증에 실패했습니다. 다시 시도해주세요."

# 데모용 인메모리 시도 제한(프로세스 재시작 시 초기화·멀티프로세스 미공유 — 한계 문서화).
_attempts: dict[object, list[float]] = {}
_WINDOW_SEC = 300.0


def _legacy_face_credential():
    """레거시 SQLite 모델을 **부를 때** 적재한다.

    ★최상단 import 는 PostgreSQL 전용 배포에도 SQLAlchemy 를 통째로 끌어온다
      (`app/auth/user_types.py` — 그것 때문에 기동이 막힌 적이 있다).
    """
    from db.sqlite_legacy.models import FaceCredential

    return FaceCredential


def has_face(db: AuthStore, user_id) -> bool:
    if isinstance(db, PgAuthStore):
        return db.get_face(user_id) is not None
    FaceCredential = _legacy_face_credential()
    return db.query(FaceCredential).filter(FaceCredential.user_id == user_id).first() is not None


def register_face(db: AuthStore, user: AuthUser, images: list[bytes]) -> dict:
    """로그인된 세션에서만 호출됨(라우터가 get_current_user로 보장).

    다중 이미지 등록(Codex 권고): 각 샷을 **엄격 품질 게이팅**(strict) + 라이브니스로 거른 뒤
    통과분의 임베딩을 평균한다 — 나쁜 기준 임베딩이 이후 매칭을 오염시키는 걸 막는다. 품질 미달은
    조용히 넘기지 않고 재촬영 사유를 그대로 전파(무폴백).
    """
    if not images:
        raise ValidationErr("등록할 이미지가 없습니다.")

    embeddings = []
    quality_passed = 0
    last_quality_err: ValidationErr | None = None
    for img in images:
        try:
            result = analyze_face(img, strict=True)  # 품질 미달이면 ValidationErr
        except ValidationErr as exc:
            last_quality_err = exc  # 이 샷은 재촬영 필요 — 다음 샷 시도
            continue
        quality_passed += 1
        if result["is_live"]:
            embeddings.append(result["embedding"])

    if quality_passed == 0:
        # 어떤 샷도 품질을 통과 못함 → 사용성 사유를 명시(재촬영 안내).
        raise last_quality_err or ValidationErr("등록할 얼굴 품질이 충분하지 않습니다. 다시 촬영하세요.")
    if not embeddings:
        # 품질은 통과했으나 라이브니스 전멸 → 위조 신호(일반 메시지).
        record_event(db, "face_register_liveness_fail", {"user_id": user.id})
        raise AuthErr(_GENERIC_FAIL)

    blob = embedding_to_bytes(average_embeddings(embeddings))
    if isinstance(db, PgAuthStore):
        db.upsert_face(user.id, blob)
        record_event(db, "face_registered", {"user_id": user.id, "shots_used": len(embeddings)})
        return {"registered": True, "shots_used": len(embeddings), "shots_submitted": len(images)}

    FaceCredential = _legacy_face_credential()
    cred = db.query(FaceCredential).filter(FaceCredential.user_id == user.id).first()
    if cred is None:
        cred = FaceCredential(user_id=user.id, embedding=blob)
        db.add(cred)
    else:
        cred.embedding = blob  # 재등록(덮어쓰기)
    db.commit()
    record_event(db, "face_registered", {"user_id": user.id, "shots_used": len(embeddings)})
    return {"registered": True, "shots_used": len(embeddings), "shots_submitted": len(images)}


def _check_attempts(db: AuthStore, user_id) -> None:
    settings = get_settings()
    now = time.monotonic()
    hist = [t for t in _attempts.get(user_id, []) if now - t < _WINDOW_SEC]
    _attempts[user_id] = hist
    if len(hist) >= settings.FACE_MAX_ATTEMPTS:
        record_event(db, "face_verify_locked", {"user_id": user_id, "attempts": len(hist)})
        raise ForbiddenErr("얼굴 인증 시도 횟수를 초과했습니다. 잠시 후 다시 시도해주세요.")


def _record_attempt(user_id: int) -> None:
    _attempts.setdefault(user_id, []).append(time.monotonic())


def _clear_attempts(user_id: int) -> None:
    _attempts.pop(user_id, None)


def verify_face(db: AuthStore, user: AuthUser, image_bytes: bytes) -> None:
    """2차 인증: 라이브니스 → 임베딩 비교. 실패 시 AuthErr(일반 메시지). 성공 시 조용히 반환."""
    _check_attempts(db, user.id)

    if isinstance(db, PgAuthStore):
        cred = db.get_face(user.id)
    else:
        FaceCredential = _legacy_face_credential()
        cred = db.query(FaceCredential).filter(FaceCredential.user_id == user.id).first()
    if cred is None:
        # 이 경로는 라우터가 이미 has_face로 거르지만 방어적으로 처리.
        raise AuthErr(_GENERIC_FAIL)

    result = analyze_face(image_bytes)  # 품질/단일얼굴 실패는 ValidationErr로 그대로 전파(사용성)
    if not result["is_live"]:
        _record_attempt(user.id)
        record_event(db, "face_verify_liveness_fail", {"user_id": user.id, "live_prob": result["live_prob"]})
        raise AuthErr(_GENERIC_FAIL)

    stored = embedding_from_bytes(cred.embedding)
    sim = cosine_similarity(result["embedding"], stored)
    if sim < get_settings().FACE_MATCH_THRESHOLD:
        _record_attempt(user.id)
        record_event(db, "face_verify_mismatch", {"user_id": user.id, "similarity": round(sim, 4)})
        raise AuthErr(_GENERIC_FAIL)

    _clear_attempts(user.id)
    record_event(db, "face_verify_ok", {"user_id": user.id, "similarity": round(sim, 4)})
