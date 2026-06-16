"""
Multi-tenant context for clinic-scoped data isolation.

Each clinic is a tenant. Doctors belong to one or more clinics via DoctorClinic.
All patient/prescription/consultation data is scoped to a clinic_id.
"""
from __future__ import annotations

from dataclasses import dataclass

from pathlib import Path

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_access_token_payload, get_current_doctor
from app.database import get_db
from app.models import Doctor, DoctorClinic, Patient, Prescription


@dataclass(frozen=True)
class TenantContext:
    """Resolved tenant scope for the current authenticated request."""

    doctor: Doctor
    clinic_id: str
    membership: DoctorClinic

    @property
    def doctor_id(self) -> str:
        return self.doctor.id

    @property
    def is_admin(self) -> bool:
        return self.membership.role == "admin"

    def prescription_filters(self) -> list:
        """SQLAlchemy filters for prescriptions visible to this tenant."""
        filters = [Prescription.clinic_id == self.clinic_id]
        if not self.is_admin:
            filters.append(Prescription.doctor_id == self.doctor_id)
        return filters

    def patient_filters(self) -> list:
        """SQLAlchemy filters for patients visible to this tenant."""
        filters = [Patient.clinic_id == self.clinic_id, Patient.is_active == True]
        if not self.is_admin:
            filters.append(Patient.created_by_doctor_id == self.doctor_id)
        return filters


def redis_voice_key(doctor_id: str, recording_id: str) -> str:
    return f"voice:{doctor_id}:{recording_id}"


def redis_transcript_key(doctor_id: str, recording_id: str) -> str:
    return f"transcript:{doctor_id}:{recording_id}"


def legacy_redis_voice_key(recording_id: str) -> str:
    return f"voice:{recording_id}"


def legacy_redis_transcript_key(recording_id: str) -> str:
    return f"transcript:{recording_id}"


def s3_prescription_key(clinic_id: str, prescription_id: str) -> str:
    return f"clinics/{clinic_id}/prescriptions/{prescription_id}.pdf"


def s3_record_attachment_key(clinic_id: str, consultation_id: str, attachment_id: str, filename: str) -> str:
    safe = Path(filename).name.replace("..", "").strip() or "upload"
    return f"clinics/{clinic_id}/record-attachments/{consultation_id}/{attachment_id}_{safe}"


async def resolve_membership(
    db: AsyncSession,
    doctor_id: str,
    clinic_id: str | None,
    *,
    onboarding: bool = False,
) -> DoctorClinic:
    """Load and validate the doctor's active clinic membership."""
    if onboarding and not clinic_id:
        raise HTTPException(
            status_code=403,
            detail="Complete onboarding before accessing clinic data.",
        )

    stmt = select(DoctorClinic).where(
        DoctorClinic.doctor_id == doctor_id,
        DoctorClinic.is_active == True,
    )
    if clinic_id:
        stmt = stmt.where(DoctorClinic.clinic_id == clinic_id)
    else:
        stmt = stmt.limit(1)

    membership = (await db.execute(stmt)).scalar_one_or_none()
    if not membership:
        if clinic_id:
            raise HTTPException(
                status_code=403,
                detail="You do not have access to this clinic.",
            )
        raise HTTPException(
            status_code=403,
            detail="Doctor not associated with any clinic.",
        )
    return membership


async def get_tenant_context(
    doctor: Doctor = Depends(get_current_doctor),
    payload: dict = Depends(get_access_token_payload),
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    """
    Resolve the authenticated doctor's tenant scope from the JWT clinic_id claim.
    """
    membership = await resolve_membership(
        db,
        doctor.id,
        payload.get("clinic_id"),
        onboarding=bool(payload.get("onboarding")),
    )
    return TenantContext(
        doctor=doctor,
        clinic_id=membership.clinic_id,
        membership=membership,
    )
