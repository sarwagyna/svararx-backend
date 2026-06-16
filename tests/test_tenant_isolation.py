"""
Multi-tenant isolation — doctors cannot access another clinic's data.
"""
from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from app.core.tenant import redis_transcript_key


@pytest.mark.asyncio
async def test_patient_from_other_clinic_returns_403(
    client: AsyncClient,
    make_doctor,
    make_patient,
    valid_token,
):
    doctor_a, clinic_a = await make_doctor()
    doctor_b, clinic_b = await make_doctor()
    patient_b = await make_patient(doctor=doctor_b, clinic=clinic_b, name="Other Clinic Patient")

    token_a = valid_token(doctor_a.id, clinic_a.id)
    response = await client.get(
        f"/api/v1/patients/{patient_b.id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_token_with_wrong_clinic_id_returns_403(
    client: AsyncClient,
    make_doctor,
    valid_token,
):
    doctor, clinic_a = await make_doctor()
    _, clinic_b = await make_doctor()

    token = valid_token(doctor.id, clinic_b.id)
    response = await client.get(
        "/api/v1/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "clinic" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_doctor_cannot_read_other_doctors_transcript(
    client: AsyncClient,
    make_doctor,
    valid_token,
    mock_redis,
):
    doctor_a, clinic_a = await make_doctor()
    doctor_b, clinic_b = await make_doctor()
    recording_id = "rec-tenant-isolation-001"

    mock_redis.setex(
        redis_transcript_key(doctor_a.id, recording_id),
        600,
        json.dumps({"transcript": "secret transcript"}),
    )

    token_b = valid_token(doctor_b.id, clinic_b.id)
    response = await client.post(
        "/api/v1/rx/structure",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"recording_id": recording_id},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_sees_all_clinic_patients(
    client: AsyncClient,
    make_doctor,
    make_patient,
    valid_token,
):
    admin, clinic = await make_doctor(role="admin")
    doctor_b, _ = await make_doctor(clinic=clinic, role="doctor")
    patient_b = await make_patient(doctor=doctor_b, clinic=clinic, name="Doctor B Patient")

    token = valid_token(admin.id, clinic.id)
    response = await client.get(
        f"/api/v1/patients/{patient_b.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Doctor B Patient"


@pytest.mark.asyncio
async def test_non_admin_cannot_see_other_doctors_patients(
    client: AsyncClient,
    make_doctor,
    make_patient,
    valid_token,
):
    _, clinic = await make_doctor(role="admin")
    doctor_a, _ = await make_doctor(clinic=clinic, role="doctor")
    doctor_b, _ = await make_doctor(clinic=clinic, role="doctor")
    patient_b = await make_patient(doctor=doctor_b, clinic=clinic, name="Doctor B Only")

    token_a = valid_token(doctor_a.id, clinic.id)
    response = await client.get(
        f"/api/v1/patients/{patient_b.id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert response.status_code == 403
