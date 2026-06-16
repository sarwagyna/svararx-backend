"""
Groq Service — Llama-3.3-70b prescription structuring.

Responsibilities:
  1. Send corrected transcription to Groq with a strict medical extraction prompt
  2. Parse JSON response (with one retry on parse failure)
  3. Return a typed StructuredOutput dict

Note: Groq does not support response_format={"type":"json_object"} on all
models — we parse manually and retry with a stricter prompt on failure.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TypedDict

from groq import Groq

from app.services.frequency_utils import normalize_frequency

logger = logging.getLogger(__name__)

# ─── Return type ──────────────────────────────────────────────

class MedicationDict(TypedDict):
    drug_name: str
    dosage: str
    frequency: str
    duration: str
    instruction: str


class StructuredOutput(TypedDict):
    medications: list[MedicationDict]
    diagnosis: str
    advice: str
    follow_up: str
    incomplete_fields: list[str]
    same_as_last_time: bool
    parse_error: bool       # True if JSON parsing failed on both attempts


# ─── Prompts ──────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a medical prescription parser for Indian clinics.
Doctor dictates in Telugu, Hindi, or English (often mixed).
Drug names are always in English.
Extract prescription data and return ONLY valid JSON.
No explanation. No markdown. No backticks. Pure JSON only.

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
  "incomplete_fields": [],
  "same_as_last_time": false
}

STRICT RULES:
- drug_name MUST be in CAPITALS (e.g. "METFORMIN 500MG")
- dosage: leave empty string "" if not stated — NEVER guess
- frequency: ONLY use OD / BD / TDS / QID / SOS — map:
    once daily / once a day → OD
    twice daily / twice a day / BD → BD
    three times / thrice / TDS → TDS
    four times / QID → QID
    as needed / when needed / PRN / avasaram aithe / SOS → SOS
- duration: as stated (e.g. "5 days", "1 month"); "" if not mentioned
- instruction: only if explicitly stated (e.g. "after food"); "" otherwise
- incomplete_fields: list the names of fields that are blank strings
- same_as_last_time: true ONLY if doctor said "same as last time" or equivalent
- If nothing parseable: return empty medications array, all fields ""
- Handle Telugu/Hindi/English code-switching naturally\
"""

_SYSTEM_PROMPT_STRICT = """\
You are a JSON-only medical parser. Output NOTHING except a single valid JSON object.
Do not include any text before or after the JSON.
Do not use markdown code fences.

Required schema:
{"medications":[{"drug_name":"","dosage":"","frequency":"","duration":"","instruction":""}],"diagnosis":"","advice":"","follow_up":"","incomplete_fields":[],"same_as_last_time":false}

Rules: drug_name in CAPITALS, frequency only OD/BD/TDS/QID/SOS, dosage blank if unknown.\
"""

# ─── JSON extraction ──────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """
    Strip markdown fences and extract the first complete JSON object.
    Raises json.JSONDecodeError or ValueError on failure.
    """
    # Remove ```json ... ``` or ``` ... ``` fences
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
    text = text.rstrip("`").strip()

    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in LLM response")

    return json.loads(text[start:end])


def _build_empty_structure(parse_error: bool = False) -> StructuredOutput:
    return StructuredOutput(
        medications=[],
        diagnosis="",
        advice="",
        follow_up="",
        incomplete_fields=["medications", "diagnosis"],
        same_as_last_time=False,
        parse_error=parse_error,
    )


def _parse_response(raw: str) -> dict:
    parsed = _extract_json(raw)

    medications: list[MedicationDict] = []
    for med in parsed.get("medications", []):
        drug_name = str(med.get("drug_name", "")).strip().upper()
        if not drug_name:
            continue
        medications.append(
            MedicationDict(
                drug_name=drug_name,
                dosage=str(med.get("dosage", "")).strip(),
                frequency=normalize_frequency(str(med.get("frequency", ""))),
                duration=str(med.get("duration", "")).strip(),
                instruction=str(med.get("instruction", "")).strip(),
            )
        )

    # Collect incomplete fields
    incomplete: list[str] = list(parsed.get("incomplete_fields", []))
    # Also auto-detect blank mandatory fields not already listed
    for med in medications:
        if not med["dosage"] and "dosage" not in incomplete:
            incomplete.append("dosage")
            break

    return dict(
        medications=medications,
        diagnosis=str(parsed.get("diagnosis", "")).strip(),
        advice=str(parsed.get("advice", "")).strip(),
        follow_up=str(parsed.get("follow_up", "")).strip(),
        incomplete_fields=incomplete,
        same_as_last_time=bool(parsed.get("same_as_last_time", False)),
        parse_error=False,
    )


# ─── Public API ───────────────────────────────────────────────

def structure_prescription(
    transcription: str,
    api_key: str,
    chief_complaint: str | None = None,
    allergy_prompt: str = "",
    conditions_prompt: str = "",
) -> StructuredOutput:
    """
    Synchronous Groq call — run with asyncio.to_thread from async endpoints.

    Attempts JSON parsing once; on failure retries with a stricter prompt.
    On second failure returns an empty structure with parse_error=True.
    """
    if not transcription.strip():
        return _build_empty_structure()

    client = Groq(api_key=api_key)
    complaint_line = (
        f"Chief complaint entered: {chief_complaint}\n"
        if chief_complaint
        else ""
    )
    user_message = f"{complaint_line}Doctor dictation: {transcription}"
    system_prompt = _SYSTEM_PROMPT
    if allergy_prompt:
        system_prompt += (
            f"\n\nCRITICAL: Patient has allergies to: {allergy_prompt}. "
            "If any prescribed drug matches an allergy, note it in incomplete_fields."
        )
    if conditions_prompt:
        system_prompt += (
            f"\n\nPatient chronic conditions: {conditions_prompt}. "
            "Avoid prescribing contraindicated drugs (e.g. NSAIDs in CKD, "
            "metformin in severe renal impairment)."
        )

    # ── First attempt ─────────────────────────────────────────
    raw_output = _call_groq(client, system_prompt, user_message)
    try:
        return StructuredOutput(**_parse_response(raw_output))
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Groq JSON parse failed (attempt 1): %s | raw: %.200s", exc, raw_output)

    # ── Retry with stricter prompt ────────────────────────────
    logger.info("Retrying Groq with strict prompt")
    raw_output_2 = _call_groq(client, _SYSTEM_PROMPT_STRICT, user_message)
    try:
        return StructuredOutput(**_parse_response(raw_output_2))
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.error("Groq JSON parse failed (attempt 2): %s | raw: %.200s", exc, raw_output_2)
        return _build_empty_structure(parse_error=True)


def _call_groq(client: Groq, system: str, user: str) -> str:
    """Make a single Groq chat completion call and return the content string."""
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
        max_tokens=1000,
        # response_format not used — parse manually per spec
    )
    return response.choices[0].message.content or ""
