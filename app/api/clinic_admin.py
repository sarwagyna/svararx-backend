"""
Clinic admin — create doctors, clinic settings (admin only).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_doctor_clinic_id, get_doctor_membership
from app.core.dependencies import get_current_doctor
from app.core.pin import hash_pin, validate_pin_format
from app.database import get_db
from app.models import Clinic, Doctor, DoctorClinic
from app.schemas import (
    ClinicDoctorCard,
    ClinicSettingsResponse,
    ClinicSettingsUpdateRequest,
    CreateClinicDoctorRequest,
)

router = APIRouter()


async def _require_admin(membership: DoctorClinic) -> None:
    if membership.role != "admin":
        raise HTTPException(status_code=403, detail="Only clinic admins can manage the clinic.")


async def _validate_mci_unique(db: AsyncSession, mci_number: str) -> None:
    existing = (
        await db.execute(select(Doctor).where(Doctor.mci_number == mci_number))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="MCI number already registered.")


@router.get("/clinic/settings", response_model=ClinicSettingsResponse)
async def get_clinic_settings(
    membership: DoctorClinic = Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
    db: AsyncSession = Depends(get_db),
):
    await _require_admin(membership)
    clinic = await db.get(Clinic, clinic_id)
    if not clinic:
        raise HTTPException(status_code=404, detail="Clinic not found.")
    return ClinicSettingsResponse(
        clinic_id=clinic.id,
        clinic_name=clinic.name,
        clinic_address=clinic.address_line1,
        clinic_address_line2=clinic.address_line2 or "",
        clinic_city=clinic.city,
        clinic_state=clinic.state,
        clinic_pin=clinic.pincode,
        clinic_phone=clinic.phone or "",
        plan=clinic.plan,
    )


@router.put("/clinic/settings", response_model=ClinicSettingsResponse)
async def update_clinic_settings(
    body: ClinicSettingsUpdateRequest,
    doctor: Doctor = Depends(get_current_doctor),
    membership: DoctorClinic = Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
    db: AsyncSession = Depends(get_db),
):
    await _require_admin(membership)
    clinic = await db.get(Clinic, clinic_id)
    if not clinic:
        raise HTTPException(status_code=404, detail="Clinic not found.")

    clinic.name = body.clinic_name.strip()
    clinic.address_line1 = body.clinic_address.strip()
    clinic.address_line2 = body.clinic_address_line2.strip() or None
    clinic.city = body.clinic_city.strip()
    clinic.state = body.clinic_state.strip() or "Andhra Pradesh"
    clinic.pincode = body.clinic_pin.strip()
    clinic.phone = body.clinic_phone.strip() or None

    doctor.clinic_name = clinic.name
    doctor.clinic_address = clinic.address_line1
    doctor.clinic_address_line2 = clinic.address_line2
    doctor.clinic_city = clinic.city
    doctor.clinic_state = clinic.state
    doctor.clinic_pin = clinic.pincode
    doctor.clinic_phone = clinic.phone

    await db.commit()
    await db.refresh(clinic)
    return ClinicSettingsResponse(
        clinic_id=clinic.id,
        clinic_name=clinic.name,
        clinic_address=clinic.address_line1,
        clinic_address_line2=clinic.address_line2 or "",
        clinic_city=clinic.city,
        clinic_state=clinic.state,
        clinic_pin=clinic.pincode,
        clinic_phone=clinic.phone or "",
        plan=clinic.plan,
    )


@router.post("/clinic/doctors", response_model=ClinicDoctorCard)
async def create_clinic_doctor(
    body: CreateClinicDoctorRequest,
    membership: DoctorClinic = Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
    db: AsyncSession = Depends(get_db),
):
    """Add a doctor profile to the clinic (admin onboarding — PIN + credentials)."""
    await _require_admin(membership)

    mci = body.mci_reg_number.strip()
    await _validate_mci_unique(db, mci)

    try:
        pin_hash = hash_pin(validate_pin_format(body.approval_pin))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    role = body.role if body.role in ("doctor", "compounder") else "doctor"

    new_doctor = Doctor(
        auth_user_id=None,
        name=body.full_name.strip(),
        qualifications=body.qualifications.strip(),
        mci_number=mci,
        speciality=body.specialization.strip(),
        state_council_reg=body.state_council_reg.strip() or None,
        pin_hash=pin_hash,
        practice_mode="clinic",
        onboarding_completed=True,
        onboarding_step=4,
        is_active=True,
    )
    db.add(new_doctor)
    await db.flush()

    db.add(
        DoctorClinic(
            doctor_id=new_doctor.id,
            clinic_id=clinic_id,
            role=role,
        )
    )
    await db.commit()
    await db.refresh(new_doctor)

    return ClinicDoctorCard(
        id=new_doctor.id,
        name=new_doctor.name,
        speciality=new_doctor.speciality,
        has_pin=True,
    )
