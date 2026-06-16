"""
POST /api/v1/structure
Accepts transcription text, sends to Groq LLM, returns structured prescription JSON.
"""
import json
import re
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from groq import AsyncGroq
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, Settings
from app.core.dependencies import get_current_doctor
from app.database import get_db
from app.auth import get_doctor_clinic_id
from app.schemas import StructureResponse, StructuredPrescription, MedicationItem
from app.services.drug_correction import correct_drug_names
from app.services.frequency_utils import normalize_frequency
from app.services.allergy_service import (
    apply_allergy_flags_to_prescription,
    fetch_patient_allergies,
    format_allergy_prompt,
)

router = APIRouter()

SYSTEM_PROMPT = """You are a medical transcription assistant for Indian doctors.
Extract prescription fields from doctor dictation.
Return ONLY valid JSON with no markdown, no explanation, no extra text.

JSON schema:
{
  "medications": [
    {
      "drug_name": "",
      "dosage": "",
      "frequency": "",
      "duration": "",
      "instruction": ""
    }
  ],
  "diagnosis": "",
  "advice": "",
  "follow_up": "",
  "same_as_last_time": false
}

Rules:
- drug_name: UPPERCASE always (e.g. "METFORMIN 500MG")
- dosage: exact as stated; leave blank string "" if not mentioned — NEVER guess
- frequency: ONLY one of: OD, BD, TDS, QID, SOS — map "once daily"→OD, "twice"→BD, "thrice/three times"→TDS, "four times"→QID, "as needed/when needed/PRN/SOS"→SOS
- duration: as stated (e.g. "5 days", "1 month", "30 days")
- instruction: only if explicitly stated (e.g. "after food", "before breakfast"); default to ""
- diagnosis: as stated; "" if not mentioned
- advice: general advice given; "" if none
- follow_up: follow-up instruction; "" if none
- same_as_last_time: true ONLY if doctor says "same as last time" or equivalent — return empty medications array with this flag
- Handle Telugu/Hindi/English code-switching naturally
- Common Telugu drug name patterns: "Metformin tablet BD", "Amlodipine OD"
"""


class StructureRequest(BaseModel):
    transcription: str
    patient_id: str | None = None


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown code blocks."""
    # Strip markdown code fences if present
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    text = text.rstrip("`").strip()

    # Find first { to last }
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in LLM response")

    return json.loads(text[start:end])


@router.post("/structure", response_model=StructureResponse)
async def structure_transcription(
    body: StructureRequest,
    settings: Settings = Depends(get_settings),
    _doctor=Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    """
    Send transcription to Groq LLM and return structured prescription.
    Applies drug name correction layer after structuring.
    """
    if not body.transcription.strip():
        raise HTTPException(status_code=400, detail="Transcription text is empty.")

    allergy_prompt = ""
    allergy_records = []
    if body.patient_id:
        allergy_records = await fetch_patient_allergies(db, body.patient_id)
        allergy_prompt = format_allergy_prompt(allergy_records)

    client = AsyncGroq(api_key=settings.groq_api_key)
    system_prompt = SYSTEM_PROMPT
    if allergy_prompt:
        system_prompt += (
            f"\n\nCRITICAL: Patient has allergies to: {allergy_prompt}. "
            "If any prescribed drug matches an allergy, flag it for doctor review."
        )

    try:
        chat_response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": body.transcription},
            ],
            temperature=0.1,  # Low temperature for deterministic extraction
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Groq API error: {str(exc)}")

    raw_output = chat_response.choices[0].message.content

    try:
        parsed = _extract_json(raw_output)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LLM returned invalid JSON: {str(exc)}. Raw: {raw_output[:200]}",
        )

    # Build structured prescription
    medications = []
    for med in parsed.get("medications", []):
        medications.append(
            MedicationItem(
                drug_name=str(med.get("drug_name", "")).upper(),
                dosage=str(med.get("dosage", "")),
                frequency=normalize_frequency(str(med.get("frequency", ""))),
                duration=str(med.get("duration", "")),
                instruction=str(med.get("instruction", "")),
            )
        )

    structured = StructuredPrescription(
        medications=medications,
        diagnosis=str(parsed.get("diagnosis", "")),
        advice=str(parsed.get("advice", "")),
        follow_up=str(parsed.get("follow_up", "")),
        same_as_last_time=bool(parsed.get("same_as_last_time", False)),
    )

    # Apply drug correction layer
    structured = await correct_drug_names(structured, settings)

    # Cross-check allergies with rapidfuzz
    structured = apply_allergy_flags_to_prescription(structured, allergy_records)

    return StructureResponse(
        structured=structured,
        raw_llm_output=raw_output,
    )
