"""
Dual-engine STT pipeline — Whisper primary, Sarvam Saarika fallback.

Pipeline:
  1. Whisper (fine-tuned local model or large-v3) with medical initial_prompt
  2. Confidence gate on avg_logprob / word count
  3. Sarvam fallback when Whisper is low-confidence or fails
  4. Self-correction phrase resolution
  5. Drug-name correction (app.ml.drug_name_corrector)
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, TypedDict

import httpx
import numpy as np

from app.ml.drug_name_corrector import CorrectionLog, correct_drug_names
from app.services.audio_convert import convert_to_wav_16k_mono

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────

WHISPER_MODEL_DIR = Path(__file__).resolve().parent.parent / "ml" / "whisper_model"
WHISPER_FALLBACK_MODEL = "large-v3"

WHISPER_INITIAL_PROMPT = (
    "Doctor prescription Telugu English. Medicine: Metformin, Amlodipine, Paracetamol, "
    "Amoxicillin, Azithromycin, Pantoprazole, Atorvastatin, Losartan, Omeprazole, Dolo 650, "
    "Cefixime, Cetirizine, Metronidazole, Ciprofloxacin, Ranitidine, Aspirin, Atenolol, "
    "Glibenclamide, Insulin, Prednisolone. Telugu: roju, sarlu, tinadaniki, mundu, tarvata, "
    "tablet, capsule."
)

WHISPER_LOGPROB_THRESHOLD = -0.8
MIN_TRANSCRIPT_WORDS = 5
MAX_AUDIO_DURATION_S = 120

SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_MODEL = "saarika:v2.5"
SARVAM_LANGUAGE = "te-IN"

CORRECTION_PHRASES: list[str] = [
    "no wait",
    "i mean",
    "actually",
    "change that to",
    "scratch that",
    "not that",
    "sorry",
    "correction",
    "i said",
    "replace",
]

_CORRECTION_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in CORRECTION_PHRASES) + r")\b",
    re.IGNORECASE,
)

_whisper_model: Any | None = None
_whisper_model_lock = asyncio.Lock()


# ─── Return types ─────────────────────────────────────────────

class CorrectionEntry(TypedDict):
    original: str
    corrected: str
    score: float


class TranscriptionResult(TypedDict):
    raw: str
    corrected: str
    corrections: list[CorrectionEntry]
    low_confidence: list[str]


class DualEngineResult(TypedDict):
    transcript: str
    raw_transcript: str
    engine_used: str
    confidence: float
    corrections_made: int
    duration_ms: int
    corrections: list[CorrectionEntry]
    metadata: dict[str, Any]


class WhisperResult(TypedDict):
    text: str
    avg_logprob: float
    word_count: int


# ─── Self-correction ──────────────────────────────────────────

def resolve_self_corrections(text: str) -> str:
    matches = list(_CORRECTION_RE.finditer(text))
    if not matches:
        return text
    last = matches[-1]
    after = text[last.end():].strip()
    if not after:
        return text
    before_first = text[: matches[0].start()].strip()
    if before_first:
        return f"{before_first} {after}"
    return after


def invalidate_drug_cache() -> None:
    from app.ml.drug_name_corrector import invalidate_drug_index

    invalidate_drug_index()


# ─── Audio helpers ────────────────────────────────────────────

def _wav_bytes_to_float32(wav_bytes: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        sample_width = wf.getsampwidth()
    if sample_width == 2:
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        audio = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")
    return audio


def _estimate_wav_duration_seconds(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


def _ensure_wav_bytes(audio_bytes: bytes, filename: str) -> bytes:
    if filename.lower().endswith(".wav"):
        return audio_bytes
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / f"input{Path(filename).suffix or '.webm'}"
        wav_path = Path(tmpdir) / "output.wav"
        input_path.write_bytes(audio_bytes)
        convert_to_wav_16k_mono(input_path, wav_path)
        return wav_path.read_bytes()


def _correction_logs_to_entries(logs: list[CorrectionLog]) -> list[CorrectionEntry]:
    return [
        CorrectionEntry(original=c["original"], corrected=c["corrected"], score=c["score"])
        for c in logs
    ]


# ─── Whisper engine ───────────────────────────────────────────

def _load_whisper_model():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model

    import whisper

    if WHISPER_MODEL_DIR.exists() and any(WHISPER_MODEL_DIR.iterdir()):
        logger.info("Loading fine-tuned Whisper from %s", WHISPER_MODEL_DIR)
        _whisper_model = whisper.load_model(str(WHISPER_MODEL_DIR))
    else:
        logger.info("Loading Whisper model %s (no fine-tuned weights in ml/whisper_model/)", WHISPER_FALLBACK_MODEL)
        _whisper_model = whisper.load_model(WHISPER_FALLBACK_MODEL)
    return _whisper_model


def _transcribe_whisper(wav_bytes: bytes) -> WhisperResult:
    model = _load_whisper_model()
    audio = _wav_bytes_to_float32(wav_bytes)

    result = model.transcribe(
        audio,
        language=None,
        initial_prompt=WHISPER_INITIAL_PROMPT,
        word_timestamps=True,
        verbose=False,
    )

    segments = result.get("segments") or []
    if segments:
        avg_logprob = sum(float(s.get("avg_logprob", 0.0)) for s in segments) / len(segments)
    else:
        avg_logprob = -1.0

    text = (result.get("text") or "").strip()
    word_count = len(text.split())

    return WhisperResult(text=text, avg_logprob=avg_logprob, word_count=word_count)


def _should_fallback_whisper(whisper: WhisperResult) -> bool:
    if not whisper["text"]:
        return True
    if whisper["word_count"] < MIN_TRANSCRIPT_WORDS:
        return True
    if whisper["avg_logprob"] < WHISPER_LOGPROB_THRESHOLD:
        return True
    return False


# ─── Sarvam fallback ──────────────────────────────────────────

def _sarvam_error_detail(response: httpx.Response | None) -> str:
    if response is None:
        return ""
    try:
        body = response.json()
        if isinstance(body, dict):
            return str(body.get("detail") or body.get("message") or body)
        return str(body)
    except Exception:
        return (response.text or "")[:500]


def _transcribe_sarvam(wav_bytes: bytes, api_key: str) -> str:
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                SARVAM_STT_URL,
                headers={"api-subscription-key": api_key},
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={
                    "model": SARVAM_MODEL,
                    "language_code": SARVAM_LANGUAGE,
                    "with_timestamps": "false",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        detail = _sarvam_error_detail(exc.response)
        raise RuntimeError(f"Sarvam STT request failed ({status}): {detail}") from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"Sarvam STT request failed: {exc}") from exc

    for key in ("transcript", "text", "transcription"):
        if isinstance(payload.get(key), str) and payload[key].strip():
            return payload[key].strip()

    if isinstance(payload.get("data"), dict):
        nested = payload["data"]
        for key in ("transcript", "text", "transcription"):
            if isinstance(nested.get(key), str) and nested[key].strip():
                return nested[key].strip()

    raise RuntimeError(f"Sarvam STT returned no transcript: {payload!r}")


async def _try_sarvam_fallback(
    wav_bytes: bytes,
    sarvam_api_key: str,
    whisper_result: WhisperResult | None,
) -> tuple[str, dict[str, Any]]:
    """Run Sarvam fallback; reuse Whisper text when Sarvam fails but Whisper had output."""
    metadata: dict[str, Any] = {"tags": ["sarvam"]}
    try:
        sarvam_text = await asyncio.to_thread(_transcribe_sarvam, wav_bytes, sarvam_api_key)
        return sarvam_text, metadata
    except RuntimeError as exc:
        whisper_text = (whisper_result or {}).get("text", "").strip()
        if whisper_text:
            logger.warning(
                "Sarvam fallback failed (%s) — using Whisper transcript",
                exc,
            )
            metadata["sarvam_fallback_failed"] = str(exc)
            metadata["tags"] = ["whisper"]
            return whisper_text, metadata
        raise


# ─── Public pipeline ──────────────────────────────────────────

async def transcribe_wav_dual_engine(
    wav_bytes: bytes,
    sarvam_api_key: str,
    engine: str = "auto",
) -> DualEngineResult:
    """
    Run Whisper → optional Sarvam fallback → self-correction → drug correction.
    Input must be 16 kHz mono WAV bytes.

    engine:
      - "sarvam": skip Whisper entirely (no torch/large-v3 load). Recommended in
        production where the 3GB Whisper model would OOM.
      - "whisper"/"auto": Whisper first, Sarvam fallback.
    """
    if len(wav_bytes) < 100:
        raise ValueError("Audio file is empty or too small.")

    duration = _estimate_wav_duration_seconds(wav_bytes)
    if duration > MAX_AUDIO_DURATION_S:
        raise ValueError(
            f"Audio is {duration:.0f}s — maximum allowed is {MAX_AUDIO_DURATION_S}s."
        )

    started = time.monotonic()
    metadata: dict[str, Any] = {}
    engine_used = "whisper"
    confidence = -1.0
    raw_transcript = ""

    if engine == "sarvam":
        # Direct Sarvam — no Whisper model load. Raises RuntimeError (→ 503) if
        # the Sarvam call fails (e.g. missing/invalid SARVAM_API_KEY).
        if not sarvam_api_key:
            raise RuntimeError(
                "SARVAM_API_KEY is not configured. Set it to enable speech-to-text."
            )
        raw_transcript = await asyncio.to_thread(
            _transcribe_sarvam, wav_bytes, sarvam_api_key
        )
        engine_used = "sarvam"
        metadata["tags"] = ["sarvam"]
    else:
        async with _whisper_model_lock:
            try:
                whisper_result = await asyncio.to_thread(_transcribe_whisper, wav_bytes)
                raw_transcript = whisper_result["text"]
                confidence = whisper_result["avg_logprob"]
                metadata["whisper_avg_logprob"] = confidence
                metadata["whisper_word_count"] = whisper_result["word_count"]

                if _should_fallback_whisper(whisper_result):
                    logger.info(
                        "Whisper below threshold (logprob=%.2f, words=%d) — falling back to Sarvam",
                        confidence,
                        whisper_result["word_count"],
                    )
                    sarvam_text, sarvam_meta = await _try_sarvam_fallback(
                        wav_bytes, sarvam_api_key, whisper_result
                    )
                    raw_transcript = sarvam_text
                    engine_used = "sarvam" if sarvam_meta.get("tags") == ["sarvam"] else "whisper"
                    metadata["fallback_reason"] = "low_confidence_or_short"
                    metadata.update(sarvam_meta)
            except Exception as exc:
                logger.warning("Whisper failed (%s) — falling back to Sarvam", exc)
                sarvam_text, sarvam_meta = await _try_sarvam_fallback(
                    wav_bytes, sarvam_api_key, None
                )
                raw_transcript = sarvam_text
                engine_used = "sarvam" if sarvam_meta.get("tags") == ["sarvam"] else "whisper"
                confidence = -1.0
                metadata["fallback_reason"] = "whisper_error"
                metadata["whisper_error"] = str(exc)
                metadata.update(sarvam_meta)

    after_self_correction = resolve_self_corrections(raw_transcript)
    drug_result = correct_drug_names(after_self_correction)
    corrections = _correction_logs_to_entries(drug_result.corrections)

    elapsed_ms = int((time.monotonic() - started) * 1000)

    return DualEngineResult(
        transcript=drug_result.transcript,
        raw_transcript=raw_transcript,
        engine_used=engine_used,
        confidence=confidence,
        corrections_made=drug_result.corrections_made,
        duration_ms=elapsed_ms,
        corrections=corrections,
        metadata=metadata,
    )


async def transcribe_audio(
    audio_bytes: bytes,
    filename: str,
    api_key: str,
    engine: str = "auto",
) -> TranscriptionResult:
    """
    Backward-compatible wrapper used by /transcribe-and-structure.
    Converts arbitrary audio to WAV, runs dual-engine pipeline.
    """
    wav_bytes = await asyncio.to_thread(_ensure_wav_bytes, audio_bytes, filename)
    result = await transcribe_wav_dual_engine(wav_bytes, api_key, engine=engine)

    return TranscriptionResult(
        raw=result["raw_transcript"],
        corrected=result["transcript"],
        corrections=result["corrections"],
        low_confidence=[],
    )
