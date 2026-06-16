"""
Tests for LLM-based prescription structuring (rx_structurer + /rx/structure).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.tenant import redis_transcript_key
from app.models.rx import StructuredRx
from app.services.rx_structurer import VALID_FREQUENCIES, structure_prescription

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts.json"
SLA_SECONDS = 4.0


def _load_cases() -> list[dict]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]


def _groq_client_returning(payload: dict) -> MagicMock:
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


@pytest.mark.parametrize("case", _load_cases(), ids=[c["id"] for c in _load_cases()])
@pytest.mark.asyncio
async def test_structure_prescription_fixture_cases(case: dict):
    with (
        patch("app.services.rx_structurer.Groq", return_value=_groq_client_returning(case["llm_response"])),
        patch("app.services.rx_structurer.recognize_drug", new=AsyncMock(return_value=True)),
    ):
        started = time.perf_counter()
        result = await structure_prescription(
            case["transcript"],
            {"allergies": [], "conditions": [], "last_rx": [], "visits_summary": "No prior visits."},
        )
        elapsed = time.perf_counter() - started

    assert isinstance(result, StructuredRx)
    assert elapsed < SLA_SECONDS, f"Structuring exceeded {SLA_SECONDS}s: {elapsed:.2f}s"

    result_names = {d.name.upper() for d in result.drugs}
    for expected in case["expected_drugs"]:
        assert any(expected.upper() in name for name in result_names), (
            f"Expected drug '{expected}' not found in {result_names}"
        )

    for drug in result.drugs:
        if drug.frequency:
            base_freq = drug.frequency.split()[0].upper()
            assert base_freq in VALID_FREQUENCIES or drug.frequency.upper() in VALID_FREQUENCIES


@pytest.mark.asyncio
async def test_structure_prescription_json_retry():
    mock_client = MagicMock()
    bad = MagicMock(message=MagicMock(content="not json at all"))
    good_payload = {
        "drugs": [{"name": "METFORMIN", "dose": "500mg", "frequency": "BD", "duration": "30 days"}],
        "structuring_confidence": 0.9,
    }
    good = MagicMock(message=MagicMock(content=json.dumps(good_payload)))
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[bad]),
        MagicMock(choices=[good]),
    ]

    with (
        patch("app.services.rx_structurer.Groq", return_value=mock_client),
        patch("app.services.rx_structurer.recognize_drug", new=AsyncMock(return_value=True)),
    ):
        result = await structure_prescription("Metformin twice daily", {})

    assert len(result.drugs) == 1
    assert result.drugs[0].name == "METFORMIN"


@pytest.mark.asyncio
async def test_structure_prescription_parse_failure_flags_all():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="invalid"))]
    )

    with (
        patch("app.services.rx_structurer.Groq", return_value=mock_client),
        patch("app.services.rx_structurer.recognize_drug", new=AsyncMock(return_value=False)),
    ):
        result = await structure_prescription("Some unknown drug twice daily", {})

    assert len(result.drugs) >= 1
    assert all(d.flagged for d in result.drugs)
    assert result.structuring_confidence == 0.0


@pytest.mark.asyncio
async def test_rx_structure_endpoint(
    client,
    auth_headers,
    make_patient,
    seed_common_drugs,
    mock_redis,
):
    case = _load_cases()[0]
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic)

    recording_id = "rec-test-001"
    mock_redis.setex(
        redis_transcript_key(doctor.id, recording_id),
        600,
        json.dumps({"transcript": case["transcript"]}),
    )

    with patch(
        "app.services.rx_structurer.Groq",
        return_value=_groq_client_returning(case["llm_response"]),
    ):
        started = time.perf_counter()
        resp = await client.post(
            "/api/v1/rx/structure",
            headers=headers,
            json={"recording_id": recording_id, "patient_id": patient.id},
        )
        elapsed = time.perf_counter() - started

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prescription_id"]
    assert len(body["structured"]["drugs"]) >= 1
    assert elapsed < SLA_SECONDS
