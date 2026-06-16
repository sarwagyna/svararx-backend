"""
LLM-based prescription structuring via Groq Llama 3.3 70B.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from groq import Groq
from pydantic import ValidationError
from rapidfuzz import fuzz, process

from app.config import Settings
from app.models.rx import DrugItem, StructuredRx
from app.services.allergy_service import (
    AllergyRecord,
    check_drug_against_allergies,
    format_allergy_prompt,
)
from app.services.drug_recognizer import recognize_drug

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "rx_structure.txt"
VALID_FREQUENCIES = {"OD", "BD", "TDS", "QID", "SOS", "HS", "WEEKLY"}


def _load_system_prompt(allergy_list: str = "", conditions_list: str = "") -> str:
    base = _PROMPT_PATH.read_text(encoding="utf-8")
    if allergy_list:
        base += (
            f"\n\nCRITICAL: Patient has allergies to: {allergy_list}. "
            "If any prescribed drug matches an allergy, set flagged=true on that drug "
            "and add 'ALLERGY WARNING: {drug} - {reaction}' to notes."
        )
    if conditions_list:
        base += (
            f"\n\nPatient chronic conditions: {conditions_list}. "
            "Avoid prescribing contraindicated drugs (e.g. NSAIDs in CKD, "
            "metformin in severe renal impairment, beta-blockers cautiously in asthma)."
        )
    return base


def _extract_json(text: str) -> dict[str, Any]:
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
    text = text.rstrip("`").strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(text[start:end])


def _parse_allergy_records(patient_context: dict) -> list[AllergyRecord]:
    raw = patient_context.get("allergies") or []
    records: list[AllergyRecord] = []
    for item in raw:
        if isinstance(item, str):
            records.append(
                AllergyRecord(id="", drug_name=item, drug_generic=None, reaction=None, severity="unknown")
            )
        elif isinstance(item, dict):
            records.append(
                AllergyRecord(
                    id=str(item.get("id", "")),
                    drug_name=str(item.get("drug_name", "")),
                    drug_generic=item.get("drug_generic"),
                    reaction=item.get("reaction"),
                    severity=str(item.get("severity", "unknown")),
                )
            )
    return [r for r in records if r.drug_name.strip()]


def _build_user_prompt(transcript: str, patient_context: dict) -> str:
    allergies = patient_context.get("allergies") or []
    conditions = patient_context.get("conditions") or []
    last_rx = patient_context.get("last_rx") or []
    visits_summary = patient_context.get("visits_summary") or "No prior visits."
    latest_vitals = patient_context.get("latest_vitals") or "Not recorded."
    chief_complaint = patient_context.get("chief_complaint")
    complaint_line = (
        f"Chief complaint entered: {chief_complaint}\n"
        if chief_complaint
        else ""
    )
    return (
        f"Transcript: {transcript}\n"
        f"{complaint_line}"
        f"Patient allergies: {allergies}\n"
        f"Chronic conditions: {conditions}\n"
        f"Last visit drugs: {last_rx}\n"
        f"Patient last 3 visits: {visits_summary}\n"
        f"Latest vitals: {latest_vitals}\n"
        "Return JSON:"
    )


def _parse_llm_payload(raw: dict[str, Any], transcript: str) -> StructuredRx:
    drugs: list[DrugItem] = []
    for item in raw.get("drugs", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        frequency = str(item.get("frequency", "")).strip().upper()
        if frequency == "WEEKLY" or frequency.startswith("WEEKLY"):
            frequency = "WEEKLY"
        else:
            for code in VALID_FREQUENCIES:
                if frequency.startswith(code):
                    frequency = code
                    break
        drugs.append(
            DrugItem(
                name=name,
                generic_name=item.get("generic_name"),
                dose=str(item.get("dose", "")).strip(),
                frequency=frequency,
                duration=str(item.get("duration", "")).strip(),
                route=str(item.get("route", "oral")).strip() or "oral",
                instructions=item.get("instructions"),
                flagged=bool(item.get("flagged", False)),
            )
        )

    confidence = raw.get("structuring_confidence", 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    follow_up = raw.get("follow_up_days")
    if follow_up is not None:
        try:
            follow_up = int(follow_up)
        except (TypeError, ValueError):
            follow_up = None

    return StructuredRx(
        drugs=drugs,
        chief_complaint=raw.get("chief_complaint"),
        diagnosis=raw.get("diagnosis"),
        follow_up_days=follow_up,
        notes=raw.get("notes"),
        raw_transcript=transcript,
        structuring_confidence=confidence,
    )


def _fallback_result(transcript: str) -> StructuredRx:
    snippet = transcript.strip()[:120] or "UNKNOWN"
    return StructuredRx(
        drugs=[
            DrugItem(
                name=snippet,
                dose="",
                frequency="",
                duration="",
                flagged=True,
            )
        ],
        raw_transcript=transcript,
        structuring_confidence=0.0,
        notes="LLM JSON parsing failed; manual review required.",
    )


def _sync_structure(
    transcript: str,
    patient_context: dict,
    settings: Settings,
) -> StructuredRx:
    """Groq call + JSON parse (sync — run in thread pool)."""
    allergy_records = _parse_allergy_records(patient_context)
    allergy_list = format_allergy_prompt(allergy_records)
    conditions = patient_context.get("conditions") or []
    conditions_list = ", ".join(str(c) for c in conditions if c)
    system_prompt = _load_system_prompt(allergy_list, conditions_list)
    user_prompt = _build_user_prompt(transcript, patient_context)
    client = Groq(api_key=settings.groq_api_key)

    raw_output = _call_groq(client, system_prompt, user_prompt)
    try:
        return _parse_llm_payload(_extract_json(raw_output), transcript)
    except (ValueError, json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
        logger.warning("Groq JSON parse failed (attempt 1): %s", exc)
        retry_user = (
            f"You returned invalid JSON. Return ONLY JSON. "
            f"Previous attempt: {raw_output[:500]}. Try again:\n\n{user_prompt}"
        )
        raw_output_2 = _call_groq(client, system_prompt, retry_user)
        try:
            return _parse_llm_payload(_extract_json(raw_output_2), transcript)
        except (ValueError, json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc2:
            logger.error("Groq JSON parse failed (attempt 2): %s", exc2)
            return _fallback_result(transcript)


def _call_groq(client: Groq, system: str, user: str) -> str:
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
        max_tokens=1000,
    )
    return response.choices[0].message.content or ""


def _append_allergy_note(notes: str | None, drug_name: str, reaction: str | None) -> str:
    warning = f"ALLERGY WARNING: {drug_name} - {reaction or 'allergic'}"
    if notes and warning not in notes:
        return f"{notes}; {warning}"
    if notes:
        return notes
    return warning


async def _cross_validate_drugs(
    result: StructuredRx,
    settings: Settings,
    allergy_records: list[AllergyRecord],
) -> StructuredRx:
    updated: list[DrugItem] = []
    notes = result.notes
    for drug in result.drugs:
        matched = await recognize_drug(drug.name, settings)
        flagged = drug.flagged or not matched

        is_allergy, allergy_drug, reaction = check_drug_against_allergies(drug.name, allergy_records)
        if is_allergy:
            flagged = True
            notes = _append_allergy_note(notes, drug.name, reaction)

        updated.append(drug.model_copy(update={"flagged": flagged}))

    return result.model_copy(update={"drugs": updated, "notes": notes})


async def structure_prescription(
    transcript: str,
    patient_context: dict,
    settings: Settings | None = None,
) -> StructuredRx:
    """
    Structure a voice transcript into StructuredRx using Groq Llama 3.3 70B.
    """
    from app.config import get_settings

    settings = settings or get_settings()
    transcript = transcript.strip()
    if not transcript:
        return StructuredRx(raw_transcript="", structuring_confidence=0.0)

    allergy_records = _parse_allergy_records(patient_context)
    parsed = await asyncio.to_thread(_sync_structure, transcript, patient_context, settings)
    return await _cross_validate_drugs(parsed, settings, allergy_records)
