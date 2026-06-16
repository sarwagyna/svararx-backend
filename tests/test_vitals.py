"""
Vitals capture API tests.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_record_vitals_with_flags(client: AsyncClient, auth_headers, make_patient):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)

    response = await client.post(
        "/api/v1/vitals",
        headers=headers,
        json={
            "patient_id": patient.id,
            "bp_systolic": 190,
            "bp_diastolic": 110,
            "blood_sugar_mg_dl": 140,
            "blood_sugar_type": "fasting",
            "spo2_percent": 92,
        },
    )
    assert response.status_code == 201
    body = response.json()
    flags = {f["flag"] for f in body["flags"]}
    assert "high_bp" in flags
    assert "high_sugar" in flags
    assert "low_spo2" in flags
    assert body["vitals"]["bp_systolic"] == 190


@pytest.mark.asyncio
async def test_record_vitals_requires_reading(client: AsyncClient, auth_headers, make_patient):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)

    response = await client.post(
        "/api/v1/vitals",
        headers=headers,
        json={"patient_id": patient.id},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_and_latest_vitals(client: AsyncClient, auth_headers, make_patient):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)

    await client.post(
        "/api/v1/vitals",
        headers=headers,
        json={"patient_id": patient.id, "weight_kg": 70.5, "pulse_bpm": 72},
    )
    await client.post(
        "/api/v1/vitals",
        headers=headers,
        json={"patient_id": patient.id, "weight_kg": 71.0, "pulse_bpm": 80},
    )

    latest = await client.get(f"/api/v1/patients/{patient.id}/vitals/latest", headers=headers)
    assert latest.status_code == 200
    assert latest.json()["weight_kg"] == 71.0

    history = await client.get(
        f"/api/v1/patients/{patient.id}/vitals",
        headers=headers,
        params={"limit": 10},
    )
    assert history.status_code == 200
    assert len(history.json()) == 2


@pytest.mark.asyncio
async def test_low_bp_flag(client: AsyncClient, auth_headers, make_patient):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)

    response = await client.post(
        "/api/v1/vitals",
        headers=headers,
        json={"patient_id": patient.id, "bp_systolic": 85, "bp_diastolic": 50},
    )
    assert response.status_code == 201
    flags = {f["flag"] for f in response.json()["flags"]}
    assert "low_bp" in flags
