"""
Patient allergy CRUD endpoints.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_doctor_clinic_id, get_doctor_membership
from app.config import Settings, get_settings
from app.core.dependencies import get_current_doctor
from app.database import get_db
from app.models import Doctor, Patient, PatientAllergy
from app.schemas import PatientAllergyCreate, PatientAllergyOut
from app.services.allergy_service import resolve_drug_generic

router = APIRouter()


async def _get_patient_for_doctor(
    db: AsyncSession,
    patient_id: str,
    doctor: Doctor,
    membership,
    clinic_id: str,
) -> Patient:
    patient = await db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    if patient.clinic_id != clinic_id:
        raise HTTPException(status_code=403, detail="Patient does not belong to your clinic.")
    if membership.role != "admin" and patient.created_by_doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Access denied for this patient.")
    return patient


@router.get("/patients/{patient_id}/allergies", response_model=list[PatientAllergyOut])
async def list_patient_allergies(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    await _get_patient_for_doctor(db, patient_id, doctor, membership, clinic_id)
    rows = (
        await db.execute(
            select(PatientAllergy)
            .where(
                PatientAllergy.patient_id == patient_id,
                PatientAllergy.deleted_at.is_(None),
            )
            .order_by(PatientAllergy.reported_at.desc())
        )
    ).scalars().all()
    return rows


@router.post("/patients/{patient_id}/allergies", response_model=PatientAllergyOut, status_code=201)
async def add_patient_allergy(
    patient_id: str,
    body: PatientAllergyCreate,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
    settings: Settings = Depends(get_settings),
):
    await _get_patient_for_doctor(db, patient_id, doctor, membership, clinic_id)

    drug_name = body.drug_name.strip()
    drug_generic = await resolve_drug_generic(db, drug_name, settings)

    allergy = PatientAllergy(
        patient_id=patient_id,
        drug_name=drug_name,
        drug_generic=drug_generic,
        reaction=body.reaction.strip() if body.reaction else None,
        severity=body.severity,
        reported_by_doctor_id=doctor.id,
    )
    db.add(allergy)
    await db.commit()
    await db.refresh(allergy)
    return allergy


@router.delete("/patients/{patient_id}/allergies/{allergy_id}", status_code=204)
async def remove_patient_allergy(
    patient_id: str,
    allergy_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    await _get_patient_for_doctor(db, patient_id, doctor, membership, clinic_id)

    allergy = await db.get(PatientAllergy, allergy_id)
    if not allergy or allergy.patient_id != patient_id or allergy.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Allergy not found.")

    allergy.deleted_at = datetime.now(timezone.utc)
    await db.commit()


async def allergy_counts_for_patients(
    db: AsyncSession,
    patient_ids: list[str],
) -> dict[str, int]:
    if not patient_ids:
        return {}
    rows = (
        await db.execute(
            select(PatientAllergy.patient_id, func.count())
            .where(
                PatientAllergy.patient_id.in_(patient_ids),
                PatientAllergy.deleted_at.is_(None),
            )
            .group_by(PatientAllergy.patient_id)
        )
    ).all()
    return {pid: count for pid, count in rows}
