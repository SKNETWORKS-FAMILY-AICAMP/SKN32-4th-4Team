"""음성 STT/TTS 테스트 (실 faster-whisper/pyttsx3, @ml — CI 제외).

실행: pytest -m ml tests/test_voice.py
"""

import pytest

from app.core.errors import ValidationErr
from app.ml.voice import synthesize_speech, transcribe_audio

pytestmark = pytest.mark.ml


def test_tts_then_stt_round_trip():
    """TTS로 만든 음성을 STT로 되돌렸을 때 원문이 인식되는지(실 모델 왕복 검증)."""
    original = "안녕하세요 주문 상태를 확인하고 싶습니다"
    audio_bytes = synthesize_speech(original)
    assert len(audio_bytes) > 0

    result = transcribe_audio(audio_bytes)
    assert result["language"] == "ko"
    assert "주문" in result["text"]


def test_stt_empty_raises():
    with pytest.raises(ValidationErr):
        transcribe_audio(b"")


def test_tts_empty_raises():
    with pytest.raises(ValidationErr):
        synthesize_speech("   ")
