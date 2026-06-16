"""
POST /api/v1/transcribe-and-structure

Combined pipeline:
  audio → Sarvam STT → self-correction → drug correction → Groq structuring

Request:  multipart/form-data  { audio: File, patient_id?: str }
Response: TranscribeAndStructureResponse
"""
from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, Settings
from app.core.dependencies import get_current_doctor
from app.database import get_db
from app.auth import get_doctor_clinic_id
from app.models import Prescription
from app.services.stt_service import transcribe_audio, TranscriptionResult
from app.services.groq_service import structure_prescription, StructuredOutput
from app.services.allergy_service import (
    apply_allergy_flags_to_prescription,
    fetch_patient_allergies,
    format_allergy_prompt,
)
from app.services.condition_service import (
    fetch_patient_conditions_for_context,
    format_conditions_prompt,
)
from app.services.drug_correction import correct_drug_names
from app.schemas import StructuredPrescription, MedicationItem

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_AUDIO_BYTES = 50 * 1024 * 1024   # 50 MB hard cap


# ─── Response schema ──────────────────────────────────────────

class CorrectionEntry(BaseModel):
    original: str
    corrected: str
    score: float


class MedicationOut(BaseModel):
    drug_name: str
    dosage: str
    frequency: str
    duration: str
    instruction: str
    flagged: bool = False
    allergy_drug: str | None = None
    allergy_warning: str | None = None


class StructuredOut(BaseModel):
    medications: list[MedicationOut]
    diagnosis: str
    advice: str
    follow_up: str
    incomplete_fields: list[str]
    same_as_last_time: bool


class TranscribeAndStructureResponse(BaseModel):
    raw_transcription: str
    corrected_transcription: str
    corrections_made: list[CorrectionEntry]
    low_confidence_terms: list[str]
    structured: StructuredOut
    prescription_id: str | None = None
    processing_time_ms: int
    stt_time_ms: int
    groq_time_ms: int
    groq_error: bool = False        # True if Groq failed; structured will be empty


# ─── Endpoint ─────────────────────────────────────────────────

