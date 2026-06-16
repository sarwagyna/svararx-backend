"""Dual-engine STT pipeline and drug correction tests."""
from __future__ import annotations

import io
import json
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ml.drug_name_corrector import correct_drug_names
from app.services.stt_service import (
    _should_fallback_whisper,
    transcribe_wav_dual_engine,
)

FIXTURES = Path(__file__).parent / "fixtures" / "stt_samples" / "samples.json"


def _load_samples() -> list[dict]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["samples"]


def _make_wav_bytes(duration_seconds: float = 2.0, sample_rate: int = 16000) -> bytes:
    n_frames = int(duration_seconds * sample_rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x01" * n_frames)
    return buffer.getvalue()


@pytest.mark.parametrize("sample", _load_samples(), ids=[s["id"] for s in _load_samples()])
def test_drug_corrector_expected_drugs(sample: dict):
    result = correct_drug_names(sample["raw_transcript"])
    transcript_lower = result.transcript.lower()

    for drug in sample["expected_drugs"]:
        assert drug.lower() in transcript_lower, (
            f"Expected drug {drug!r} in corrected transcript: {result.transcript!r}"
        )

    for keyword in sample["expected_keywords"]:
        assert keyword.lower() in transcript_lower, (
            f"Expected keyword {keyword!r} in corrected transcript: {result.transcript!r}"
        )


def test_whisper_fallback_triggers_on_low_logprob():
    assert _should_fallback_whisper({"text": "a b c d e", "avg_logprob": -0.9, "word_count": 5})


def test_whisper_fallback_triggers_on_short_transcript():
    assert _should_fallback_whisper({"text": "hello world", "avg_logprob": -0.2, "word_count": 2})


def test_whisper_keeps_good_transcript():
    assert not _should_fallback_whisper(
        {
            "text": "Metformin 500mg BD for 30 days after food",
            "avg_logprob": -0.3,
            "word_count": 7,
        }
    )


@pytest.mark.asyncio
async def test_dual_engine_uses_whisper_when_confident(monkeypatch):
    wav = _make_wav_bytes()
    monkeypatch.setattr(
        "app.services.stt_service._transcribe_whisper",
        lambda _wav: {
            "text": "metro foreman 500mg BD for 30 days",
            "avg_logprob": -0.2,
            "word_count": 6,
        },
    )
    monkeypatch.setattr(
        "app.services.stt_service._transcribe_sarvam",
        lambda *_args: (_ for _ in ()).throw(AssertionError("no fallback")),
    )

    result = await transcribe_wav_dual_engine(wav, "test-key")
    assert result["engine_used"] == "whisper"
    assert "Metformin" in result["transcript"]
    assert result["corrections_made"] >= 1


@pytest.mark.asyncio
async def test_dual_engine_falls_back_to_sarvam(monkeypatch):
    wav = _make_wav_bytes()
    monkeypatch.setattr(
        "app.services.stt_service._transcribe_whisper",
        lambda _wav: {"text": "hi", "avg_logprob": -0.95, "word_count": 1},
    )
    monkeypatch.setattr(
        "app.services.stt_service._transcribe_sarvam",
        lambda *_args: "Amoxicillin 500mg BD for 5 days after food",
    )

    result = await transcribe_wav_dual_engine(wav, "test-key")
    assert result["engine_used"] == "sarvam"
    assert "sarvam" in result["metadata"].get("tags", [])
    assert "Amoxicillin" in result["transcript"]


@pytest.mark.asyncio
async def test_voice_transcribe_endpoint(monkeypatch):
    import fakeredis
    from httpx import ASGITransport, AsyncClient

    from app.core.dependencies import get_current_doctor
    from app.core.tenant import redis_transcript_key, redis_voice_key
    from app.database import get_db
    from app.main import app
    from app.models import Doctor

    fake = fakeredis.FakeRedis(decode_responses=False)
    recording_id = "rec-12345"
    wav = _make_wav_bytes(5.0)
    fake.setex(redis_voice_key("doc-1", recording_id), 600, wav)

    monkeypatch.setattr("app.api.voice.get_redis", lambda: fake)
    monkeypatch.setattr(
        "app.api.voice.transcribe_wav_dual_engine",
        AsyncMock(
            return_value={
                "transcript": "Metformin 500mg BD",
                "raw_transcript": "metro foreman 500mg BD",
                "engine_used": "whisper",
                "confidence": -0.25,
                "corrections_made": 1,
                "duration_ms": 1200,
                "corrections": [],
                "metadata": {},
            }
        ),
    )

    async def override_doctor():
        return Doctor(
            id="doc-1",
            name="Dr STT",
            qualifications="MBBS",
            mci_number="STT-1",
            speciality="GP",
            pin_hash="",
            is_active=True,
        )

    async def override_get_db():
        session = MagicMock()
        yield session

    app.dependency_overrides[get_current_doctor] = override_doctor
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/voice/transcribe",
            json={"recording_id": recording_id},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transcript"] == "Metformin 500mg BD"
    assert body["engine_used"] == "whisper"
    assert body["corrections_made"] == 1
    assert fake.get(redis_transcript_key("doc-1", recording_id)) is not None


def test_transcribe_sarvam_wraps_http_errors(monkeypatch):
    import httpx
    from app.services import stt_service

    class FakeResponse:
        status_code = 400

        def json(self):
            return {"detail": "Model deprecated"}

        @property
        def text(self):
            return '{"detail":"Model deprecated"}'

    request = httpx.Request("POST", stt_service.SARVAM_STT_URL)
    response = httpx.Response(400, request=request)
    response._content = b'{"detail":"Model deprecated"}'

    def fake_post(*_args, **_kwargs):
        raise httpx.HTTPStatusError("bad request", request=request, response=response)

    monkeypatch.setattr("httpx.Client.post", fake_post)

    with pytest.raises(RuntimeError, match="Sarvam STT request failed \\(400\\)"):
        stt_service._transcribe_sarvam(b"wav", "test-key")


@pytest.mark.asyncio
async def test_dual_engine_uses_whisper_when_sarvam_fails(monkeypatch):
    wav = _make_wav_bytes()
    monkeypatch.setattr(
        "app.services.stt_service._transcribe_whisper",
        lambda _wav: {"text": "hi", "avg_logprob": -0.95, "word_count": 1},
    )

    def fail_sarvam(*_args):
        raise RuntimeError("Sarvam STT request failed (400): Model deprecated")

    monkeypatch.setattr("app.services.stt_service._transcribe_sarvam", fail_sarvam)

    result = await transcribe_wav_dual_engine(wav, "test-key")
    assert result["engine_used"] == "whisper"
    assert result["raw_transcript"] == "hi"
    assert "sarvam_fallback_failed" in result["metadata"]
