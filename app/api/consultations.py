"""
Consultation lifecycle — chief complaint capture before dictation.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_doctor_clinic_id, get_doctor_membership
from app.core.dependencies import get_current_doctor
from app.database import get_db
from app.models import Consultation, Doctor, Patient
from app.schemas import (
    ConsultationOut,
    ConsultationStartRequest,
    ConsultationCompleteRequest,
)

router = APIRouter()


async def _get_active_consultation(
    db: AsyncSession,
    doctor_id: str,
    clinic_id: str,
) -> Consultation | None:
    result = await db.execute(
        select(Consultation)
        .where(
            Consultation.doctor_id == doctor_id,
            Consultation.clinic_id == clinic_id,
            Consultation.completed_at.is_(None),
        )
        .order_by(Consultation.started_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _to_out(c: Consultation) -> ConsultationOut:
    return ConsultationOut(
        id=c.id,
        doctor_id=c.doctor_id,
        patient_id=c.patient_id,
        chief_complaint=c.chief_complaint,
        chief_complaint_tags=list(c.chief_complaint_tags or []),
        started_at=c.started_at,
        completed_at=c.completed_at,
        prescription_id=c.prescription_id,
    )


@router.get("/consultations/active", response_model=ConsultationOut)
async def get_active_consultation(
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    active = await _get_active_consultation(db, doctor.id, clinic_id)
    if not active:
        raise HTTPException(status_code=404, detail="No active consultation.")
    return _to_out(active)


@router.post("/consultations/start", response_model=ConsultationOut)
async def start_consultation(
    body: ConsultationStartRequest,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    active = await _get_active_consultation(db, doctor.id, clinic_id)
    if active:
        raise HTTPException(
            status_code=409,
            detail="An active consultation already exists. Complete it before starting a new one.",
        )

    if body.patient_id:
        patient = await db.get(Patient, body.patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found.")
        if patient.clinic_id != clinic_id:
            raise HTTPException(status_code=403, detail="Patient does not belong to your clinic.")
        if membership.role != "admin" and patient.created_by_doctor_id != doctor.id:
            raise HTTPException(status_code=403, detail="Access denied for this patient.")

    consultation = Consultation(
        doctor_id=doctor.id,
        clinic_id=clinic_id,
        patient_id=body.patient_id,
        chief_complaint=body.chief_complaint,
        chief_complaint_tags=body.tags or [],
        visit_type=body.visit_type,
    )
    db.add(consultation)
    await db.commit()
    await db.refresh(consultation)
    return _to_out(consultation)


@router.patch("/consultations/{consultation_id}/complete", response_model=ConsultationOut)
async def complete_consultation(
    consultation_id: str,
    body: ConsultationCompleteRequest,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    consultation = await db.get(Consultation, consultation_id)
    if not consultation or consultation.doctor_id != doctor.id:
        raise HTTPException(status_code=404, detail="Consultation not found.")
    if consultation.clinic_id != clinic_id:
        raise HTTPException(status_code=403, detail="Consultation does not belong to your clinic.")
    if consultation.completed_at:
        raise HTTPException(status_code=409, detail="Consultation already completed.")

    consultation.completed_at = datetime.now(timezone.utc)
    if body.prescription_id:
        consultation.prescription_id = body.prescription_id
    await db.commit()
    await db.refresh(consultation)
    return _to_out(consultation)
