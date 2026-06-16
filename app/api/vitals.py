"""
Vitals capture API — typed entry for current consultation.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_doctor_clinic_id, get_doctor_membership
from app.core.dependencies import get_current_doctor
from app.database import get_db
from app.models import Consultation, Doctor, Vital
from app.schemas import VitalCreate, VitalFlag, VitalOut, VitalRecordResponse
from app.services.patient_history import verify_patient_access
from app.services.vitals_flags import compute_vital_flags

router = APIRouter()


def _vital_to_out(vital: Vital, flags: list[dict[str, str]] | None = None) -> VitalOut:
    weight = float(vital.weight_kg) if vital.weight_kg is not None else None
    temp = float(vital.temperature_f) if vital.temperature_f is not None else None
    height = float(vital.height_cm) if vital.height_cm is not None else None
    return VitalOut(
        id=vital.id,
        consultation_id=vital.consultation_id,
        patient_id=vital.patient_id,
        doctor_id=vital.doctor_id,
        bp_systolic=vital.bp_systolic,
        bp_diastolic=vital.bp_diastolic,
        weight_kg=weight,
        blood_sugar_mg_dl=vital.blood_sugar_mg_dl,
        blood_sugar_type=vital.blood_sugar_type,
        spo2_percent=vital.spo2_percent,
        temperature_f=temp,
        pulse_bpm=vital.pulse_bpm,
        height_cm=height,
        respiratory_rate=vital.respiratory_rate,
        recorded_at=vital.recorded_at,
        flags=[VitalFlag(**f) for f in (flags or compute_vital_flags(
            bp_systolic=vital.bp_systolic,
            blood_sugar_mg_dl=vital.blood_sugar_mg_dl,
            blood_sugar_type=vital.blood_sugar_type,
            spo2_percent=vital.spo2_percent,
        ))],
    )


def _has_any_reading(body: VitalCreate) -> bool:
    return any(
        v is not None
        for v in (
            body.bp_systolic,
            body.bp_diastolic,
            body.weight_kg,
            body.blood_sugar_mg_dl,
            body.spo2_percent,
            body.temperature_f,
            body.pulse_bpm,
            body.height_cm,
            body.respiratory_rate,
        )
    )


@router.post("/vitals", response_model=VitalRecordResponse, status_code=201)
async def record_vitals(
    body: VitalCreate,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
    membership=Depends(get_doctor_membership),
):
    if not _has_any_reading(body):
        raise HTTPException(status_code=422, detail="At least one vital sign must be provided.")

    await verify_patient_access(db, body.patient_id, clinic_id, doctor, membership)

    if body.consultation_id:
        consultation = await db.get(Consultation, body.consultation_id)
        if not consultation or consultation.doctor_id != doctor.id:
            raise HTTPException(status_code=404, detail="Consultation not found.")
        if consultation.patient_id and consultation.patient_id != body.patient_id:
            raise HTTPException(status_code=404, detail="Consultation not found for this patient.")

    vital = Vital(
        consultation_id=body.consultation_id,
        patient_id=body.patient_id,
        doctor_id=doctor.id,
        bp_systolic=body.bp_systolic,
        bp_diastolic=body.bp_diastolic,
        weight_kg=Decimal(str(body.weight_kg)) if body.weight_kg is not None else None,
        blood_sugar_mg_dl=body.blood_sugar_mg_dl,
        blood_sugar_type=body.blood_sugar_type,
        spo2_percent=body.spo2_percent,
        temperature_f=Decimal(str(body.temperature_f)) if body.temperature_f is not None else None,
        pulse_bpm=body.pulse_bpm,
        height_cm=Decimal(str(body.height_cm)) if body.height_cm is not None else None,
        respiratory_rate=body.respiratory_rate,
    )
    db.add(vital)
    await db.commit()
    await db.refresh(vital)

    flags = compute_vital_flags(
        bp_systolic=vital.bp_systolic,
        blood_sugar_mg_dl=vital.blood_sugar_mg_dl,
        blood_sugar_type=vital.blood_sugar_type,
        spo2_percent=vital.spo2_percent,
    )
    return VitalRecordResponse(vitals=_vital_to_out(vital, flags), flags=[VitalFlag(**f) for f in flags])


@router.get("/patients/{patient_id}/vitals", response_model=list[VitalOut])
async def list_patient_vitals(
    patient_id: str,
    limit: int = Query(default=10, le=50),
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
    membership=Depends(get_doctor_membership),
):
    await verify_patient_access(db, patient_id, clinic_id, doctor, membership)

    rows = await db.execute(
        select(Vital)
        .where(Vital.patient_id == patient_id)
        .order_by(Vital.recorded_at.desc())
        .limit(limit)
    )
    return [_vital_to_out(v) for v in rows.scalars().all()]


@router.get("/patients/{patient_id}/vitals/latest", response_model=VitalOut | None)
async def get_latest_patient_vitals(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
    membership=Depends(get_doctor_membership),
):
    await verify_patient_access(db, patient_id, clinic_id, doctor, membership)

    row = await db.execute(
        select(Vital)
        .where(Vital.patient_id == patient_id)
        .order_by(Vital.recorded_at.desc())
        .limit(1)
    )
    vital = row.scalar_one_or_none()
    if not vital:
        return None
    return _vital_to_out(vital)
