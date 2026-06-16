"""
Voice pipeline integration tests — STT → structuring → PDF approval.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest
from httpx import AsyncClient

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts.json"
MIN_PDF_BYTES = 3_000  # ReportLab A5 Rx; well-formed PDF with medications

from app.services.frequency_utils import VALID_FREQUENCIES, normalize_frequency


def _load_cases() -> list[dict]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]


def _legacy_structured(case: dict) -> dict:
    """Map rx fixture llm_response to legacy transcribe-and-structure shape."""
    llm = case.get("llm_response", case.get("structured", {}))
    medications = []
    for drug in llm.get("drugs", llm.get("medications", [])):
        medications.append(
            {
                "drug_name": drug.get("name", drug.get("drug_name", "")).upper(),
                "dosage": drug.get("dose", drug.get("dosage", "")),
                "frequency": drug.get("frequency", ""),
                "duration": drug.get("duration", ""),
                "instruction": drug.get("instructions", drug.get("instruction", "")),
            }
        )
    return {
        "medications": medications,
        "diagnosis": llm.get("diagnosis", ""),
        "advice": llm.get("notes", llm.get("advice", "")),
        "follow_up": str(llm.get("follow_up_days", llm.get("follow_up", ""))),
        "incomplete_fields": [],
        "same_as_last_time": False,
    }


@pytest.mark.parametrize("case", _load_cases(), ids=[c["id"] for c in _load_cases()])
@pytest.mark.asyncio
async def test_voice_pipeline_end_to_end(
    client: AsyncClient,
    auth_headers,
    make_patient,
    seed_common_drugs,
    mock_sarvam_response,
    mock_groq_response,
    case: dict,
):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)
    mock_sarvam_response(case["transcript"])
    mock_groq_response(_legacy_structured(case))

    started = time.time()
    audio = b"\x00" * 256
    files = {"audio": ("recording.webm", audio, "audio/webm")}
    pipeline = await client.post(
        "/api/v1/transcribe-and-structure",
        headers=headers,
        files=files,
    )
    assert pipeline.status_code == 200, pipeline.text
    body = pipeline.json()
    assert body["groq_error"] is False

    structured = body["structured"]
    assert len(structured["medications"]) >= 1
    for med in structured["medications"]:
        assert med["drug_name"].strip()
        assert normalize_frequency(med["frequency"]) in VALID_FREQUENCIES
        assert med["duration"].strip()

    approve_payload = {
        "patient_id": patient.id,
        "raw_transcription": case["transcript"],
        "structured": structured,
    }
    approve = await client.post(
        "/api/v1/prescription/approve",
        headers=headers,
        json=approve_payload,
    )
    assert approve.status_code == 200, approve.text
    approve_body = approve.json()
    assert approve_body["status"] == "approved"

    pdf_bytes = base64.b64decode(approve_body["pdf_base64"])
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > MIN_PDF_BYTES

    elapsed = time.time() - started
    assert elapsed < 35, f"Pipeline exceeded 35s SLA: {elapsed:.2f}s"

    pdf_resp = await client.get(
        f"/api/v1/prescriptions/{approve_body['prescription_id']}/pdf",
        headers=headers,
    )
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"] == "application/pdf"
    assert len(pdf_resp.content) > MIN_PDF_BYTES
