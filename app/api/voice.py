"""
POST /api/v1/voice/capture — ephemeral voice upload (Redis only, no PostgreSQL).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.dependencies import get_current_doctor
from app.core.tenant import (
    legacy_redis_transcript_key,
    legacy_redis_voice_key,
    redis_transcript_key,
    redis_voice_key,
)
from app.config import get_settings, Settings
from app.models import Doctor
from app.schemas import (
    VoiceCaptureResponse,
    VoiceTranscribeRequest,
    VoiceTranscribeResponse,
)
from app.services.audio_convert import (
    convert_to_wav_16k_mono,
    is_allowed_audio_type,
    suffix_for_audio_upload,
    wav_duration_seconds,
)
from app.services.redis_client import get_redis
from app.services.stt_service import transcribe_wav_dual_engine

logger = logging.getLogger(__name__)
router = APIRouter()

VOICE_TTL_SECONDS = 600  # 10 minutes
TRANSCRIPT_TTL_SECONDS = 600
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
STT_SLA_MS = 8_000

ALLOWED_CONTENT_TYPES = {
    "audio/webm",
    "audio/mp4",
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/ogg",
    "application/octet-stream",
}


def _suffix_for_upload(content_type: str | None, filename: str | None) -> str:
    return suffix_for_audio_upload(content_type, filename)


@router.post(
    "/voice/capture",
    response_model=VoiceCaptureResponse,
    summary="Upload voice capture (ephemeral Redis storage)",
)
async def capture_voice(
    audio: UploadFile = File(..., description="audio/webm, audio/mp4, or audio/wav"),
    doctor: Doctor = Depends(get_current_doctor),
):
    if audio.content_type and not is_allowed_audio_type(audio.content_type, ALLOWED_CONTENT_TYPES):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported audio format: {audio.content_type}. Use webm, mp4, or wav.",
        )

    raw_bytes = await audio.read()
    if len(raw_bytes) < 100:
        raise HTTPException(status_code=400, detail="Audio file is empty.")
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Audio file exceeds 50 MB limit.")

    recording_id = str(uuid.uuid4())
    suffix = _suffix_for_upload(audio.content_type, audio.filename)

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / f"input{suffix}"
        wav_path = Path(tmpdir) / "output.wav"
        input_path.write_bytes(raw_bytes)

        try:
            await asyncio.to_thread(convert_to_wav_16k_mono, input_path, wav_path)
            duration = await asyncio.to_thread(wav_duration_seconds, wav_path)
            wav_bytes = wav_path.read_bytes()
        except (RuntimeError, FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    redis_key = redis_voice_key(doctor.id, recording_id)
    try:
        redis_client = get_redis()
        await asyncio.to_thread(
            redis_client.setex,
            redis_key,
            VOICE_TTL_SECONDS,
            wav_bytes,
        )
    except Exception as exc:
        logger.exception("Redis store failed for %s", redis_key)
        raise HTTPException(status_code=503, detail="Voice storage unavailable.") from exc

    return VoiceCaptureResponse(
        recording_id=recording_id,
        duration_seconds=round(duration, 2),
    )


@router.post(
    "/voice/transcribe",
    response_model=VoiceTranscribeResponse,
    summary="Transcribe a captured recording (Whisper + Sarvam fallback)",
)
async def transcribe_voice(
    body: VoiceTranscribeRequest,
    settings: Settings = Depends(get_settings),
    doctor: Doctor = Depends(get_current_doctor),
):
    redis_client = get_redis()
    voice_key = redis_voice_key(doctor.id, body.recording_id)
    wav_bytes = await asyncio.to_thread(redis_client.get, voice_key)
    if not wav_bytes:
        wav_bytes = await asyncio.to_thread(
            redis_client.get, legacy_redis_voice_key(body.recording_id)
        )
    if not wav_bytes:
        raise HTTPException(status_code=404, detail="Recording not found or expired.")

    try:
        result = await transcribe_wav_dual_engine(
            wav_bytes, settings.sarvam_api_key, engine=settings.resolve_stt_engine()
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("STT pipeline failed for %s", body.recording_id)
        raise HTTPException(status_code=503, detail=f"Transcription failed: {exc}") from exc

    if result["duration_ms"] > STT_SLA_MS:
        logger.warning(
            "STT exceeded SLA (%dms) for recording %s (engine=%s)",
            STT_SLA_MS,
            body.recording_id,
            result["engine_used"],
        )

    transcript_payload = {
        "transcript": result["transcript"],
        "engine_used": result["engine_used"],
        "confidence": result["confidence"],
        "corrections_made": result["corrections_made"],
        "duration_ms": result["duration_ms"],
        "raw_transcript": result["raw_transcript"],
        "metadata": result["metadata"],
    }
    await asyncio.to_thread(
        redis_client.setex,
        redis_transcript_key(doctor.id, body.recording_id),
        TRANSCRIPT_TTL_SECONDS,
        json.dumps(transcript_payload).encode("utf-8"),
    )

    return VoiceTranscribeResponse(
        transcript=result["transcript"],
        engine_used=result["engine_used"],
        confidence=result["confidence"],
        corrections_made=result["corrections_made"],
        duration_ms=result["duration_ms"],
    )
