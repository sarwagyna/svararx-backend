"""
POST /api/v1/auth/register — create Doctor profile after Supabase signup
GET  /api/v1/auth/me       — return current doctor info
POST /api/v1/auth/token    — exchange Supabase JWT for SvaraRx API tokens
"""
import uuid

from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Clinic, Doctor, DoctorClinic
from app.schemas import (
    ClinicMembershipOut,
    DoctorRegisterRequest,
    DoctorRegisterResponse,
    SwitchClinicRequest,
    TokenResponse,
)
from app.auth import get_supabase_user
from app.core.dependencies import get_current_doctor
from app.core.security import create_access_token, create_refresh_token, decode_token, WWW_AUTHENTICATE_HEADER

router = APIRouter()
_bearer = HTTPBearer()


def _doctor_response(doctor: Doctor, membership: DoctorClinic | None) -> DoctorRegisterResponse:
    return DoctorRegisterResponse(
        id=doctor.id,
        name=doctor.name,
        qualifications=doctor.qualifications,
        mci_number=doctor.mci_number,
        speciality=doctor.speciality,
        clinic_id=membership.clinic_id if membership else None,
        clinic_role=membership.role if membership else None,
        onboarding_step=doctor.onboarding_step,
        onboarding_completed=doctor.onboarding_completed,
    )


async def _get_membership(db: AsyncSession, doctor_id: str) -> DoctorClinic | None:
    return (
        await db.execute(
            select(DoctorClinic)
            .where(DoctorClinic.doctor_id == doctor_id, DoctorClinic.is_active == True)
            .limit(1)
        )
    ).scalar_one_or_none()


async def _issue_tokens(db: AsyncSession, doctor: Doctor) -> TokenResponse:
    membership = await _get_membership(db, doctor.id)
    if membership and doctor.onboarding_completed:
        access = create_access_token(doctor.id, membership.clinic_id)
    else:
        access = create_access_token(doctor.id, onboarding=True)
    return TokenResponse(
        access_token=access,
        refresh_token=create_refresh_token(doctor.id),
        token_type="bearer",
    )


async def _ensure_doctor(db: AsyncSession, auth_uid: str, user: dict) -> Doctor:
    doctor = (
        await db.execute(select(Doctor).where(Doctor.auth_user_id == auth_uid, Doctor.is_active == True))
    ).scalar_one_or_none()
    if doctor:
        return doctor

    meta = user.get("user_metadata") or {}
    meta_mci = (meta.get("mci_number") or "").strip()

    # Re-link legacy doctor row created before auth_user_id was wired up
    if meta_mci and not meta_mci.startswith("PENDING-"):
        legacy = (
            await db.execute(
                select(Doctor).where(
                    Doctor.mci_number == meta_mci,
                    Doctor.auth_user_id.is_(None),
                    Doctor.is_active == True,
                )
            )
        ).scalar_one_or_none()
        if legacy:
            legacy.auth_user_id = auth_uid
            await db.commit()
            await db.refresh(legacy)
            return legacy

    temp_mci = f"PENDING-{uuid.uuid4().hex[:12].upper()}"
    doctor = Doctor(
        auth_user_id=auth_uid,
        name=meta.get("name") or "Doctor",
        qualifications=meta.get("qualifications") or "",
        mci_number=temp_mci,
        speciality="General Physician",
        pin_hash="",
        onboarding_step=0,
        onboarding_completed=False,
    )
    db.add(doctor)
    await db.commit()
    await db.refresh(doctor)
    return doctor


@router.post(
    "/auth/token",
    response_model=TokenResponse,
    summary="Exchange Supabase session JWT for SvaraRx API tokens",
)
async def exchange_token(
    user: dict = Depends(get_supabase_user),
    db: AsyncSession = Depends(get_db),
):
    """Issue HS256 access + refresh tokens after Supabase login."""
    auth_uid = user["sub"]
    doctor = await _ensure_doctor(db, auth_uid, user)
    return await _issue_tokens(db, doctor)


@router.post(
    "/auth/refresh",
    response_model=TokenResponse,
    summary="Refresh an expired access token",
)
async def refresh_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
):
    payload = decode_token(credentials.credentials, expected_type="refresh")
    doctor_id = payload["sub"]

    doctor = (
        await db.execute(select(Doctor).where(Doctor.id == doctor_id, Doctor.is_active == True))
    ).scalar_one_or_none()
    if not doctor:
        raise HTTPException(
            status_code=401,
            detail="Doctor account not found.",
            headers=WWW_AUTHENTICATE_HEADER,
        )

    return await _issue_tokens(db, doctor)


