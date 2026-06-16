"""
GET /api/v1/admin/overview — clinic, doctors, and system stats
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models import Clinic, Doctor, DoctorClinic, Patient, Prescription, Drug
from app.schemas import AdminOverview, ClinicInfo, DoctorInfo
from app.auth import get_doctor_clinic_id, require_clinic_admin

router = APIRouter()
_IST = ZoneInfo("Asia/Kolkata")


@router.get("/admin/overview", response_model=AdminOverview)
async def get_admin_overview(
    db: AsyncSession = Depends(get_db),
    _admin_membership = Depends(require_clinic_admin),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    clinic = await db.get(Clinic, clinic_id)
    if not clinic:
        raise HTTPException(status_code=404, detail="Clinic not found.")

    doctor_rows = (await db.execute(
        select(Doctor)
        .join(DoctorClinic, DoctorClinic.doctor_id == Doctor.id)
        .where(DoctorClinic.clinic_id == clinic_id)
        .order_by(Doctor.name)
    )).scalars().all()

    total_patients = await db.scalar(
        select(func.count()).select_from(Patient).where(
            Patient.clinic_id == clinic_id,
            Patient.is_active == True,
        )
    ) or 0

    total_rx = await db.scalar(
        select(func.count()).select_from(Prescription).where(
            Prescription.clinic_id == clinic_id
        )
    ) or 0

    total_drugs = await db.scalar(
        select(func.count()).select_from(Drug).where(Drug.is_active == True)
    ) or 0

    now_ist = datetime.now(_IST)
    month_start = now_ist.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    this_month_rx = await db.scalar(
        select(func.count()).select_from(Prescription).where(
            Prescription.clinic_id == clinic_id,
            Prescription.created_at >= month_start,
        )
    ) or 0

    return AdminOverview(
        clinic=ClinicInfo.model_validate(clinic),
        doctors=[DoctorInfo.model_validate(d) for d in doctor_rows],
        total_patients=total_patients,
        total_prescriptions=total_rx,
        total_drugs=total_drugs,
        prescriptions_this_month=this_month_rx,
    )
