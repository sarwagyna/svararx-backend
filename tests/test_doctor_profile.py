"""Doctor profile settings API tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import get_current_doctor
from app.database import get_db
from app.main import app
from app.models import Doctor


@pytest.fixture
async def doctor_client(monkeypatch):
    doctor = Doctor(
        id="00000000-0000-0000-0000-000000000088",
        name="Dr Profile",
        qualifications="MBBS",
        mci_number="MCI-12345",
        speciality="General Physician",
        pin_hash="",
        is_active=True,
        onboarding_completed=True,
        onboarding_step=4,
        languages=["Telugu", "English"],
        subscription_tier="free",
    )

    async def override_doctor():
        return doctor

    async def override_get_db():
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        async def _refresh(obj):
            pass

        session.refresh.side_effect = _refresh
        yield session

    app.dependency_overrides[get_current_doctor] = override_doctor
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, doctor

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_doctor_me(doctor_client):
    client, _doctor = doctor_client
    response = await client.get("/api/v1/doctors/me")
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Dr Profile"
    assert body["subscription_tier"] == "free"
    assert body["onboarding_completed"] is True


@pytest.mark.asyncio
async def test_update_doctor_me_partial(doctor_client):
    client, doctor = doctor_client
    response = await client.put(
        "/api/v1/doctors/me",
        json={"qualifications": "MBBS, MD (Medicine)"},
    )
    assert response.status_code == 200
    assert doctor.qualifications == "MBBS, MD (Medicine)"


@pytest.mark.asyncio
async def test_update_rejects_invalid_phone(doctor_client):
    client, _doctor = doctor_client
    response = await client.put(
        "/api/v1/doctors/me",
        json={"clinic_phone": "12345"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_preview_letterhead_pdf(doctor_client):
    client, _doctor = doctor_client
    response = await client.post("/api/v1/doctors/me/letterhead/preview")
    assert response.status_code == 200
    body = response.json()
    assert body["pdf_base64"]
    assert len(body["pdf_base64"]) > 100
    assert body["filename"] == "letterhead-preview.pdf"


@pytest.mark.asyncio
async def test_get_referral_stats(monkeypatch):
    doctor = Doctor(
        id="00000000-0000-0000-0000-000000000099",
        name="Dr Referrer",
        qualifications="MBBS",
        mci_number="MCI-99999",
        speciality="General Physician",
        pin_hash="",
        is_active=True,
        onboarding_completed=True,
        onboarding_step=4,
        languages=["Telugu", "English"],
        subscription_tier="solo",
    )

    session = AsyncMock()
    session.scalar = AsyncMock(side_effect=[4, 2])

    async def override_doctor():
        return doctor

    async def override_get_db():
        yield session

    app.dependency_overrides[get_current_doctor] = override_doctor
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/doctors/me/referrals")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["total_referrals"] == 4
    assert body["paid_referrals"] == 2
    assert body["pending_referrals"] == 2
    assert body["earnings_inr"] == 1000
    assert body["reward_per_referral_inr"] == 500
