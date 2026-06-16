"""
Doctor profile settings — GET/PUT /doctors/me, signature & logo uploads.
"""
from __future__ import annotations

import base64
import re
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_doctor_clinic_id, get_doctor_membership
from app.config import Settings, get_settings
from app.core.dependencies import get_current_doctor
from app.core.pin import doctor_has_pin, hash_pin, validate_pin_format
from app.database import get_db
from app.models import Clinic, Doctor, DoctorClinic
from app.schemas import (
    DoctorMeResponse,
    DoctorMeUpdateRequest,
    LetterheadPreviewResponse,
    LetterheadResponse,
    LetterheadUpdateRequest,
    ReferralStatsResponse,
    UpgradeToClinicRequest,
    UploadUrlResponse,
)
from app.services.image_processing import process_letterhead_logo, process_logo_png, process_signature_png
from app.services.pdf_generator import generate_sample_prescription_pdf
from app.services.pdf_template_cache import invalidate_letterhead_cache
from app.services.s3 import upload_bytes_to_s3

router = APIRouter()

REFERRAL_REWARD_INR = 500
PAID_SUBSCRIPTION_TIERS = frozenset({"solo", "clinic", "opd", "annual_solo"})

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024

MCI_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/\-\.]{3,49}$")
PHONE_DIGITS = re.compile(r"^\d{10}$")


def _normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if not PHONE_DIGITS.match(digits):
        raise HTTPException(status_code=422, detail="Phone must be a 10-digit Indian mobile number.")
    return digits


def _validate_mci(mci: str) -> str:
    cleaned = mci.strip()
    if cleaned.startswith("PENDING-"):
        return cleaned
    if not MCI_PATTERN.match(cleaned):
        raise HTTPException(
            status_code=422,
            detail="MCI registration number format looks invalid. Use letters, numbers, /, - or .",
        )
    return cleaned


def _doctor_to_response(doctor: Doctor) -> DoctorMeResponse:
    return DoctorMeResponse(
        id=doctor.id,
        full_name=doctor.name,
        qualifications=doctor.qualifications,
        mci_reg_number=doctor.mci_number,
        state_council_reg=doctor.state_council_reg,
        specialization=doctor.speciality,
        languages=list(doctor.languages or ["Telugu", "English"]),
        clinic_name=doctor.clinic_name,
        clinic_address=doctor.clinic_address,
        clinic_address_line2=doctor.clinic_address_line2,
        clinic_city=doctor.clinic_city,
        clinic_state=doctor.clinic_state,
        clinic_pin=doctor.clinic_pin,
        clinic_phone=doctor.clinic_phone,
        clinic_logo_url=doctor.clinic_logo_url,
        signature_url=doctor.signature_url,
        onboarding_completed=bool(doctor.onboarding_completed),
        onboarding_step=doctor.onboarding_step or 0,
        subscription_tier=doctor.subscription_tier or "free",
        subscription_expires_at=doctor.subscription_expires_at,
        practice_mode=doctor.practice_mode or "solo",
        has_approval_pin=doctor_has_pin(doctor.pin_hash),
    )


async def _validate_mci_unique(db: AsyncSession, mci_number: str, doctor_id: str) -> None:
    from sqlalchemy import select

    if mci_number.startswith("PENDING-"):
        return

    existing = (
        await db.execute(
            select(Doctor).where(Doctor.mci_number == mci_number, Doctor.id != doctor_id)
        )
    ).scalar_one_or_none()
    if not existing:
        return

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


async def _read_image_upload(file: UploadFile) -> bytes:
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type and content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=422, detail=f"Unsupported file type: {content_type}")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="Empty file.")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=422, detail="File too large (max 5 MB).")
    return data


@router.get("/doctors/me", response_model=DoctorMeResponse)
async def get_doctor_me(doctor: Doctor = Depends(get_current_doctor)):
    return _doctor_to_response(doctor)