@router.post(
    "/transcribe-and-structure",
    response_model=TranscribeAndStructureResponse,
    summary="Voice → structured prescription (Sarvam STT + Groq LLM)",
)
async def transcribe_and_structure(
    audio: UploadFile = File(..., description="WebM/Opus audio from browser"),
    patient_id: str | None = Form(default=None, description="Optional patient ID"),
    chief_complaint: str | None = Form(default=None, description="Chief complaint from consultation"),
    consultation_id: str | None = Form(default=None, description="Active consultation ID"),
    settings: Settings = Depends(get_settings),
    doctor=Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Full pipeline endpoint used by the frontend push-to-talk flow.

    Error behaviour:
    - Empty audio            → 400
    - Audio > 60 s           → 400
    - Sarvam API failure     → 503
    - Groq API failure       → 200 with groq_error=true and empty structured
    """
    t_start = time.monotonic()

    # ── Read audio ────────────────────────────────────────────
    audio_bytes = await audio.read()

    if len(audio_bytes) < 100:
        raise HTTPException(status_code=400, detail="Audio file is empty.")

    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Audio file exceeds {MAX_AUDIO_BYTES // (1024*1024)} MB limit.",
        )

    filename = audio.filename or "recording.webm"

    # ── STT (Sarvam) ──────────────────────────────────────────
    stt_start = time.monotonic()
    try:
        stt_result: TranscriptionResult = await asyncio.wait_for(
            transcribe_audio(
                audio_bytes=audio_bytes,
                filename=filename,
                api_key=settings.sarvam_api_key,
                engine=settings.resolve_stt_engine(),
            ),
            timeout=settings.stt_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        logger.error("Sarvam STT timed out after %ds", settings.stt_timeout_seconds)
        raise HTTPException(
            status_code=504,
            detail="Speech-to-text request timed out. Please retry with a shorter recording.",
        ) from exc
    except ValueError as exc:
        # Empty audio or duration exceeded
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        # Sarvam API failure
        logger.error("Sarvam STT error: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Speech-to-text service unavailable: {exc}",
        )
    stt_time_ms = int((time.monotonic() - stt_start) * 1000)

    corrected_transcription = stt_result["corrected"]

    allergy_prompt = ""
    allergy_records = []
    conditions_prompt = ""
    if patient_id:
        allergy_records = await fetch_patient_allergies(db, patient_id)
        allergy_prompt = format_allergy_prompt(allergy_records)
        conditions = await fetch_patient_conditions_for_context(db, patient_id)
        conditions_prompt = format_conditions_prompt(conditions)

    # ── Structuring (Groq) ────────────────────────────────────
    groq_error = False
    structured_raw: StructuredOutput
    groq_start = time.monotonic()

    try:
        structured_raw = await asyncio.wait_for(
            asyncio.to_thread(
                structure_prescription,
                corrected_transcription,
                settings.groq_api_key,
                chief_complaint,
                allergy_prompt,
                conditions_prompt,
            ),
            timeout=settings.groq_timeout_seconds,
        )
        if structured_raw["parse_error"]:
            groq_error = True
            logger.warning(
                "Groq returned unparseable JSON for transcription: %.100s",
                corrected_transcription,
            )
    except asyncio.TimeoutError as exc:
        logger.error("Groq structuring timed out after %ds", settings.groq_timeout_seconds)
        groq_error = True
        structured_raw = StructuredOutput(
            medications=[],
            diagnosis="",
            advice="",
            follow_up="",
            incomplete_fields=[],
            same_as_last_time=False,
            parse_error=True,
        )
    except Exception as exc:
        logger.error("Groq service error: %s", exc)
        groq_error = True
        structured_raw = StructuredOutput(
            medications=[],
            diagnosis="",
            advice="",
            follow_up="",
            incomplete_fields=[],
            same_as_last_time=False,
            parse_error=True,
        )
    groq_time_ms = int((time.monotonic() - groq_start) * 1000)

    # Apply drug correction + allergy cross-check
    structured_for_flags = StructuredPrescription(
        medications=[
            MedicationItem(
                drug_name=m["drug_name"],
                dosage=m["dosage"],
                frequency=m["frequency"],
                duration=m["duration"],
                instruction=m["instruction"],
            )
            for m in structured_raw["medications"]
        ],
        diagnosis=structured_raw["diagnosis"],
        advice=structured_raw["advice"],
        follow_up=structured_raw["follow_up"],
        same_as_last_time=structured_raw["same_as_last_time"],
    )
    if not groq_error:
        structured_for_flags = await correct_drug_names(structured_for_flags, settings)
        structured_for_flags = apply_allergy_flags_to_prescription(structured_for_flags, allergy_records)

    prescription_id: str | None = None
    if not groq_error:
        prescription = Prescription(
            clinic_id=clinic_id,
            doctor_id=doctor.id,
            patient_id=patient_id,
            raw_transcription=corrected_transcription,
            structured_json=structured_for_flags.model_dump(),
            status="draft",
        )
        db.add(prescription)
        await db.commit()
        await db.refresh(prescription)
        prescription_id = prescription.id

    # ── Build response ──────────────────────────────────────
    elapsed_ms = int((time.monotonic() - t_start) * 1000)
    if elapsed_ms > settings.sla_threshold_seconds * 1000:
        logger.warning(
            "Transcribe-and-structure exceeded SLA (%ds): stt=%dms groq=%dms total=%dms",
            settings.sla_threshold_seconds,
            stt_time_ms,
            groq_time_ms,
            elapsed_ms,
        )

    return TranscribeAndStructureResponse(
        raw_transcription=stt_result["raw"],
        corrected_transcription=corrected_transcription,
        corrections_made=[
            CorrectionEntry(**c) for c in stt_result["corrections"]
        ],
        low_confidence_terms=stt_result["low_confidence"],
        structured=StructuredOut(
            medications=[
                MedicationOut(
                    drug_name=m.drug_name,
                    dosage=m.dosage,
                    frequency=m.frequency,
                    duration=m.duration,
                    instruction=m.instruction,
                    flagged=m.flagged,
                    allergy_drug=m.allergy_drug,
                    allergy_warning=m.allergy_warning,
                )
                for m in structured_for_flags.medications
            ],
            diagnosis=structured_for_flags.diagnosis,
            advice=structured_for_flags.advice,
            follow_up=structured_for_flags.follow_up,
            incomplete_fields=structured_raw["incomplete_fields"],
            same_as_last_time=structured_for_flags.same_as_last_time,
        ),
        prescription_id=prescription_id,
        processing_time_ms=elapsed_ms,
        stt_time_ms=stt_time_ms,
        groq_time_ms=groq_time_ms,
        groq_error=groq_error,
    )
