"""음성 STT/TTS 라우터 (Phase 11)."""

from __future__ import annotations

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.errors import ValidationErr
from app.ml.voice import synthesize_speech, transcribe_audio
from app.routers._uploads import read_capped

router = APIRouter(prefix="/api/voice", tags=["voice"])


class TranscribeResponse(BaseModel):
    text: str
    language: str
    language_probability: float


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1)


@router.post("/stt", response_model=TranscribeResponse)
async def speech_to_text(audio: UploadFile = File(...)) -> TranscribeResponse:
    audio_bytes = await read_capped(audio, get_settings().VOICE_MAX_UPLOAD_BYTES, field="오디오")
    if not audio_bytes:
        raise ValidationErr("업로드된 오디오 파일이 비어 있습니다.")
    result = transcribe_audio(audio_bytes)
    return TranscribeResponse(**result)


@router.post("/tts")
def text_to_speech(body: SynthesizeRequest) -> Response:
    audio_bytes = synthesize_speech(body.text)
    return Response(content=audio_bytes, media_type="audio/wav")