@router.put("/doctors/me", response_model=DoctorMeResponse)
async def update_doctor_me(
    body: DoctorMeUpdateRequest,
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return _doctor_to_response(doctor)

    if "full_name" in updates and updates["full_name"] is not None:
        doctor.name = updates["full_name"].strip()
    if "qualifications" in updates and updates["qualifications"] is not None:
        doctor.qualifications = updates["qualifications"].strip()
    if "mci_reg_number" in updates and updates["mci_reg_number"] is not None:
        mci = _validate_mci(updates["mci_reg_number"])
        if mci != doctor.mci_number:
            await _validate_mci_unique(db, mci, doctor.id)
        doctor.mci_number = mci
    if "state_council_reg" in updates:
        val = updates["state_council_reg"]
        doctor.state_council_reg = val.strip() if val else None
    if "specialization" in updates and updates["specialization"] is not None:
        doctor.speciality = updates["specialization"].strip()
    if "languages" in updates and updates["languages"] is not None:
        langs = [lang.strip() for lang in updates["languages"] if lang.strip()]
        if not langs:
            raise HTTPException(status_code=422, detail="At least one language is required.")
        doctor.languages = langs
    if "clinic_name" in updates and updates["clinic_name"] is not None:
        doctor.clinic_name = updates["clinic_name"].strip()
    if "clinic_address" in updates and updates["clinic_address"] is not None:
        doctor.clinic_address = updates["clinic_address"].strip()
    if "clinic_city" in updates and updates["clinic_city"] is not None:
        doctor.clinic_city = updates["clinic_city"].strip()
    if "clinic_state" in updates and updates["clinic_state"] is not None:
        doctor.clinic_state = updates["clinic_state"].strip() or "Andhra Pradesh"
    if "clinic_pin" in updates and updates["clinic_pin"] is not None:
        doctor.clinic_pin = updates["clinic_pin"].strip()
    if "clinic_phone" in updates:
        doctor.clinic_phone = _normalize_phone(updates["clinic_phone"])

    await db.commit()
    await db.refresh(doctor)
    return _doctor_to_response(doctor)


@router.post("/doctors/me/upgrade-to-clinic", response_model=DoctorMeResponse)
async def upgrade_to_clinic(
    body: UpgradeToClinicRequest,
    doctor: Doctor = Depends(get_current_doctor),
    membership: DoctorClinic = Depends(get_doctor_membership),
    db: AsyncSession = Depends(get_db),
):
    """Switch a solo doctor to multi-doctor clinic mode (settings upgrade)."""
    if (doctor.practice_mode or "solo") == "clinic":
        raise HTTPException(status_code=400, detail="Already on multi-doctor clinic mode.")
    if membership.role != "admin":
        raise HTTPException(status_code=403, detail="Only clinic admins can upgrade practice mode.")

    doctor.clinic_name = body.clinic_name.strip()
    doctor.clinic_address = body.clinic_address.strip()
    doctor.clinic_city = body.clinic_city.strip()
    doctor.clinic_state = body.clinic_state.strip() or "Andhra Pradesh"
    doctor.clinic_pin = body.clinic_pin.strip()
    doctor.clinic_phone = _normalize_phone(body.clinic_phone) if body.clinic_phone.strip() else None

    pin_input = body.approval_pin.strip()
    if pin_input:
        try:
            doctor.pin_hash = hash_pin(validate_pin_format(pin_input))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif not doctor_has_pin(doctor.pin_hash):
        raise HTTPException(
            status_code=422,
            detail=(
                "Set a 4-digit approval PIN. It is used to open doctor workspaces from the "
                "clinic dashboard and to let staff approve prescriptions on a doctor's behalf."
            ),
        )

    doctor.practice_mode = "clinic"
    await _sync_clinic_record(db, doctor)
    invalidate_letterhead_cache(doctor.id)
    await db.commit()
    await db.refresh(doctor)
    return _doctor_to_response(doctor)


@router.post("/doctors/me/signature", response_model=UploadUrlResponse)
async def upload_signature(
    file: UploadFile = File(...),
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    raw = await _read_image_upload(file)
    try:
        png_bytes = process_signature_png(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not process image: {exc}") from exc

    key = f"doctors/{doctor.id}/signature-{uuid.uuid4().hex[:8]}.png"
    try:
        url = await upload_bytes_to_s3(png_bytes, key, settings, content_type="image/png")
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="File storage not configured.") from exc

    doctor.signature_url = url
    await db.commit()
    return UploadUrlResponse(signature_url=url)


@router.post("/doctors/me/logo", response_model=UploadUrlResponse)
async def upload_clinic_logo(
    file: UploadFile = File(...),
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    raw = await _read_image_upload(file)
    try:
        png_bytes = process_logo_png(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not process image: {exc}") from exc

    key = f"doctors/{doctor.id}/logo-{uuid.uuid4().hex[:8]}.png"
    try:
        url = await upload_bytes_to_s3(png_bytes, key, settings, content_type="image/png")
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="File storage not configured.") from exc

    doctor.clinic_logo_url = url
    await db.commit()
    return UploadUrlResponse(clinic_logo_url=url)


def _letterhead_from_doctor(doctor: Doctor) -> LetterheadResponse:
    return LetterheadResponse(
        clinic_name=doctor.clinic_name or "",
        clinic_address=doctor.clinic_address or "",
        clinic_address_line2=doctor.clinic_address_line2 or "",
        clinic_city=doctor.clinic_city or "",
        clinic_state=doctor.clinic_state or "Andhra Pradesh",
        clinic_pin=doctor.clinic_pin or "",
        clinic_phone=doctor.clinic_phone or "",
        clinic_logo_url=doctor.clinic_logo_url,
        doctor_name=doctor.name,
        qualifications=doctor.qualifications,
        mci_reg_number=doctor.mci_number,
        state_council_reg=doctor.state_council_reg,
        signature_url=doctor.signature_url,
    )


def _clinic_snapshot_from_doctor(doctor: Doctor, clinic_id: str = "preview") -> Clinic:
    return Clinic(
        id=clinic_id,
        name=doctor.clinic_name or "Your Clinic",
        address_line1=doctor.clinic_address or "",
        address_line2=doctor.clinic_address_line2,
        city=doctor.clinic_city or "",
        state=doctor.clinic_state or "Andhra Pradesh",
        pincode=doctor.clinic_pin or "000000",
        phone=doctor.clinic_phone,
    )


async def _sync_clinic_record(db: AsyncSession, doctor: Doctor) -> None:
    membership = (
        await db.execute(
            select(DoctorClinic).where(
                DoctorClinic.doctor_id == doctor.id,
                DoctorClinic.is_active == True,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if not membership:
        return
    clinic = await db.get(Clinic, membership.clinic_id)
    if not clinic:
        return
    clinic.name = doctor.clinic_name or clinic.name
    clinic.address_line1 = doctor.clinic_address or clinic.address_line1
    clinic.address_line2 = doctor.clinic_address_line2
    clinic.city = doctor.clinic_city or clinic.city
    clinic.state = doctor.clinic_state or clinic.state
    clinic.pincode = doctor.clinic_pin or clinic.pincode
    clinic.phone = doctor.clinic_phone
    if doctor.clinic_logo_url:
        clinic.letterhead_type = "uploaded"


@router.get("/doctors/me/letterhead", response_model=LetterheadResponse)
async def get_letterhead(doctor: Doctor = Depends(get_current_doctor)):
    return _letterhead_from_doctor(doctor)


@router.put("/doctors/me/letterhead", response_model=LetterheadResponse)
async def update_letterhead(
    body: LetterheadUpdateRequest,
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
):
    phone = _normalize_phone(body.clinic_phone) if body.clinic_phone.strip() else None

    doctor.clinic_name = body.clinic_name.strip()
    doctor.clinic_address = body.clinic_address.strip()
    doctor.clinic_address_line2 = body.clinic_address_line2.strip() or None
    doctor.clinic_city = body.clinic_city.strip()
    doctor.clinic_state = body.clinic_state.strip() or "Andhra Pradesh"
    doctor.clinic_pin = body.clinic_pin.strip()
    doctor.clinic_phone = phone

    await _sync_clinic_record(db, doctor)
    invalidate_letterhead_cache(doctor.id)
    await db.commit()
    await db.refresh(doctor)
    return _letterhead_from_doctor(doctor)


@router.post("/doctors/me/letterhead/logo", response_model=UploadUrlResponse)
async def upload_letterhead_logo(
    file: UploadFile = File(...),
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    raw = await _read_image_upload(file)
    try:
        png_bytes = process_letterhead_logo(raw, max_width=300)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not process image: {exc}") from exc

    key = f"doctors/{doctor.id}/letterhead-logo-{uuid.uuid4().hex[:8]}.png"
    try:
        url = await upload_bytes_to_s3(png_bytes, key, settings, content_type="image/png")
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="File storage not configured.") from exc

    doctor.clinic_logo_url = url
    await _sync_clinic_record(db, doctor)
    invalidate_letterhead_cache(doctor.id)
    await db.commit()
    return UploadUrlResponse(clinic_logo_url=url)


@router.get("/doctors/me/referrals", response_model=ReferralStatsResponse)
async def get_referral_stats(
    doctor: Doctor = Depends(get_current_doctor),
    db: AsyncSession = Depends(get_db),
):
    """Return referral sign-ups and earned credit for the logged-in doctor."""
    base_filter = (
        Doctor.referred_by_doctor_id == doctor.id,
        Doctor.is_active == True,
    )
    total_referrals = int(
        await db.scalar(select(func.count()).select_from(Doctor).where(*base_filter)) or 0
    )
    paid_referrals = int(
        await db.scalar(
            select(func.count())
            .select_from(Doctor)
            .where(
                *base_filter,
                func.lower(Doctor.subscription_tier).in_(PAID_SUBSCRIPTION_TIERS),
            )
        )
        or 0
    )
    pending_referrals = max(total_referrals - paid_referrals, 0)
    return ReferralStatsResponse(
        total_referrals=total_referrals,
        paid_referrals=paid_referrals,
        pending_referrals=pending_referrals,
        earnings_inr=paid_referrals * REFERRAL_REWARD_INR,
        reward_per_referral_inr=REFERRAL_REWARD_INR,
    )


@router.post("/doctors/me/letterhead/preview", response_model=LetterheadPreviewResponse)
async def preview_letterhead_pdf(doctor: Doctor = Depends(get_current_doctor)):
    clinic = _clinic_snapshot_from_doctor(doctor, clinic_id=doctor.id)
    pdf_bytes = generate_sample_prescription_pdf(doctor, clinic)
    return LetterheadPreviewResponse(
        pdf_base64=base64.b64encode(pdf_bytes).decode("utf-8"),
    )
