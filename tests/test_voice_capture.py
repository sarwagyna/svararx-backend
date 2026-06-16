"""Tests for POST /api/v1/voice/capture — ephemeral Redis storage."""
from __future__ import annotations

import io
import struct
import wave
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import get_current_doctor
from app.core.tenant import redis_voice_key
from app.database import get_db
from app.main import app
from app.models import Doctor


def _make_wav_bytes(duration_seconds: float = 5.0, sample_rate: int = 16000) -> bytes:
    n_frames = int(duration_seconds * sample_rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return buffer.getvalue()


@pytest.fixture
def mock_redis_binary(monkeypatch):
    import fakeredis

    fake = fakeredis.FakeRedis(decode_responses=False)
    monkeypatch.setattr("app.api.voice.get_redis", lambda: fake)
    return fake


@pytest.fixture
async def voice_client(mock_redis_binary):
    from unittest.mock import AsyncMock

    doctor = Doctor(
        id="00000000-0000-0000-0000-000000000099",
        name="Dr Voice",
        qualifications="MBBS",
        mci_number="VOICE-001",
        speciality="GP",
        pin_hash="",
        is_active=True,
    )

    async def override_doctor():
        return doctor

    async def override_get_db():
        session = AsyncMock()
        yield session

    app.dependency_overrides[get_current_doctor] = override_doctor
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, mock_redis_binary

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_voice_capture_stores_wav_in_redis(voice_client, monkeypatch):
    client, redis_client = voice_client
    wav_bytes = _make_wav_bytes(duration_seconds=5.0)

    def fake_convert(input_path, output_path):
        from pathlib import Path

        Path(output_path).write_bytes(wav_bytes)

    monkeypatch.setattr("app.api.voice.convert_to_wav_16k_mono", fake_convert)

    response = await client.post(
        "/api/v1/voice/capture",
        files={"audio": ("sample.wav", wav_bytes, "audio/wav")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["duration_seconds"] == pytest.approx(5.0, abs=0.1)
    assert body["recording_id"]

    doctor_id = "00000000-0000-0000-0000-000000000099"
    stored = redis_client.get(redis_voice_key(doctor_id, body["recording_id"]))
    assert stored is not None
    assert stored[:4] == b"RIFF"
    assert redis_client.ttl(redis_voice_key(doctor_id, body["recording_id"])) > 0


@pytest.mark.asyncio
async def test_voice_capture_rejects_empty_audio(voice_client, monkeypatch):
    client, _redis = voice_client
    monkeypatch.setattr(
        "app.api.voice.convert_to_wav_16k_mono",
        MagicMock(side_effect=AssertionError("should not convert")),
    )

@pytest.mark.asyncio
async def test_voice_capture_accepts_webm_opus_codec(voice_client, monkeypatch):
    client, redis_client = voice_client
    wav_bytes = _make_wav_bytes(duration_seconds=2.0)

    def fake_convert(input_path, output_path):
        from pathlib import Path

        Path(output_path).write_bytes(wav_bytes)

    monkeypatch.setattr("app.api.voice.convert_to_wav_16k_mono", fake_convert)

    response = await client.post(
        "/api/v1/voice/capture",
        files={"audio": ("recording.webm", b"\x00" * 200, "audio/webm;codecs=opus")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recording_id"]
    assert redis_client.get(redis_voice_key("00000000-0000-0000-0000-000000000099", body["recording_id"])) is not None


@pytest.mark.asyncio
async def test_voice_capture_rejects_empty_audio(voice_client, monkeypatch):
    client, _redis = voice_client
    monkeypatch.setattr(
        "app.api.voice.convert_to_wav_16k_mono",
        MagicMock(side_effect=AssertionError("should not convert")),
    )

    response = await client.post(
        "/api/v1/voice/capture",
        files={"audio": ("empty.wav", b"tiny", "audio/wav")},
    )
    assert response.status_code == 400
