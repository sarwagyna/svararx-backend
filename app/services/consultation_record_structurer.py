"""
LLM structuring for full consultation EMR records from voice transcript.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from groq import Groq

from app.config import Settings, get_settings
from app.consultation_record_schemas import ConsultationRecordContent
from app.services.consultation_record_service import apply_llm_payload_to_content

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "consultation_record.txt"


def _load_system_prompt(allergy_list: str = "", conditions_list: str = "") -> str:
    base = _PROMPT_PATH.read_text(encoding="utf-8")
    if allergy_list:
        base += f"\n\nPatient allergies: {allergy_list}"
    if conditions_list:
        base += f"\n\nPatient chronic conditions: {conditions_list}"
    return base


def _extract_json(text: str) -> dict[str, Any]:
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
    text = text.rstrip("`").strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(text[start:end])


def _build_user_prompt(
    transcript: str,
    *,
    chief_complaint: str | None = None,
    vitals_summary: str | None = None,
) -> str:
    lines = [f"Transcript: {transcript}"]
    if chief_complaint:
        lines.append(f"Chief complaint entered: {chief_complaint}")
    if vitals_summary:
        lines.append(f"Vitals recorded: {vitals_summary}")
    lines.append("Return JSON:")
    return "\n".join(lines)


async def structure_consultation_record(
    transcript: str,
    patient_context: dict[str, Any],
    existing: ConsultationRecordContent | None = None,
    settings: Settings | None = None,
) -> tuple[ConsultationRecordContent, str | None]:
    settings = settings or get_settings()
    allergies = patient_context.get("allergy_text") or ""
    conditions = patient_context.get("conditions_text") or ""
    chief = patient_context.get("chief_complaint")
    vitals = patient_context.get("vitals_summary")

    client = Groq(api_key=settings.groq_api_key)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": _load_system_prompt(allergies, conditions)},
            {
                "role": "user",
                "content": _build_user_prompt(
                    transcript,
                    chief_complaint=chief,
                    vitals_summary=vitals,
                ),
            },
        ],
        temperature=0.1,
        max_tokens=2048,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content or ""
    payload = _extract_json(raw)
    base = existing or ConsultationRecordContent()
    return apply_llm_payload_to_content(base, payload)
