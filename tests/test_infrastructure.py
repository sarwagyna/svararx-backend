"""Infrastructure smoke tests for shared fixtures."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.dependencies import get_current_doctor
from app.main import app
from app.models import Doctor


def test_mock_redis_set_get(mock_redis):
    mock_redis.set("celery:ping", "pong")
    value = mock_redis.get("celery:ping")
    assert value == b"pong" or value == "pong"


@pytest.mark.asyncio
async def test_mock_whisper_transcribe_endpoint(unit_client: AsyncClient, mock_whisper_response):
    doctor = Doctor(
        id="00000000-0000-0000-0000-000000000001",
        name="Dr Mock",
        qualifications="MBBS",
        mci_number="MOCK-1",
        speciality="GP",
        pin_hash="",
        is_active=True,
    )

    async def _override_doctor():
        return doctor

    app.dependency_overrides[get_current_doctor] = _override_doctor
    mock_whisper_response("Metformin five hundred BD")

    audio = b"\x00" * 200
    response = await unit_client.post(
        "/api/v1/transcribe",
        files={"audio": ("test.webm", audio, "audio/webm")},
    )
    app.dependency_overrides.pop(get_current_doctor, None)

    assert response.status_code == 200
    assert "Metformin" in response.json()["transcription"]
