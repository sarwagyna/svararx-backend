"""
POST /api/v1/rx/structure — structure a Redis-cached transcript via Groq LLM.
POST /api/v1/rx/complete   — structure + save draft + link consultation.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_doctor_clinic_id, get_doctor_membership
from app.config import Settings, get_settings
from app.core.dependencies import get_current_doctor
from app.core.tenant import legacy_redis_transcript_key, redis_transcript_key
from app.database import get_db
from app.models import Consultation, Doctor, Patient, Prescription
from app.services.allergy_service import (
    allergies_to_context,
    fetch_patient_allergies,
)
from app.services.condition_service import fetch_patient_conditions_for_context
from app.models.rx import StructuredRx
from app.schemas import LinkPatientRequest, PrescriptionDetail, StructuredPrescription
from app.services.consultation_service import (
    complete_consultation_with_prescription,
    merge_chief_complaint_into_structured,
)
from app.services.patient_history import (
    build_visits_summary,
    extract_drugs,
    fetch_last_n_prescriptions,
    invalidate_patient_history_cache,
)
from app.services.redis_client import get_redis
from app.services.rx_structurer import structure_prescription

logger = logging.getLogger(__name__)
router = APIRouter()


class RxStructureRequest(BaseModel):
    recording_id: str
    patient_id: str | None = None
    consultation_id: str | None = None
    chief_complaint: str | None = None


class RxCompleteRequest(BaseModel):
    recording_id: str
    patient_id: str | None = None
    consultation_id: str | None = None


class RxStructureResponse(BaseModel):
    structured: StructuredRx
    prescription_id: str | None = None


async def _fetch_transcript(recording_id: str, doctor_id: str) -> str:
    redis_client = get_redis()
    raw = await asyncio.to_thread(redis_client.get, redis_transcript_key(doctor_id, recording_id))
    if not raw:
        raw = await asyncio.to_thread(
            redis_client.get, legacy_redis_transcript_key(recording_id)
        )
    if not raw:
        raise HTTPException(status_code=404, detail="Transcript not found or expired.")
    payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    transcript = payload.get("transcript", "").strip()
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript is empty.")
    return transcript


async def _load_consultation(
    db: AsyncSession,
    consultation_id: str | None,
    doctor_id: str,
) -> Consultation | None:
    if not consultation_id:
        return None
    consultation = await db.get(Consultation, consultation_id)
    if not consultation or consultation.doctor_id != doctor_id:
        raise HTTPException(status_code=404, detail="Consultation not found.")
    return consultation


async def _fetch_patient_context(
    db: AsyncSession,
    patient_id: str,
    clinic_id: str,
    chief_complaint: str | None = None,
) -> dict:
    patient = await db.get(Patient, patient_id)
    if not patient or patient.clinic_id != clinic_id:
        raise HTTPException(status_code=404, detail="Patient not found.")

    allergy_records = await fetch_patient_allergies(db, patient_id)
    allergies = allergies_to_context(allergy_records)

    last_visits = await fetch_last_n_prescriptions(db, patient_id, clinic_id, n=3)
    visits_summary = build_visits_summary(last_visits)

    last_rx: list[str] = []
    if last_visits:
        for d in extract_drugs(last_visits[0].structured_json or {}):
            last_rx.append(d["name"])

    conditions = await fetch_patient_conditions_for_context(db, patient_id)

    return {
        "allergies": allergies,
        "conditions": conditions,
        "last_rx": last_rx,
        "visits_summary": visits_summary,
        "chief_complaint": chief_complaint,
    }


async def _structure_and_save(
    *,
    db: AsyncSession,
    transcript: str,
    doctor: Doctor,
    clinic_id: str,
    patient_id: str | None,
    consultation_id: str | None,
    chief_complaint: str | None,
    settings: Settings,
    link_consultation: bool,
) -> RxStructureResponse:
    consultation = await _load_consultation(db, consultation_id, doctor.id)
    complaint = chief_complaint or (consultation.chief_complaint if consultation else None)
    tags = list(consultation.chief_complaint_tags or []) if consultation else []

    patient_context: dict = {
        "allergies": [],
        "conditions": [],
        "last_rx": [],
        "visits_summary": "No prior visits.",
        "chief_complaint": complaint,
    }
    effective_patient_id = patient_id or (consultation.patient_id if consultation else None)
    if effective_patient_id:
        patient_context = await _fetch_patient_context(
            db, effective_patient_id, clinic_id, chief_complaint=complaint
        )

    structured = await structure_prescription(transcript, patient_context, settings)

    structured_data = merge_chief_complaint_into_structured(
        structured.model_dump(),
        complaint,
        tags,
    )
    prescription = Prescription(
        clinic_id=clinic_id,
        doctor_id=doctor.id,
        patient_id=effective_patient_id,
        raw_transcription=transcript,
        structured_json=structured_data,
        status="draft",
    )
    db.add(prescription)
    await db.flush()

    if link_consultation and consultation_id:
        await complete_consultation_with_prescription(
            db, consultation_id, doctor.id, prescription.id
        )

    await db.commit()
    await db.refresh(prescription)
    prescription_id = prescription.id
    if effective_patient_id:
        await invalidate_patient_history_cache(effective_patient_id)

    return RxStructureResponse(structured=structured, prescription_id=prescription_id)


@router.post("/rx/structure", response_model=RxStructureResponse)
async def structure_rx_from_recording(
    body: RxStructureRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    transcript = await _fetch_transcript(body.recording_id, doctor.id)

    try:
        return await _structure_and_save(
            db=db,
            transcript=transcript,
            doctor=doctor,
            clinic_id=clinic_id,
            patient_id=body.patient_id,
            consultation_id=body.consultation_id,
            chief_complaint=body.chief_complaint,
            settings=settings,
            link_consultation=False,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Rx structuring failed for recording %s", body.recording_id)
        raise HTTPException(status_code=502, detail=f"Structuring failed: {exc}") from exc


@router.post("/rx/complete", response_model=RxStructureResponse)
async def complete_rx_from_recording(
    body: RxCompleteRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    transcript = await _fetch_transcript(body.recording_id, doctor.id)

    try:
        return await _structure_and_save(
            db=db,
            transcript=transcript,
            doctor=doctor,
            clinic_id=clinic_id,
            patient_id=body.patient_id,
            consultation_id=body.consultation_id,
            chief_complaint=None,
            settings=settings,
            link_consultation=True,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Rx complete failed for recording %s", body.recording_id)
        raise HTTPException(status_code=502, detail=f"Rx complete failed: {exc}") from exc


@router.post("/rx/{prescription_id}/link-patient", response_model=PrescriptionDetail)
async def link_patient_to_prescription(
    prescription_id: str,
    body: LinkPatientRequest,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    rx = await db.get(Prescription, prescription_id)
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found.")
    if rx.clinic_id != clinic_id:
        raise HTTPException(status_code=403, detail="Prescription does not belong to your clinic.")
    if membership.role != "admin" and rx.doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Access denied for this prescription.")
    if rx.status != "draft":
        raise HTTPException(status_code=409, detail="Only draft prescriptions can be linked to a patient.")
    if rx.patient_id:
        raise HTTPException(status_code=409, detail="Prescription already has a linked patient.")

    patient = await db.get(Patient, body.patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    if patient.clinic_id != clinic_id:
        raise HTTPException(status_code=403, detail="Patient does not belong to your clinic.")
    if membership.role != "admin" and patient.created_by_doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Access denied for this patient.")

    rx.patient_id = body.patient_id
    await db.commit()
    await db.refresh(rx)
    await invalidate_patient_history_cache(body.patient_id)

    structured_data = rx.structured_json or {}
    return PrescriptionDetail(
        id=rx.id,
        patient_id=rx.patient_id,
        created_at=rx.created_at,
        approved_at=rx.approved_at,
        status=rx.status,
        structured=StructuredPrescription(**structured_data),
        pdf_s3_key=rx.pdf_s3_key,
        raw_transcription=rx.raw_transcription,
    )
