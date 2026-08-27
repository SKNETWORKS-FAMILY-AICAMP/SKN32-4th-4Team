"""얼굴 인식/라이브니스 ONNX 모델 다운로드.

- 라이브니스: Silent-Face MiniFASNetV2 (HuggingFace, ~1.7MB, Apache-2.0)
- 인식: AdaFace IR-101 WebFace12M (Google Drive via gdown, ~260MB) — 저품질 벤치마크 SOTA.
  insightface 임베딩 모델(buffalo_l, 검출·정렬용)은 최초 사용 시 자동 다운로드된다.

FACE_RECOGNITION=insightface로 두면 AdaFace 없이도 동작하므로 AdaFace 다운로드는 건너뛴다.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

from app.core.config import get_settings

_ANTISPOOF_URL = (
    "https://huggingface.co/garciafido/minifasnet-v2-anti-spoofing-onnx/"
    "resolve/main/minifasnet_v2.onnx"
)
# AdaFace IR-101 WebFace12M ONNX (InsightFace-REST 배포본, Google Drive).
_ADAFACE_GDRIVE_ID = "1dgMFOASKnaujQcCL4sSYkKOkBrmXUUU1"


def _fetch_http(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 0:
        print(f"[fetch_face_model] 이미 존재: {out} ({out.stat().st_size} bytes)")
        return
    print(f"[fetch_face_model] 다운로드: {url}")
    urllib.request.urlretrieve(url, out)
    print(f"[fetch_face_model] 완료: {out} ({out.stat().st_size} bytes)")


def _fetch_gdrive(file_id: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 0:
        print(f"[fetch_face_model] 이미 존재: {out} ({out.stat().st_size} bytes)")
        return
    try:
        import gdown
    except ImportError as exc:
        raise SystemExit("gdown이 필요합니다: pip install gdown") from exc
    print(f"[fetch_face_model] Google Drive에서 AdaFace 다운로드(약 260MB)…")
    gdown.download(f"https://drive.google.com/uc?id={file_id}", str(out), quiet=False)
    if not out.exists() or out.stat().st_size == 0:
        raise SystemExit("AdaFace ONNX 다운로드 실패")
    print(f"[fetch_face_model] 완료: {out} ({out.stat().st_size} bytes)")


def _fetch_hf(repo: str, filename: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 0:
        print(f"[fetch_face_model] 이미 존재: {out} ({out.stat().st_size} bytes)")
        return
    import shutil

    from huggingface_hub import hf_hub_download

    print(f"[fetch_face_model] HuggingFace에서 다운로드: {repo}/{filename}")
    cached = hf_hub_download(repo, filename)
    shutil.copy(cached, out)
    print(f"[fetch_face_model] 완료: {out} ({out.stat().st_size} bytes)")


def main() -> None:
    settings = get_settings()
    _fetch_http(_ANTISPOOF_URL, settings.FACE_ANTISPOOF_ONNX)
    backend = settings.FACE_RECOGNITION
    if backend == "adaface":
        _fetch_gdrive(_ADAFACE_GDRIVE_ID, settings.FACE_ADAFACE_ONNX)  # AdaFace: Google Drive
    elif backend == "lvface":
        _fetch_hf("bytedance-research/LVFace", "LVFace-S_Glint360K/LVFace-S_Glint360K.onnx",
                  settings.FACE_LVFACE_ONNX)
    else:
        print("[fetch_face_model] FACE_RECOGNITION=insightface — 별도 인식 모델 다운로드 없음.")


if __name__ == "__main__":
    main()
