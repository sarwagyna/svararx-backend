"""
Consultation lifecycle tests.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


def _structured_payload():
    return {
        "medications": [
            {
                "drug_name": "PARACETAMOL",
                "dosage": "650mg",
                "frequency": "TDS",
                "duration": "3 days",
                "instruction": "",
            }
        ],
        "diagnosis": "Fever",
        "advice": "Rest",
        "follow_up": "3 days",
        "same_as_last_time": False,
    }


@pytest.mark.asyncio
async def test_start_and_get_active_consultation(
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
            "chief_complaint": "Fever, Cold / Cough",
            "tags": ["Fever", "Cold / Cough"],
        },
    )
    assert start.status_code == 200
    body = start.json()
    assert body["chief_complaint"] == "Fever, Cold / Cough"
    assert body["completed_at"] is None

    active = await client.get("/api/v1/consultations/active", headers=headers)
    assert active.status_code == 200
    assert active.json()["id"] == body["id"]


@pytest.mark.asyncio
async def test_double_start_returns_409(
    client: AsyncClient,
    auth_headers,
):
    headers, _, _ = auth_headers

    first = await client.post(
        "/api/v1/consultations/start",
        headers=headers,
        json={"tags": ["Fever"]},
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/v1/consultations/start",
        headers=headers,
        json={"tags": ["Headache"]},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_complete_consultation_links_prescription(
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
            "chief_complaint": "Fever",
            "tags": ["Fever"],
        },
    )
    consultation_id = start.json()["id"]

    approve = await client.post(
        "/api/v1/prescription/approve",
        headers=headers,
        json={
            "patient_id": patient.id,
            "structured": _structured_payload(),
            "consultation_id": consultation_id,
        },
    )
    assert approve.status_code == 200
    rx_id = approve.json()["prescription_id"]

    active = await client.get("/api/v1/consultations/active", headers=headers)
    assert active.status_code == 404

    detail = await client.get(f"/api/v1/prescriptions/{rx_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["structured"].get("chief_complaint") == "Fever"
