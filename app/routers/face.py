"""얼굴 등록/상태 라우터 (Phase 13) — 모두 로그인 세션 전용(get_current_user).

등록은 이미 인증된 세션에서만 가능해 체인 잠김을 방지한다(미인증 상태에서 얼굴만으로
등록/로그인하는 경로 없음).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel
from app.auth.roles import require_admin
from app.auth.security import get_current_user
from app.auth.store import get_auth_store
from app.core.config import get_settings
from app.core.errors import ValidationErr
from db.sqlite_legacy.models import User
from db.postgres.auth_repository import PgAuthStore, PgUser
from app.ml import face as face_ml
from app.routers._uploads import read_capped
from app.services import face_service

router = APIRouter(prefix="/api/face", tags=["face"])


class FaceStatusResponse(BaseModel):
    registered: bool


class FaceRegisterResponse(BaseModel):
    registered: bool
    shots_used: int
    shots_submitted: int


@router.get("/status", response_model=FaceStatusResponse)
def face_status(
    user: User | PgUser = Depends(get_current_user), store=Depends(get_auth_store)
) -> FaceStatusResponse:
    return FaceStatusResponse(registered=face_service.has_face(store, user.id))


@router.post("/register", response_model=FaceRegisterResponse)
async def face_register(
    images: list[UploadFile] = File(...),
    user: User | PgUser = Depends(get_current_user),
    store=Depends(get_auth_store),
) -> FaceRegisterResponse:
    """다중 이미지 등록(여러 샷을 품질 게이팅 후 임베딩 평균). 단일 샷도 허용."""
    settings = get_settings()
    # 개수 상한(합산 DoS 차단): 파일별 크기 상한 + 장수 상한을 함께 건다.
    if len(images) > settings.FACE_MAX_ENROLL_IMAGES:
        raise ValidationErr(f"등록 이미지는 최대 {settings.FACE_MAX_ENROLL_IMAGES}장까지 허용됩니다.")
    cap = settings.FACE_MAX_UPLOAD_BYTES
    blobs = []
    for f in images:
        b = await read_capped(f, cap, field="얼굴 이미지")
        if b:
            blobs.append(b)
    if not blobs:
        raise ValidationErr("업로드된 이미지가 비어 있습니다.")
    result = face_service.register_face(store, user, blobs)
    return FaceRegisterResponse(**result)


@router.get("/backend")
def get_backend() -> dict:
    """현재 활성 인식 백엔드 + 선택 가능 목록(조회는 공개)."""
    return face_ml.backend_status()


class SetBackendRequest(BaseModel):
    backend: str


@router.put("/backend")
def set_backend(body: SetBackendRequest, _admin: User = Depends(require_admin)) -> dict:
    """활성 인식 백엔드 변경 — **관리자 전용**(전역 인증 설정이므로). 미영속(재시작 시 복귀).

    주의: 백엔드마다 임베딩 공간이 달라 바꾸면 기존 등록 얼굴은 재등록해야 한다.
    """
    return face_ml.set_active_backend(body.backend)


@router.post("/benchmark")
async def face_benchmark(
    image_a: UploadFile = File(...),
    image_b: UploadFile = File(...),
    _admin: User = Depends(require_admin),
) -> dict:
    """두 얼굴 이미지로 인식 백엔드(insightface/adaface/lvface) 성능 실측 비교(코사인·지연).

    3개 모델을 모두 도는 무거운 연산이라 **관리자 전용**(운영 도구)이며 업로드 크기를 상한한다
    — 미인증·대용량 요청으로 인한 모델 연산 DoS 표면을 줄인다(Codex 지적). 모델 파일 없으면
    해당 백엔드는 error로 표시.
    """
    cap = get_settings().FACE_MAX_UPLOAD_BYTES
    a = await read_capped(image_a, cap, field="이미지 A")
    b = await read_capped(image_b, cap, field="이미지 B")
    if not a or not b:
        raise ValidationErr("두 이미지가 모두 필요합니다.")
    return face_ml.benchmark_pair(a, b)


@router.delete("/register", response_model=FaceStatusResponse)
def face_unregister(
    user: User | PgUser = Depends(get_current_user), store=Depends(get_auth_store)
) -> FaceStatusResponse:
    if isinstance(store, PgAuthStore):
        store.delete_face(user.id)
    else:
        from db.sqlite_legacy.models import FaceCredential

        cred = store.query(FaceCredential).filter(FaceCredential.user_id == user.id).first()
        if cred is not None:
            store.delete(cred)
            store.commit()
    return FaceStatusResponse(registered=False)
