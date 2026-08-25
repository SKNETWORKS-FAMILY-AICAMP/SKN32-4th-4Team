"""음성 STT/TTS (Phase 11, 로컬 실행).

STT: faster-whisper(CPU, int8) — API 키 불필요, 로컬 모델 가중치만 다운로드.
TTS: pyttsx3(Windows SAPI5) — 완전 오프라인, 네트워크 호출 없음.
둘 다 lazy 로드·싱글턴 캐시(감성분석과 동일 패턴). 모델 로드/보이스 부재는 ConfigError,
빈 입력은 ValidationErr — 조용히 빈 결과나 무음으로 대체하지 않는다(무폴백).
"""

from __future__ import annotations

import io
import wave
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.core.errors import ConfigError, ValidationErr


@lru_cache(maxsize=1)
def _get_stt_model():
    from faster_whisper import WhisperModel

    settings = get_settings()
    try:
        return WhisperModel(
            settings.STT_MODEL,
            device=settings.STT_DEVICE,
            compute_type=settings.STT_COMPUTE_TYPE,
        )
    except Exception as exc:  # noqa: BLE001 - 모델 로드 실패는 명시적 실패
        raise ConfigError(f"STT 모델 로드 실패({settings.STT_MODEL}): {exc}") from exc


def transcribe_audio(audio_bytes: bytes) -> dict[str, Any]:
    if not audio_bytes:
        raise ValidationErr("변환할 오디오 데이터가 비어 있습니다.")

    settings = get_settings()
    model = _get_stt_model()
    segments, info = model.transcribe(io.BytesIO(audio_bytes), language=settings.STT_LANGUAGE)
    text = "".join(segment.text for segment in segments).strip()
    if not text:
        raise ValidationErr("오디오에서 인식된 텍스트가 없습니다(무음이거나 언어 불일치일 수 있음).")

    return {
        "text": text,
        "language": info.language,
        "language_probability": round(float(info.language_probability), 4),
    }


@lru_cache(maxsize=1)
def _get_tts_voice_id() -> str:
    import pyttsx3

    settings = get_settings()
    engine = pyttsx3.init()
    try:
        for voice in engine.getProperty("voices"):
            if settings.TTS_VOICE_MATCH.upper() in voice.id.upper():
                return voice.id
    finally:
        engine.stop()
    raise ConfigError(
        f"'{settings.TTS_VOICE_MATCH}' 보이스를 찾을 수 없습니다. "
        "Windows 설정 > 언어 및 지역에서 한국어 음성팩을 설치하세요."
    )


def synthesize_speech(text: str) -> bytes:
    if not text or not text.strip():
        raise ValidationErr("음성으로 변환할 텍스트가 비어 있습니다.")

    import tempfile
    from pathlib import Path

    import pyttsx3

    voice_id = _get_tts_voice_id()
    engine = pyttsx3.init()
    try:
        engine.setProperty("voice", voice_id)
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "tts_output.wav"
            engine.save_to_file(text, str(out_path))
            engine.runAndWait()
            if not out_path.exists() or out_path.stat().st_size == 0:
                raise ConfigError("TTS 엔진이 오디오 파일을 생성하지 못했습니다.")
            audio_bytes = out_path.read_bytes()
    finally:
        engine.stop()

    _validate_wav(audio_bytes)
    return audio_bytes


def _validate_wav(audio_bytes: bytes) -> None:
    """생성된 바이트가 실제 재생 가능한 WAV인지 확인한다(깨진 파일을 조용히 반환하지 않음)."""
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            if wav_file.getnframes() == 0:
                raise ConfigError("TTS 결과 WAV에 오디오 프레임이 없습니다.")
    except wave.Error as exc:
        raise ConfigError(f"TTS 결과가 유효한 WAV 형식이 아닙니다: {exc}") from exc
