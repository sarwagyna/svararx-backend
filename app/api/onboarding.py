"""
Onboarding endpoints — guided doctor setup after signup.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.dependencies import get_current_doctor
from app.database import get_db
from app.models import Clinic, Doctor, DoctorClinic
from app.core.pin import doctor_has_pin, hash_pin, validate_pin_format
from app.schemas import (
    OnboardingSoloSetupRequest,
    OnboardingStatusResponse,
    OnboardingStep1Request,
    OnboardingStep2Request,
    OnboardingStepResponse,
    PracticeModeRequest,
)
from app.services.s3 import upload_bytes_to_s3

router = APIRouter()

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
ALLOWED_AUDIO_TYPES = {
    "audio/webm",
    "audio/ogg",
    "audio/mp4",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "application/octet-stream",
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_AUDIO_BYTES = 2 * 1024 * 1024


def _base_content_type(content_type: str) -> str:
    return (content_type or "").split(";")[0].strip().lower()


def _content_type_allowed(content_type: str, allowed: set[str]) -> bool:
    base = _base_content_type(content_type)
    if not base:
        return True  # browsers often omit type on FormData blobs
    return base in allowed


def _is_solo_mode(doctor: Doctor) -> bool:
    return (doctor.practice_mode or "solo") == "solo"


def _solo_clinic_display_name(doctor: Doctor) -> str:
    name = (doctor.name or "").strip() or "My Practice"
    return name if name.lower().startswith("dr") else f"Dr. {name}"


def _auto_solo_clinic_fields(doctor: Doctor) -> None:
    """Provision invisible solo clinic row from doctor profile."""
    if not doctor.clinic_name:
        doctor.clinic_name = _solo_clinic_display_name(doctor)
    if not doctor.clinic_address:
        doctor.clinic_address = doctor.clinic_city if doctor.clinic_city and doctor.clinic_city != "—" else "—"
    if not doctor.clinic_city:
        doctor.clinic_city = "—"
    if not doctor.clinic_pin:
        doctor.clinic_pin = "000000"
    if not doctor.clinic_state:
        doctor.clinic_state = "Andhra Pradesh"


def _status_from_doctor(doctor: Doctor) -> OnboardingStatusResponse:
    solo = _is_solo_mode(doctor)
    needs_practice_mode = (
        not doctor.onboarding_completed
        and doctor.onboarding_step < 1
        and (doctor.mci_number or "").startswith("PENDING-")
    )
    return OnboardingStatusResponse(
        step=doctor.onboarding_step,
        completed=doctor.onboarding_completed,
        practice_mode=doctor.practice_mode or "solo",
        is_solo_onboarding=solo,
        needs_practice_mode_choice=needs_practice_mode,
        full_name=doctor.name or "",
        qualifications=doctor.qualifications or "",
        mci_reg_number=doctor.mci_number or "",
        state_council_reg=doctor.state_council_reg or "",
        specialization=doctor.speciality or "",
        clinic_name=doctor.clinic_name or "",
        clinic_address=doctor.clinic_address or "",
        clinic_city=doctor.clinic_city or "",
        clinic_state=doctor.clinic_state or "Andhra Pradesh",
        clinic_pin=doctor.clinic_pin or "",
        clinic_phone=doctor.clinic_phone or "",
        clinic_logo_url=doctor.clinic_logo_url,
        signature_url=doctor.signature_url,
        referral_code=doctor.referred_by_doctor_id or "",
    )


async def _apply_referral_code(
    db: AsyncSession,
    doctor: Doctor,
    referral_code: str | None,
) -> None:
    """Link new doctor to referrer if a valid code is provided (set once)."""
    if doctor.referred_by_doctor_id:
        return
    code = (referral_code or "").strip()
    if not code or code == doctor.id:
        return

    referrer = await db.get(Doctor, code)
    if not referrer or not referrer.is_active:
        raise HTTPException(status_code=422, detail="Invalid referral code.")

    doctor.referred_by_doctor_id = referrer.id


async def _validate_mci_unique(
    db: AsyncSession,
    mci_number: str,
    doctor_id: str,
) -> None:
    """Ensure MCI is unique, reclaiming abandoned placeholder/orphan rows."""
    if mci_number.startswith("PENDING-"):
        return

    existing = (
        await db.execute(
            select(Doctor).where(
                Doctor.mci_number == mci_number,
                Doctor.id != doctor_id,
            )
        )
    ).scalar_one_or_none()
    if not existing:
        return

    # Reclaim MCI from abandoned placeholder or legacy rows (avoid hard delete — FKs)
    if (
        existing.mci_number.startswith("PENDING-")
        or existing.auth_user_id is None
        or not existing.is_active
    ):
        existing.mci_number = f"PENDING-RECLAIMED-{uuid.uuid4().hex[:8].upper()}"
        existing.is_active = False
        await db.flush()
        return

    raise HTTPException(status_code=409, detail="MCI number already registered.")


async def _read_upload(file: UploadFile | None, allowed: set[str], max_bytes: int) -> bytes | None:
    if file is None:
        return None
    content_type = file.content_type or ""
    if not _content_type_allowed(content_type, allowed):
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type: {content_type or 'unknown'}",
        )
    data = await file.read()
    if len(data) > max_bytes:
        raise HTTPException(status_code=422, detail="File too large.")
    if not data:
        return None
    return data


@router.get("/onboarding/status", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
    doctor: Doctor = Depends(get_current_doctor),
):
    return _status_from_doctor(doctor)


@router.post("/onboarding/practice-mode", response_model=OnboardingStepResponse)
async def save_practice_mode(
    body: PracticeModeRequest,
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
):
    """First step after email login — solo vs multi-doctor clinic."""
    if doctor.onboarding_completed:
        raise HTTPException(status_code=400, detail="Onboarding already completed.")
    doctor.practice_mode = body.practice_mode
    await db.commit()
    await db.refresh(doctor)
    return OnboardingStepResponse(step=doctor.onboarding_step, completed=doctor.onboarding_completed)


@router.post("/onboarding/step1", response_model=OnboardingStepResponse)
async def save_onboarding_step1(
    body: OnboardingStep1Request,
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
):
    mci = body.mci_reg_number.strip()
    if mci != doctor.mci_number:
        await _validate_mci_unique(db, mci, doctor.id)

    doctor.name = body.full_name.strip()
    doctor.qualifications = body.qualifications.strip()
    doctor.mci_number = mci
    doctor.state_council_reg = body.state_council_reg.strip() or None
    doctor.speciality = body.specialization.strip()
    doctor.practice_mode = body.practice_mode
    doctor.onboarding_step = max(doctor.onboarding_step, 1)

    await _apply_referral_code(db, doctor, body.referral_code)

    await db.commit()
    await db.refresh(doctor)
    return OnboardingStepResponse(step=doctor.onboarding_step, completed=doctor.onboarding_completed)


@router.post("/onboarding/solo-setup", response_model=OnboardingStepResponse)
async def save_solo_setup(
    body: OnboardingSoloSetupRequest,
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
):
    if doctor.onboarding_step < 1:
        raise HTTPException(status_code=400, detail="Complete step 1 first.")
    if not _is_solo_mode(doctor):
        raise HTTPException(status_code=400, detail="Solo setup is only for solo practice mode.")

    city = body.practice_city.strip()
    if city:
        doctor.clinic_city = city
    phone = body.practice_phone.strip()
    if phone:
        doctor.clinic_phone = phone

    _auto_solo_clinic_fields(doctor)

    pin = body.approval_pin.strip()
    if pin:
        try:
            doctor.pin_hash = hash_pin(validate_pin_format(pin))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    doctor.onboarding_step = max(doctor.onboarding_step, 2)
    await db.commit()
    await db.refresh(doctor)
    return OnboardingStepResponse(step=doctor.onboarding_step, completed=doctor.onboarding_completed)


@router.post("/onboarding/step2", response_model=OnboardingStepResponse)
async def save_onboarding_step2(
    body: OnboardingStep2Request,
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
):
    if doctor.onboarding_step < 1:
        raise HTTPException(status_code=400, detail="Complete step 1 first.")
    if _is_solo_mode(doctor):
        raise HTTPException(
            status_code=400,
            detail="Solo doctors use /onboarding/solo-setup instead of clinic details.",
        )

    doctor.clinic_name = body.clinic_name.strip()
    doctor.clinic_address = body.clinic_address.strip()
    doctor.clinic_city = body.clinic_city.strip()
    doctor.clinic_state = body.clinic_state.strip() or "Andhra Pradesh"
    doctor.clinic_pin = body.clinic_pin.strip()
    doctor.clinic_phone = body.clinic_phone.strip() or None
    doctor.onboarding_step = max(doctor.onboarding_step, 2)

    await db.commit()
    await db.refresh(doctor)
    return OnboardingStepResponse(step=doctor.onboarding_step, completed=doctor.onboarding_completed)


@router.post("/onboarding/step3", response_model=OnboardingStepResponse)
async def save_onboarding_step3(
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clinic_logo: UploadFile | None = File(None),
    signature: UploadFile | None = File(None),
):
    if doctor.onboarding_step < 2:
        if doctor.clinic_name and doctor.clinic_address and doctor.clinic_city and doctor.clinic_pin:
            doctor.onboarding_step = 2
        else:
            raise HTTPException(status_code=400, detail="Complete step 2 first.")

    logo_bytes = await _read_upload(clinic_logo, ALLOWED_IMAGE_TYPES, MAX_IMAGE_BYTES)
    sig_bytes = await _read_upload(signature, ALLOWED_IMAGE_TYPES, MAX_IMAGE_BYTES)

    if logo_bytes:
        ext = "png" if (clinic_logo.content_type or "").endswith("png") else "jpg"
        key = f"onboarding/{doctor.id}/logo.{ext}"
        try:
            doctor.clinic_logo_url = await upload_bytes_to_s3(
                logo_bytes, key, settings, content_type=clinic_logo.content_type or "image/png"
            )
        except ValueError:
            pass  # S3 not configured — skip upload

    if sig_bytes:
        ext = "png" if (signature.content_type or "").endswith("png") else "jpg"
        key = f"onboarding/{doctor.id}/signature.{ext}"
        try:
            doctor.signature_url = await upload_bytes_to_s3(
                sig_bytes, key, settings, content_type=signature.content_type or "image/png"
            )
        except ValueError:
            pass

    doctor.onboarding_step = max(doctor.onboarding_step, 3)
    await db.commit()
    await db.refresh(doctor)
    return OnboardingStepResponse(step=doctor.onboarding_step, completed=doctor.onboarding_completed)


@router.post("/onboarding/voice-calibration", response_model=OnboardingStepResponse)
async def save_voice_calibration(
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    audio: UploadFile | None = File(None),
):
    if doctor.onboarding_step < 3:
        raise HTTPException(status_code=400, detail="Complete step 3 first.")

    audio_bytes = await _read_upload(audio, ALLOWED_AUDIO_TYPES, MAX_AUDIO_BYTES)
    if audio_bytes:
        key = f"onboarding/{doctor.id}/voice-calibration-{uuid.uuid4().hex[:8]}.webm"
        try:
            await upload_bytes_to_s3(
                audio_bytes,
                key,
                settings,
                content_type=_base_content_type(audio.content_type or "") or "audio/webm",
            )
            doctor.voice_calibration_s3_key = key
        except ValueError:
            pass  # S3 not configured — still advance step

    doctor.onboarding_step = max(doctor.onboarding_step, 4)
    await db.commit()
    await db.refresh(doctor)
    return OnboardingStepResponse(step=doctor.onboarding_step, completed=doctor.onboarding_completed)


@router.post("/onboarding/complete", response_model=OnboardingStepResponse)
async def complete_onboarding(
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
):
    solo = _is_solo_mode(doctor)
    if solo:
        if doctor.onboarding_step < 2:
            raise HTTPException(status_code=400, detail="Complete solo setup first.")
        _auto_solo_clinic_fields(doctor)
    else:
        if doctor.onboarding_step < 3:
            raise HTTPException(status_code=400, detail="Complete all required steps first.")
        if not doctor_has_pin(doctor.pin_hash):
            raise HTTPException(
                status_code=400,
                detail="Set your 4-digit approval PIN before completing setup.",
            )
        if not doctor.clinic_name or not doctor.clinic_address:
            raise HTTPException(status_code=400, detail="Clinic details are incomplete.")

    membership = (
        await db.execute(
            select(DoctorClinic)
            .where(DoctorClinic.doctor_id == doctor.id, DoctorClinic.is_active == True)
            .limit(1)
        )
    ).scalar_one_or_none()

    if not membership:
        clinic = Clinic(
            name=doctor.clinic_name,
            address_line1=doctor.clinic_address,
            city=doctor.clinic_city or "",
            state=doctor.clinic_state or "Andhra Pradesh",
            pincode=doctor.clinic_pin or "000000",
            phone=doctor.clinic_phone,
            plan="free",
            is_active=True,
        )
        if doctor.clinic_logo_url:
            clinic.letterhead_type = "uploaded"
        db.add(clinic)
        await db.flush()

        db.add(
            DoctorClinic(
                doctor_id=doctor.id,
                clinic_id=clinic.id,
                role="admin",
            )
        )

    doctor.onboarding_completed = True
    doctor.onboarding_step = max(doctor.onboarding_step, 4)
    await db.commit()
    await db.refresh(doctor)
    return OnboardingStepResponse(step=doctor.onboarding_step, completed=doctor.onboarding_completed)
