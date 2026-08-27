"""얼굴 임베딩 + 패시브 라이브니스 + 품질 게이팅 (Phase 13, 로컬 CPU).

파이프라인(무폴백): 디코드 → 대상 얼굴(여러 명이면 **가장 앞=가장 큰 얼굴** 선택) →
**품질 게이팅**(흐림·밝기·크기·자세·검출신뢰도) → 라이브니스 → 정렬(112×112) → 저조도 조건부
CLAHE → 임베딩. 저품질은 조용히 통과시키지 않고 명시적 재촬영 사유를 돌려준다(ValidationErr).
모델 로드 실패는 ConfigError.

인식 백엔드(실측 근거): 검출·정렬은 insightface RetinaFace(우수) 유지, **인식 임베딩은 기본
AdaFace IR-101(WebFace12M)**. config FACE_RECOGNITION으로 adaface/lvface/insightface 선택.
동일 셋업 열화 매칭 실측(동일인 코사인, 높을수록 좋음):
    degrade      buffalo_l  AdaFace  LVFace-S  LVFace-B
    블러 k21       0.578    0.665    0.379     0.279
    저조도 ×0.12    0.869    0.923    0.881     0.880
    저해상 0.15배   0.299    0.389    0.120     0.057
→ **AdaFace가 저품질 전 항목 최강.** LVFace는 일반/고품질 벤치마크(MR-All) SOTA이자 CPU가 빠르나
(~96ms vs AdaFace ~550ms) **저품질에선 buffalo_l보다도 약함** — 웹캠 로그인처럼 저품질이 실제
조건인 용도엔 AdaFace가 최선(LVFace의 SOTA는 고품질 한정). 그 외 병목은 정보 손실·등록 오염이라
품질 게이팅 + 다중 등록이 실질 레버. CLAHE는 정상광에선 임베딩을 흔들어(실측) 저조도만 조건부 적용.

한계: 라이브니스는 Silent-Face 단일 모델(앙상블 아님)이고 이 환경에서 실 라이브/사진 쌍으로
정확도 검증 불가. insightface 사전학습 모델·AdaFace 모두 연구/비상업 성격 — 상용 시 라이선스
확인 필요. AdaFace ONNX는 Google Drive 배포본이라 다운로드 재현성이 HTTP 호스팅보다 약함(문서화).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np

from app.core.config import get_settings
from app.core.errors import ConfigError, ValidationErr


@lru_cache(maxsize=1)
def _get_face_app():
    try:
        from insightface.app import FaceAnalysis
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"insightface import 실패: {exc}") from exc

    settings = get_settings()
    try:
        app = FaceAnalysis(name=settings.FACE_EMBED_MODEL, providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=-1, det_size=(640, 640))
    except Exception as exc:  # noqa: BLE001 - 모델 준비 실패는 명시적 실패
        raise ConfigError(f"얼굴 임베딩 모델 로드 실패({settings.FACE_EMBED_MODEL}): {exc}") from exc
    return app


def _resolve_providers(requested: list[str]) -> list[str]:
    """요청 EP 중 이 설치에서 실제 가용한 것만 남긴다(항상 CPU 보장).

    plain onnxruntime(CPU 전용)이면 DmlExecutionProvider가 자동 제외돼 CPU로 돈다 —
    존재하지 않는 EP를 넘겨 세션 생성이 깨지는 걸 막는 것(조용한 성능 저하 아님, 가용성 기반).
    """
    import onnxruntime as ort

    available = set(ort.get_available_providers())
    resolved = [p for p in requested if p in available]
    if "CPUExecutionProvider" not in resolved:
        resolved.append("CPUExecutionProvider")
    return resolved


@lru_cache(maxsize=1)
def _get_antispoof_session():
    import onnxruntime as ort

    settings = get_settings()
    path = settings.FACE_ANTISPOOF_ONNX
    if not path.exists():
        raise ConfigError(
            f"라이브니스 ONNX 모델이 없습니다: {path}. "
            "`python -m scripts.fetch_face_model`로 내려받으세요."
        )
    try:
        return ort.InferenceSession(
            str(path), providers=_resolve_providers(settings.FACE_LIVENESS_PROVIDERS)
        )
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"라이브니스 모델 로드 실패: {exc}") from exc


@lru_cache(maxsize=2)
def _get_onnx_recognizer(path_str: str):
    import onnxruntime as ort

    from pathlib import Path

    path = Path(path_str)
    if not path.exists():
        raise ConfigError(
            f"인식 ONNX 모델이 없습니다: {path}. "
            "`python -m scripts.fetch_face_model`로 내려받으세요(또는 config FACE_RECOGNITION=insightface)."
        )
    try:
        return ort.InferenceSession(
            str(path), providers=_resolve_providers(get_settings().FACE_RECOG_PROVIDERS)
        )
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"인식 모델 로드 실패({path.name}): {exc}") from exc


def _decode_image(image_bytes: bytes) -> np.ndarray:
    if not image_bytes:
        raise ValidationErr("이미지 데이터가 비어 있습니다.")
    import cv2

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)  # BGR
    if img is None:
        raise ValidationErr("이미지를 디코드할 수 없습니다(형식 확인).")
    return img


def _detect_primary_face(bgr: np.ndarray):
    """대상 얼굴을 고른다. 여러 명이 잡히면 **가장 앞(=bbox 면적 최대, 카메라에 가장 가까운)**
    사람을 선택한다(배경 인물 무시). 얼굴이 하나도 없으면 재촬영 요구(무폴백).
    """
    app = _get_face_app()
    faces = app.get(bgr)
    if not faces:
        raise ValidationErr("얼굴이 감지되지 않았습니다. 얼굴을 화면 중앙에 맞춰 다시 촬영하세요.")
    if len(faces) > 1:
        # 면적이 큰 얼굴 = 카메라에 가까운(앞에 있는) 사람.
        faces = sorted(
            faces,
            key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
            reverse=True,
        )
    return faces[0]


def _aligned_crop(bgr: np.ndarray, face) -> np.ndarray:
    from insightface.utils import face_align

    return face_align.norm_crop(bgr, face.kps, image_size=112)  # BGR 112×112


def _check_quality(bgr: np.ndarray, face, aligned: np.ndarray, *, strict: bool) -> None:
    """품질 미달이면 조용히 통과시키지 않고 사유를 담아 ValidationErr(무폴백)."""
    import cv2

    settings = get_settings()
    q = settings.FACE_QUALITY_REGISTER if strict else settings.FACE_QUALITY_VERIFY

    face_px = float(face.bbox[2] - face.bbox[0])
    if face_px < q["min_face_px"]:
        raise ValidationErr("얼굴이 너무 작습니다. 카메라에 조금 더 가까이 와서 다시 촬영하세요.")

    det = float(getattr(face, "det_score", 1.0))
    if det < q["min_det"]:
        raise ValidationErr("얼굴이 뚜렷하지 않습니다. 정면을 보고 다시 촬영하세요.")

    gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if blur < q["min_blur"]:
        raise ValidationErr("이미지가 흐릿합니다. 흔들림 없이 초점을 맞춰 다시 촬영하세요.")

    bright = float(gray.mean())
    if bright < q["min_bright"]:
        raise ValidationErr("너무 어둡습니다. 조명을 밝게 하고 다시 촬영하세요.")
    if bright > q["max_bright"]:
        raise ValidationErr("너무 밝습니다(과노출). 조명을 낮추고 다시 촬영하세요.")

    # pose = [pitch, yaw, roll] (insightface). roll은 정렬로 보정되므로 pitch·yaw만 본다.
    pose = getattr(face, "pose", None)
    if pose is not None:
        pitch, yaw = abs(float(pose[0])), abs(float(pose[1]))
        if yaw > q["max_yaw"] or pitch > q["max_pitch"]:
            raise ValidationErr("고개가 많이 돌아가 있습니다. 정면을 바라보고 다시 촬영하세요.")


def _maybe_clahe(aligned: np.ndarray) -> np.ndarray:
    """정렬 crop 평균 밝기가 임계 미만(저조도)일 때만 luminance에 약한 CLAHE(등록·검증 동일)."""
    import cv2

    settings = get_settings()
    gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
    if float(gray.mean()) >= settings.FACE_CLAHE_BRIGHTNESS:
        return aligned  # 정상광엔 적용 안 함(정상광 CLAHE는 임베딩을 흔든다 — 실측)
    lab = cv2.cvtColor(aligned, cv2.COLOR_BGR2LAB)
    lch, ach, bch = cv2.split(lab)
    lch = cv2.createCLAHE(clipLimit=settings.FACE_CLAHE_CLIP, tileGridSize=(8, 8)).apply(lch)
    return cv2.cvtColor(cv2.merge((lch, ach, bch)), cv2.COLOR_LAB2BGR)


def _embed(aligned: np.ndarray, backend: str | None = None) -> np.ndarray:
    """정렬된 112×112 BGR crop → 정규화 512차원 임베딩.

    backend 미지정 시 config FACE_RECOGNITION 사용(기본 AdaFace). 벤치마크는 backend를 명시.
    """
    import cv2

    settings = get_settings()
    backend = backend or settings.FACE_RECOGNITION
    if backend in ("adaface", "lvface"):
        # AdaFace·LVFace 동일 전처리: RGB 112, [-1,1].
        path = settings.FACE_ADAFACE_ONNX if backend == "adaface" else settings.FACE_LVFACE_ONNX
        sess = _get_onnx_recognizer(str(path))
        rgb = cv2.cvtColor(aligned, cv2.COLOR_BGR2RGB).astype(np.float32)
        rgb = ((rgb / 255.0) - 0.5) / 0.5
        blob = rgb.transpose(2, 0, 1)[None]
        feat = sess.run(None, {sess.get_inputs()[0].name: blob})[0][0].astype(np.float32)
    elif backend == "insightface":
        rec = _get_face_app().models["recognition"]
        feat = rec.get_feat(aligned).flatten().astype(np.float32)
    else:
        raise ConfigError(f"알 수 없는 FACE_RECOGNITION 백엔드: {backend}")

    norm = float(np.linalg.norm(feat))
    if norm == 0.0:
        raise ValidationErr("임베딩을 계산할 수 없습니다. 다시 촬영하세요.")
    return feat / norm


#: MiniFASNet 출력 축 순서: [2D-spoof, live, 3D-spoof]. **1 이 live 다.**
#: 원본 Silent-Face 구현도 `argmax == 1` 일 때만 real 로 판정한다.
_LIVE_CLASS = 1


def _liveness_prob(bgr: np.ndarray, bbox: np.ndarray) -> float:
    """live 클래스 확률(0~1). 값이 클수록 실제 촬영일 가능성이 높다."""
    import cv2

    settings = get_settings()
    h, w = bgr.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in bbox]
    lx, ly, rx, ry = _get_new_box(w, h, (x1, y1, x2 - x1, y2 - y1), settings.FACE_ANTISPOOF_SCALE)
    crop = bgr[ly:ry, lx:rx]
    if crop.size == 0:
        raise ValidationErr("얼굴 영역을 잘라낼 수 없습니다. 다시 촬영하세요.")
    crop = cv2.resize(crop, (80, 80))  # BGR 80x80
    blob = crop.astype(np.float32).transpose(2, 0, 1)[None]  # NCHW

    sess = _get_antispoof_session()
    out = sess.run(None, {sess.get_inputs()[0].name: blob})[0][0]
    e = np.exp(out - out.max())
    prob = e / e.sum()

    #: ★★**클래스 순서를 틀리고 있었다 — 얼굴 등록이 아무도 안 됐다(2026-08-04).**
    #:
    #:   전에는 `prob[0]` 을 live 로 읽고 주석에 `[live, print, replay]` 라 적어 두었다.
    #:   MiniFASNet(Silent-Face) 의 실제 출력은 **`[2D-spoof, live, 3D-spoof]`** 이고,
    #:   원본 구현도 `label == 1` 일 때만 real 로 판정한다.
    #:
    #:   실측 — 실제 사람 얼굴 5장:
    #:       p0 = 0.0001 ~ 0.0033   (우리가 live 라고 읽던 값)
    #:       p1 = 0.62   ~ 0.996    (진짜 live 값)
    #:   임계값이 0.50 이므로 **어떤 실제 얼굴도 통과할 수 없었다.** 등록 100% 실패.
    #:
    #:   ★테스트가 왜 못 잡았나 — `relax_liveness` fixture 가 임계값을 **0.0 으로 낮춰**
    #:     `p0 >= 0.0` 을 항상 참으로 만들었다. 임계값을 낮춰 초록불을 만드는 것이
    #:     정확히 이 결함을 가렸다. 그래서 아래 `_LIVE_CLASS` 를 상수로 꺼내고,
    #:     "실제 얼굴은 **기본 임계값에서** 통과해야 한다"를 시험으로 고정한다.
    return float(prob[_LIVE_CLASS])


def _get_new_box(src_w: int, src_h: int, bbox_xywh, scale: float):
    """Silent-Face 원본 크롭 로직 — bbox 중심 기준 scale배 확대 후 이미지 경계로 클램프."""
    x, y, bw, bh = bbox_xywh
    scale = min((src_h - 1) / bh, (src_w - 1) / bw, scale)
    nw, nh = bw * scale, bh * scale
    cx, cy = x + bw / 2, y + bh / 2
    lx, ly = cx - nw / 2, cy - nh / 2
    rx, ry = cx + nw / 2, cy + nh / 2
    if lx < 0:
        rx -= lx
        lx = 0
    if ly < 0:
        ry -= ly
        ly = 0
    if rx > src_w - 1:
        lx -= rx - (src_w - 1)
        rx = src_w - 1
    if ry > src_h - 1:
        ly -= ry - (src_h - 1)
        ry = src_h - 1
    return int(lx), int(ly), int(rx), int(ry)


def analyze_face(image_bytes: bytes, *, strict: bool = False) -> dict[str, Any]:
    """디코드 → 단일 얼굴 → 품질 게이팅 → 라이브니스 → 정렬 → 조건부 CLAHE → 임베딩.

    게이트 순서를 이 함수가 강제한다. 품질 미달/얼굴 문제는 ValidationErr(명시적 재촬영),
    라이브니스 통과 여부 판정은 호출자가 하도록 확률·bool을 함께 돌려준다.
    """
    settings = get_settings()
    bgr = _decode_image(image_bytes)
    face = _detect_primary_face(bgr)
    aligned = _aligned_crop(bgr, face)
    _check_quality(bgr, face, aligned, strict=strict)  # 품질 미달이면 여기서 실패
    live_prob = _liveness_prob(bgr, face.bbox)
    is_live = live_prob >= settings.FACE_LIVENESS_THRESHOLD
    embedding = _embed(_maybe_clahe(aligned))
    return {"embedding": embedding, "live_prob": round(live_prob, 4), "is_live": is_live}


RECOGNITION_BACKENDS = ("insightface", "adaface", "lvface")

# 백엔드별 한 줄 설명(UI 표시용).
BACKEND_NOTES = {
    "insightface": "ArcFace r50 · 기준선 · 빠름",
    "adaface": "AdaFace IR-101 · 저품질 강함 · CPU 느림",
    "lvface": "LVFace ViT-S · 고품질 벤치 SOTA · 빠름 · 저품질 약함",
}


def _backend_available(backend: str) -> bool:
    s = get_settings()
    if backend == "insightface":
        return True  # buffalo_l은 최초 사용 시 자동 다운로드
    if backend == "adaface":
        return s.FACE_ADAFACE_ONNX.exists()
    if backend == "lvface":
        return s.FACE_LVFACE_ONNX.exists()
    return False


def backend_status() -> dict[str, Any]:
    s = get_settings()
    return {
        "active": s.FACE_RECOGNITION,
        "match_threshold": s.FACE_MATCH_THRESHOLD,
        "backends": [
            {"name": be, "available": _backend_available(be), "note": BACKEND_NOTES[be]}
            for be in RECOGNITION_BACKENDS
        ],
    }


def set_active_backend(backend: str) -> dict[str, Any]:
    """런타임에 활성 인식 백엔드를 바꾼다(프로세스 한정·미영속 — 재시작 시 config값으로 복귀).

    무폴백: 알 수 없는 값·모델 파일 부재는 명시적 오류. 주의: 백엔드마다 임베딩 공간이 달라
    **기존 등록 얼굴은 재등록해야 한다**(호출자/문서가 경고).
    """
    if backend not in RECOGNITION_BACKENDS:
        raise ValidationErr(f"지원하지 않는 백엔드: {backend}")
    if not _backend_available(backend):
        raise ConfigError(
            f"'{backend}' 모델 파일이 없습니다. `python -m scripts.fetch_face_model`로 내려받으세요."
        )
    get_settings().FACE_RECOGNITION = backend
    return backend_status()


def _align_from_bytes(image_bytes: bytes) -> np.ndarray:
    """벤치마크용: 디코드 → 대상(앞) 얼굴 → 정렬 crop. 품질 게이트는 걸지 않는다(모델 차이만 관찰)."""
    bgr = _decode_image(image_bytes)
    face = _detect_primary_face(bgr)
    return _aligned_crop(bgr, face)


def benchmark_pair(image_a: bytes, image_b: bytes) -> dict[str, Any]:
    """같은 정렬 crop에 대해 각 인식 백엔드의 코사인 유사도와 임베딩 지연(ms)을 실측한다.

    A=등록샷, B=로그인샷처럼 두 장을 주면 백엔드별 '이 둘을 동일인으로 보는 정도'와 속도를 비교.
    모델 파일이 없는 백엔드는 error로 표시(무폴백: 조용히 건너뛰지 않음)."""
    import time

    settings = get_settings()
    aa = _maybe_clahe(_align_from_bytes(image_a))
    ab = _maybe_clahe(_align_from_bytes(image_b))

    results = []
    for be in RECOGNITION_BACKENDS:
        try:
            t0 = time.perf_counter()
            ea = _embed(aa, backend=be)
            eb = _embed(ab, backend=be)
            ms = (time.perf_counter() - t0) / 2 * 1000  # 임베딩 1장당 평균 ms
            results.append({
                "backend": be,
                "cosine": round(cosine_similarity(ea, eb), 4),
                "ms_per_embed": round(ms, 1),
                "match": cosine_similarity(ea, eb) >= settings.FACE_MATCH_THRESHOLD,
                "error": None,
            })
        except Exception as exc:  # noqa: BLE001 - 모델 부재 등은 error로 노출
            results.append({"backend": be, "cosine": None, "ms_per_embed": None,
                            "match": None, "error": str(exc)[:120]})
    return {
        "active_backend": settings.FACE_RECOGNITION,
        "match_threshold": settings.FACE_MATCH_THRESHOLD,
        "results": results,
    }


def average_embeddings(embeddings: list[np.ndarray]) -> np.ndarray:
    """다중 등록: 품질 통과 임베딩들을 평균 후 재정규화(견고한 기준 임베딩)."""
    if not embeddings:
        raise ValidationErr("등록에 사용할 유효한 얼굴이 없습니다.")
    mean = np.mean(np.stack(embeddings), axis=0).astype(np.float32)
    norm = float(np.linalg.norm(mean))
    if norm == 0.0:
        raise ValidationErr("등록 임베딩을 계산할 수 없습니다. 다시 촬영하세요.")
    return mean / norm


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def embedding_to_bytes(embedding: np.ndarray) -> bytes:
    return np.asarray(embedding, dtype=np.float32).tobytes()


def embedding_from_bytes(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
