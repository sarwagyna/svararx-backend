"""
POST /api/v1/transcribe
Accepts audio blob (webm/opus), sends to OpenAI Whisper, returns transcription.
Applies self-correction resolution before returning.
"""
import re
import tempfile
import os
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from openai import AsyncOpenAI

from app.config import get_settings, Settings
from app.services.audio_convert import is_allowed_audio_type
from app.core.dependencies import get_current_doctor
from app.schemas import TranscribeResponse

router = APIRouter()

# ─── Self-correction patterns ─────────────────────────────────
# Phrases that signal the doctor is correcting themselves.
# We resolve by keeping only the text AFTER the correction phrase.
CORRECTION_PATTERNS = [
    r"\bno wait\b",
    r"\bi mean\b",
    r"\bactually\b",
    r"\bchange that to\b",
    r"\bscratch that\b",
    r"\bcorrection\b",
    r"\bsorry\b,?\s*i mean\b",
    r"\blet me correct\b",
]

_CORRECTION_RE = re.compile(
    "|".join(CORRECTION_PATTERNS),
    re.IGNORECASE,
)


def resolve_self_corrections(text: str) -> tuple[str, bool]:
    """
    Find the last self-correction phrase and return only the text after it.
    Returns (resolved_text, was_corrected).
    """
    matches = list(_CORRECTION_RE.finditer(text))
    if not matches:
        return text, False

    last_match = matches[-1]
    corrected = text[last_match.end():].strip()
    # If nothing follows the correction phrase, return original
    if not corrected:
        return text, False
    return corrected, True


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    audio: UploadFile = File(..., description="Audio file in webm/opus format"),
    settings: Settings = Depends(get_settings),
    _doctor=Depends(get_current_doctor),
):
    """
    Transcribe doctor dictation audio using OpenAI Whisper.
    Supports Telugu, Hindi, English, and code-switching.
    Applies self-correction resolution before returning.
    """
    # Validate content type loosely — accept webm, ogg, mp4, wav
    allowed_types = {
        "audio/webm", "audio/ogg", "audio/mp4", "audio/wav",
        "audio/mpeg", "application/octet-stream",
    }
    if audio.content_type and not is_allowed_audio_type(audio.content_type, allowed_types):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported audio format: {audio.content_type}. Use webm/opus.",
        )

    audio_bytes = await audio.read()
    if len(audio_bytes) < 100:
        raise HTTPException(status_code=400, detail="Audio file is too small or empty.")

    # Write to temp file — Whisper SDK requires a file-like object with a name
    suffix = ".webm"
    if audio.filename:
        _, ext = os.path.splitext(audio.filename)
        if ext:
            suffix = ext

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)

        with open(tmp_path, "rb") as f:
            response = await client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                # Hint Whisper about expected languages for better accuracy
                language=None,  # Auto-detect; handles code-switching better
                response_format="verbose_json",
                prompt=(
                    "Doctor dictating a prescription in Telugu, Hindi, or English. "
                    "Drug names, dosages, and medical terms may appear. "
                    "Common drugs: Metformin, Amlodipine, Atorvastatin, Pantoprazole, "
                    "Paracetamol, Amoxicillin, Azithromycin, Cetirizine, Omeprazole."
                ),
            )

        raw_text: str = response.text.strip()

        # Apply self-correction resolution
        resolved_text, was_corrected = resolve_self_corrections(raw_text)

        return TranscribeResponse(
            transcription=resolved_text,
            confidence=None,  # Whisper verbose_json doesn't expose a single confidence
            corrected=was_corrected,
            original=raw_text if was_corrected else None,
        )

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Whisper API error: {str(exc)}",
        )
    finally:
        os.unlink(tmp_path)
