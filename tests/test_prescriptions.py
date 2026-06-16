"""
Prescription lifecycle tests.
"""
from __future__ import annotations

import asyncio
import base64

import pytest
from httpx import AsyncClient


def _structured_payload():
    return {
        "medications": [
            {
                "drug_name": "METFORMIN",
                "dosage": "500mg",
                "frequency": "BD",
                "duration": "30 days",
                "instruction": "after food",
            }
        ],
        "diagnosis": "Type 2 diabetes",
        "advice": "Diet control",
        "follow_up": "4 weeks",
        "same_as_last_time": False,
    }


@pytest.mark.asyncio
async def test_create_prescription_and_get_by_id(
    client: AsyncClient,
    auth_headers,
    make_patient,
):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)

    create = await client.post(
        "/api/v1/prescription/approve",
        headers=headers,
        json={
            "patient_id": patient.id,
            "raw_transcription": "Metformin BD",
            "structured": _structured_payload(),
        },
    )
    assert create.status_code == 200
    rx_id = create.json()["prescription_id"]

    detail = await client.get(f"/api/v1/prescriptions/{rx_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == rx_id
    assert body["structured"]["medications"][0]["drug_name"] == "METFORMIN"


@pytest.mark.asyncio
async def test_get_prescriptions_by_patient_history(
    client: AsyncClient,
    auth_headers,
    make_patient,
):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)

    for _ in range(2):
        resp = await client.post(
            "/api/v1/prescription/approve",
            headers=headers,
            json={
                "patient_id": patient.id,
                "structured": _structured_payload(),
            },
        )
        assert resp.status_code == 200

    history = await client.get(
        f"/api/v1/patients/{patient.id}/history",
        headers=headers,
    )
    assert history.status_code == 200
    body = history.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert body["items"][0]["drugs"][0]["name"] == "METFORMIN"


@pytest.mark.asyncio
async def test_get_patient_history_last(
    client: AsyncClient,
    auth_headers,
    make_patient,
):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)

    await client.post(
        "/api/v1/prescription/approve",
        headers=headers,
        json={"patient_id": patient.id, "structured": _structured_payload()},
    )

    last = await client.get(
        f"/api/v1/patients/{patient.id}/history/last",
        headers=headers,
    )
    assert last.status_code == 200
    assert last.json()["diagnosis"] == "Type 2 diabetes"
    assert len(last.json()["drugs"]) == 1


@pytest.mark.asyncio
async def test_get_patient_history_detail(
    client: AsyncClient,
    auth_headers,
    make_patient,
):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)

    create = await client.post(
        "/api/v1/prescription/approve",
        headers=headers,
        json={"patient_id": patient.id, "structured": _structured_payload()},
    )
    rx_id = create.json()["prescription_id"]

    detail = await client.get(
        f"/api/v1/patients/{patient.id}/history/{rx_id}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["id"] == rx_id


@pytest.mark.asyncio
async def test_get_patient_history_last_empty(
    client: AsyncClient,
    auth_headers,
    make_patient,
):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)

    last = await client.get(
        f"/api/v1/patients/{patient.id}/history/last",
        headers=headers,
    )
    assert last.status_code == 404


@pytest.mark.asyncio
async def test_pdf_download_returns_pdf_content_type(
    client: AsyncClient,
    auth_headers,
    make_patient,
):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)

    create = await client.post(
        "/api/v1/prescription/approve",
        headers=headers,
        json={"patient_id": patient.id, "structured": _structured_payload()},
    )
    rx_id = create.json()["prescription_id"]

    pdf = await client.get(f"/api/v1/prescriptions/{rx_id}/pdf", headers=headers)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_prescription_list_ordered_by_created_at_desc(
    client: AsyncClient,
    auth_headers,
    make_patient,
    make_prescription,
):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)
    first = await make_prescription(patient=patient)
    await asyncio.sleep(0.05)
    second = await make_prescription(patient=patient)

    history = await client.get(
        f"/api/v1/patients/{patient.id}/history",
        headers=headers,
    )
    assert history.status_code == 200
    ids = [item["id"] for item in history.json()["items"]]
    assert ids.index(second.id) < ids.index(first.id)


@pytest.mark.asyncio
async def test_approve_returns_base64_pdf(client: AsyncClient, auth_headers, make_patient):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)

    response = await client.post(
        "/api/v1/prescription/approve",
        headers=headers,
        json={"patient_id": patient.id, "structured": _structured_payload()},
    )
    assert response.status_code == 200
    body = response.json()
    pdf = base64.b64decode(body["pdf_base64"])
    assert pdf[:4] == b"%PDF"
