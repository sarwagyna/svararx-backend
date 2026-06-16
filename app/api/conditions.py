"""
Patient chronic condition CRUD endpoints.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_doctor_clinic_id, get_doctor_membership
from app.core.dependencies import get_current_doctor
from app.database import get_db
from app.models import (
    Doctor,
    Patient,
    PatientCondition,
    PatientConditionSuggestion,
)
from app.schemas import (
    PatientConditionCreate,
    PatientConditionOut,
    PatientConditionSuggestionOut,
    PatientConditionUpdate,
)

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


@router.get(
    "/patients/{patient_id}/conditions",
    response_model=list[PatientConditionOut],
)
async def list_patient_conditions(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    await _get_patient_for_doctor(db, patient_id, doctor, membership, clinic_id)
    rows = (
        await db.execute(
            select(PatientCondition)
            .where(
                PatientCondition.patient_id == patient_id,
                PatientCondition.status == "active",
            )
            .order_by(PatientCondition.created_at.desc())
        )
    ).scalars().all()
    return rows


@router.post(
    "/patients/{patient_id}/conditions",
    response_model=PatientConditionOut,
    status_code=201,
)
async def add_patient_condition(
    patient_id: str,
    body: PatientConditionCreate,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    await _get_patient_for_doctor(db, patient_id, doctor, membership, clinic_id)

    condition_name = body.condition_name.strip()
    if not condition_name:
        raise HTTPException(status_code=400, detail="Condition name is required.")

    existing = (
        await db.execute(
            select(PatientCondition).where(
                PatientCondition.patient_id == patient_id,
                PatientCondition.condition_name == condition_name,
                PatientCondition.status == "active",
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Condition already recorded as active.")

    condition = PatientCondition(
        patient_id=patient_id,
        condition_name=condition_name,
        condition_code=body.condition_code.strip() if body.condition_code else None,
        diagnosed_at=body.diagnosed_at,
        status="active",
        added_by_doctor_id=doctor.id,
    )
    db.add(condition)
    await db.commit()
    await db.refresh(condition)
    return condition


@router.patch(
    "/patients/{patient_id}/conditions/{condition_id}",
    response_model=PatientConditionOut,
)
async def update_patient_condition(
    patient_id: str,
    condition_id: str,
    body: PatientConditionUpdate,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    await _get_patient_for_doctor(db, patient_id, doctor, membership, clinic_id)

    condition = await db.get(PatientCondition, condition_id)
    if not condition or condition.patient_id != patient_id:
        raise HTTPException(status_code=404, detail="Condition not found.")

    if body.status is not None:
        if body.status not in ("active", "resolved", "monitoring"):
            raise HTTPException(status_code=400, detail="Invalid status.")
        condition.status = body.status

    await db.commit()
    await db.refresh(condition)
    return condition


@router.get(
    "/patients/{patient_id}/condition-suggestions",
    response_model=list[PatientConditionSuggestionOut],
)
async def list_condition_suggestions(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    await _get_patient_for_doctor(db, patient_id, doctor, membership, clinic_id)
    rows = (
        await db.execute(
            select(PatientConditionSuggestion)
            .where(
                PatientConditionSuggestion.patient_id == patient_id,
                PatientConditionSuggestion.status == "pending",
            )
            .order_by(PatientConditionSuggestion.suggested_at.desc())
        )
    ).scalars().all()
    return rows


@router.post(
    "/patients/{patient_id}/condition-suggestions/{suggestion_id}/confirm",
    response_model=PatientConditionOut,
    status_code=201,
)
async def confirm_condition_suggestion(
    patient_id: str,
    suggestion_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    await _get_patient_for_doctor(db, patient_id, doctor, membership, clinic_id)

    suggestion = await db.get(PatientConditionSuggestion, suggestion_id)
    if not suggestion or suggestion.patient_id != patient_id:
        raise HTTPException(status_code=404, detail="Suggestion not found.")
    if suggestion.status != "pending":
        raise HTTPException(status_code=400, detail="Suggestion already reviewed.")

    condition = PatientCondition(
        patient_id=patient_id,
        condition_name=suggestion.condition_name,
        status="active",
        added_by_doctor_id=doctor.id,
    )
    db.add(condition)

    suggestion.status = "confirmed"
    suggestion.reviewed_by_doctor_id = doctor.id
    suggestion.reviewed_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(condition)
    return condition


@router.post(
    "/patients/{patient_id}/condition-suggestions/{suggestion_id}/dismiss",
    status_code=204,
)
async def dismiss_condition_suggestion(
    patient_id: str,
    suggestion_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    await _get_patient_for_doctor(db, patient_id, doctor, membership, clinic_id)

    suggestion = await db.get(PatientConditionSuggestion, suggestion_id)
    if not suggestion or suggestion.patient_id != patient_id:
        raise HTTPException(status_code=404, detail="Suggestion not found.")
    if suggestion.status != "pending":
        raise HTTPException(status_code=400, detail="Suggestion already reviewed.")

    suggestion.status = "dismissed"
    suggestion.reviewed_by_doctor_id = doctor.id
    suggestion.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