@router.post(
    "/auth/register",
    response_model=DoctorRegisterResponse,
    status_code=201,
    summary="Create Doctor profile for the authenticated Supabase user",
)
async def register_doctor(
    body: DoctorRegisterRequest,
    user: dict = Depends(get_supabase_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Called once after the doctor confirms their email and first logs in.
    Idempotent: returns the existing profile if already registered.
    Clinic is optional — full setup happens via onboarding.
    """
    auth_uid = user["sub"]

    existing = (
        await db.execute(select(Doctor).where(Doctor.auth_user_id == auth_uid))
    ).scalar_one_or_none()
    if existing:
        membership = await _get_membership(db, existing.id)
        return _doctor_response(existing, membership)

    mci = body.mci_number.strip()
    if mci and not mci.startswith("PENDING-"):
        if (
            await db.execute(select(Doctor).where(Doctor.mci_number == mci))
        ).scalar_one_or_none():
            raise HTTPException(status_code=409, detail="MCI number already registered.")

    doctor = Doctor(
        auth_user_id=auth_uid,
        name=body.name.strip() if body.name else "Doctor",
        qualifications=body.qualifications.strip() if body.qualifications else "",
        mci_number=mci or f"PENDING-{uuid.uuid4().hex[:12].upper()}",
        speciality=body.speciality or "General Physician",
        pin_hash="",
        onboarding_step=0,
        onboarding_completed=False,
    )
    db.add(doctor)
    await db.flush()

    clinic_id = None
    role = "doctor"

    if body.clinic_name:
        if not body.clinic_address_line1 or not body.clinic_city or not body.clinic_state or not body.clinic_pincode:
            raise HTTPException(status_code=422, detail="Incomplete clinic details.")

        clinic = Clinic(
            name=body.clinic_name.strip(),
            address_line1=body.clinic_address_line1.strip(),
            address_line2=body.clinic_address_line2.strip() if body.clinic_address_line2 else None,
            city=body.clinic_city.strip(),
            state=body.clinic_state.strip(),
            pincode=body.clinic_pincode.strip(),
            phone=body.clinic_phone.strip() if body.clinic_phone else None,
            plan="free",
            is_active=True,
        )
        db.add(clinic)
        await db.flush()
        clinic_id = clinic.id
        role = "admin"

        doctor.clinic_name = body.clinic_name.strip()
        doctor.clinic_address = body.clinic_address_line1.strip()
        doctor.clinic_city = body.clinic_city.strip()
        doctor.clinic_state = body.clinic_state.strip()
        doctor.clinic_pin = body.clinic_pincode.strip()
        doctor.clinic_phone = body.clinic_phone.strip() if body.clinic_phone else None

        db.add(
            DoctorClinic(
                doctor_id=doctor.id,
                clinic_id=clinic_id,
                role=role,
            )
        )

    await db.commit()
    await db.refresh(doctor)

    membership = await _get_membership(db, doctor.id)
    return _doctor_response(doctor, membership)


@router.get(
    "/auth/me",
    response_model=DoctorRegisterResponse,
    summary="Return the current doctor's profile",
)
async def get_me(
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
):
    membership = await _get_membership(db, doctor.id)
    return _doctor_response(doctor, membership)


@router.get(
    "/auth/clinics",
    response_model=list[ClinicMembershipOut],
    summary="List clinics the current doctor belongs to",
)
async def list_doctor_clinics(
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(DoctorClinic, Clinic)
            .join(Clinic, DoctorClinic.clinic_id == Clinic.id)
            .where(DoctorClinic.doctor_id == doctor.id, DoctorClinic.is_active == True)
            .order_by(Clinic.name)
        )
    ).all()
    return [
        ClinicMembershipOut(clinic_id=clinic.id, clinic_name=clinic.name, role=membership.role)
        for membership, clinic in rows
    ]


@router.post(
    "/auth/switch-clinic",
    response_model=TokenResponse,
    summary="Issue new tokens scoped to a different clinic",
)
async def switch_clinic(
    body: SwitchClinicRequest,
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
):
    membership = (
        await db.execute(
            select(DoctorClinic).where(
                DoctorClinic.doctor_id == doctor.id,
                DoctorClinic.clinic_id == body.clinic_id,
                DoctorClinic.is_active == True,
            )
        )
    ).scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=403, detail="You do not have access to this clinic.")
    if not doctor.onboarding_completed:
        raise HTTPException(status_code=403, detail="Complete onboarding before switching clinics.")
    return TokenResponse(
        access_token=create_access_token(doctor.id, membership.clinic_id),
        refresh_token=create_refresh_token(doctor.id),
        token_type="bearer",
    )
