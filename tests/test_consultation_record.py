"""
Consultation EMR record API tests.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_consultation_record_assembles_from_visit(
    client: AsyncClient,
    auth_headers,
    make_patient,
):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)

    start = await client.post(
        "/api/v1/consultations/start",
        headers=headers,
        json={
            "patient_id": patient.id,
            "chief_complaint": "Fever for 3 days, dry cough",
            "tags": ["Fever"],
            "visit_type": "new",
        },
    )
    consultation_id = start.json()["id"]

    await client.post(
        "/api/v1/vitals",
        headers=headers,
        json={
            "patient_id": patient.id,
            "consultation_id": consultation_id,
            "bp_systolic": 120,
            "bp_diastolic": 80,
            "temperature_f": 99.2,
            "pulse_bpm": 88,
            "height_cm": 170,
            "weight_kg": 70,
        },
    )

    record = await client.get(
        f"/api/v1/consultations/{consultation_id}/record",
        headers=headers,
    )
    assert record.status_code == 200
    body = record.json()
    assert body["patient"]["full_name"] == patient.name
    assert body["visit"]["visit_type"] == "new"
    assert body["vitals"]["bp_systolic"] == 120
    assert body["vitals"]["bmi"] is not None
    assert "Fever" in body["content"]["chief_complaints"][0]


@pytest.mark.asyncio
async def test_update_consultation_record(
    client: AsyncClient,
    auth_headers,
):
    headers, _, _ = auth_headers
    start = await client.post(
        "/api/v1/consultations/start",
        headers=headers,
        json={"tags": ["Headache"]},
    )
    consultation_id = start.json()["id"]

    updated = await client.put(
        f"/api/v1/consultations/{consultation_id}/record",
        headers=headers,
        json={
            "ai_summary": "Patient with headache. Symptomatic care advised.",
            "content": {
                "chief_complaints": ["Headache for 2 days"],
                "examination_findings": ["No neck stiffness"],
                "investigations_ordered": ["CBC"],
            },
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["ai_summary"] == "Patient with headache. Symptomatic care advised."
    assert body["content"]["investigations_ordered"] == ["CBC"]
