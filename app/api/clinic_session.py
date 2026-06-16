"""
Clinic UX session — tier-aware routing and PIN approval gestures.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_doctor_clinic_id, get_doctor_membership
from app.core.dependencies import get_current_doctor
from app.core.pin import doctor_has_pin, hash_pin, validate_pin_format, verify_pin
from app.core.security import (
    APPROVAL_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    create_approval_token,
    create_refresh_token,
)
from app.database import get_db
from app.models import Clinic, Doctor, DoctorClinic
from app.schemas import (
    ActAsDoctorRequest,
    ClinicDoctorCard,
    ClinicUxContext,
    SetPinRequest,
    TokenResponse,
    VerifyPinRequest,
    VerifyPinResponse,
)

router = APIRouter()


def _solo_default_path() -> str:
    return "/prescribe"


def _multi_default_path(role: str) -> str:
    if role == "compounder":
        return "/prescribe"
    return "/"


async def _clinic_doctors(db: AsyncSession, clinic_id: str) -> list[Doctor]:
    return list(
        (
            await db.execute(
                select(Doctor)
                .join(DoctorClinic, DoctorClinic.doctor_id == Doctor.id)
                .where(
                    DoctorClinic.clinic_id == clinic_id,
                    DoctorClinic.is_active == True,
                    Doctor.is_active == True,
                )
                .order_by(Doctor.name)
            )
        ).scalars().all()
    )


def _build_context(
    *,
    clinic: Clinic,
    membership: DoctorClinic,
    doctor: Doctor,
    doctors: list[Doctor],
) -> ClinicUxContext:
    doctor_count = len(doctors)
    is_solo = doctor_count <= 1
    role = membership.role
    practice_mode = doctor.practice_mode or "solo"
    uses_clinic_layer = (
        practice_mode == "clinic" or doctor_count > 1 or (not is_solo and role != "compounder")
    )

    requires_doctor_selection = uses_clinic_layer and role != "compounder"
    requires_pin_to_approve = (not is_solo) and role == "compounder"
    can_prescribe_directly = is_solo or role == "compounder" or role == "admin"

    if is_solo and doctors:
        active = doctors[0]
    else:
        active = doctor

    default_path = _solo_default_path() if is_solo else _multi_default_path(role)

    return ClinicUxContext(
        clinic_id=clinic.id,
        clinic_name=clinic.name,
        plan=clinic.plan,
        doctor_count=doctor_count,
        is_solo=is_solo,
        practice_mode=practice_mode,
        uses_clinic_layer=uses_clinic_layer,
        membership_role=role,
        default_path=default_path,
        requires_doctor_selection=requires_doctor_selection,
        requires_pin_to_approve=requires_pin_to_approve,
        can_prescribe_directly=can_prescribe_directly,
        active_doctor_id=active.id,
        active_doctor_name=active.name,
        doctors=[
            ClinicDoctorCard(
                id=d.id,
                name=d.name,
                speciality=d.speciality,
                has_pin=doctor_has_pin(d.pin_hash),
            )
            for d in doctors
        ],
    )


@router.get("/auth/context", response_model=ClinicUxContext)
async def get_clinic_ux_context(
    doctor: Doctor = Depends(get_current_doctor),
    membership: DoctorClinic = Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
    db: AsyncSession = Depends(get_db),
):
    """Tier-aware UX flags — solo clinics never surface the clinic layer."""
    clinic = await db.get(Clinic, clinic_id)
    if not clinic:
        raise HTTPException(status_code=404, detail="Clinic not found.")
    doctors = await _clinic_doctors(db, clinic_id)
    return _build_context(clinic=clinic, membership=membership, doctor=doctor, doctors=doctors)


@router.post("/auth/pin", status_code=204)
async def set_doctor_pin(
    body: SetPinRequest,
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
):
    """Set or update the logged-in doctor's 4-digit approval PIN."""
    try:
        doctor.pin_hash = hash_pin(body.pin)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()


@router.post("/auth/verify-pin", response_model=VerifyPinResponse)
async def verify_doctor_pin(
    body: VerifyPinRequest,
    doctor: Doctor = Depends(get_current_doctor),
    membership: DoctorClinic = Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Approval gesture — unlocks approve for one prescription.
    Does NOT switch the authenticated session (compounder stays logged in).
    """
    target = await db.get(Doctor, body.doctor_id)
    if not target or not target.is_active:
        raise HTTPException(status_code=404, detail="Doctor not found.")

    linked = (
        await db.execute(
            select(DoctorClinic).where(
                DoctorClinic.doctor_id == target.id,
                DoctorClinic.clinic_id == clinic_id,
                DoctorClinic.is_active == True,
            )
        )
    ).scalar_one_or_none()
    if not linked:
        raise HTTPException(status_code=403, detail="Doctor does not belong to this clinic.")

    if not doctor_has_pin(target.pin_hash):
        raise HTTPException(status_code=422, detail="Doctor has not set an approval PIN yet.")

    try:
        validate_pin_format(body.pin)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not verify_pin(body.pin, target.pin_hash):
        raise HTTPException(status_code=403, detail="Incorrect PIN.")

    token = create_approval_token(
        target.id,
        clinic_id,
        prescription_id=body.prescription_id,
        issued_by=doctor.id if membership.role == "compounder" else None,
    )
    return VerifyPinResponse(
        approval_token=token,
        doctor_id=target.id,
        doctor_name=target.name,
        expires_in_seconds=APPROVAL_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/auth/act-as-doctor", response_model=TokenResponse)
async def act_as_doctor(
    body: ActAsDoctorRequest,
    doctor: Doctor = Depends(get_current_doctor),
    membership: DoctorClinic = Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Multi-doctor clinic: verify PIN and issue a doctor-scoped prescribe session.
    Not used in compounder flow (compounder uses verify-pin on approve only).
    """
    if membership.role == "compounder":
        raise HTTPException(
            status_code=403,
            detail="Compounder accounts cannot switch doctor sessions. Use PIN at approve.",
        )

    doctors = await _clinic_doctors(db, clinic_id)
    practice_mode = doctor.practice_mode or "solo"
    if len(doctors) <= 1 and practice_mode != "clinic":
        raise HTTPException(status_code=400, detail="Solo clinics do not require doctor selection.")

    target = await db.get(Doctor, body.doctor_id)
    if not target or not target.is_active:
        raise HTTPException(status_code=404, detail="Doctor not found.")

    linked = (
        await db.execute(
            select(DoctorClinic).where(
                DoctorClinic.doctor_id == target.id,
                DoctorClinic.clinic_id == clinic_id,
                DoctorClinic.is_active == True,
            )
        )
    ).scalar_one_or_none()
    if not linked:
        raise HTTPException(status_code=403, detail="Doctor does not belong to this clinic.")

    if not doctor_has_pin(target.pin_hash):
        raise HTTPException(status_code=422, detail="Doctor has not set an approval PIN yet.")

    if not verify_pin(body.pin, target.pin_hash):
        raise HTTPException(status_code=403, detail="Incorrect PIN.")

    return TokenResponse(
        access_token=create_access_token(target.id, clinic_id),
        refresh_token=create_refresh_token(target.id),
        token_type="bearer",
    )
