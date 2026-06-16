"""Convert uploaded audio to 16 kHz mono WAV via ffmpeg."""
from __future__ import annotations

import logging
import shutil
import wave
from functools import lru_cache
from pathlib import Path

import ffmpeg

logger = logging.getLogger(__name__)

ALLOWED_AUDIO_CONTENT_TYPES = {
    "audio/webm",
    "audio/mp4",
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/ogg",
    "application/octet-stream",
}


def base_content_type(content_type: str | None) -> str:
    """Strip parameters like ';codecs=opus' from a MIME type."""
    return (content_type or "").split(";")[0].strip().lower()


def is_allowed_audio_type(content_type: str | None, allowed: set[str] | None = None) -> bool:
    base = base_content_type(content_type)
    if not base:
        return True  # browsers often omit type on FormData blobs
    return base in (allowed or ALLOWED_AUDIO_CONTENT_TYPES)


def suffix_for_audio_upload(content_type: str | None, filename: str | None) -> str:
    if filename and "." in filename:
        return Path(filename).suffix
    mapping = {
        "audio/webm": ".webm",
        "audio/mp4": ".mp4",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mpeg": ".mp3",
        "audio/ogg": ".ogg",
    }
    return mapping.get(base_content_type(content_type), ".webm")


@lru_cache(maxsize=1)
def resolve_ffmpeg_cmd() -> str:
    """Prefer system ffmpeg; fall back to bundled binary from imageio-ffmpeg."""
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(
            "ffmpeg is not available. Install ffmpeg on PATH or pip install imageio-ffmpeg."
        ) from exc


def convert_to_wav_16k_mono(input_path: str | Path, output_path: str | Path) -> None:
    """Convert any ffmpeg-supported audio file to 16 kHz mono WAV."""
    input_path = str(input_path)
    output_path = str(output_path)
    ffmpeg_cmd = resolve_ffmpeg_cmd()
    try:
        (
            ffmpeg.input(input_path)
            .output(output_path, ar=16000, ac=1, format="wav")
            .overwrite_output()
            .run(cmd=ffmpeg_cmd, capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else str(exc)
        logger.error("ffmpeg conversion failed: %s", stderr)
        raise RuntimeError(f"Audio conversion failed: {stderr}") from exc
    except (FileNotFoundError, OSError) as exc:
        logger.error("ffmpeg executable unavailable: %s", exc)
        raise RuntimeError(
            "Audio conversion unavailable. Install ffmpeg or restart the backend after "
            "pip install imageio-ffmpeg."
        ) from exc


def wav_duration_seconds(wav_path: str | Path) -> float:
    with wave.open(str(wav_path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())
