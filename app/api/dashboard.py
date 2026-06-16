"""
GET /api/v1/dashboard — clinic summary stats + recent prescriptions + analytics
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models import Clinic, Doctor, DoctorClinic, Prescription, Patient, PatientAllergy
from app.schemas import (
    ClinicDashboardSummary,
    ClinicDoctorStats,
    DashboardAnalytics,
    DashboardData,
    DailyCount,
    PatientSexCount,
    RecentPrescription,
)
from app.auth import get_doctor_clinic_id, get_doctor_membership
from app.core.dependencies import get_current_doctor
from app.core.pin import doctor_has_pin

router = APIRouter()
_IST = ZoneInfo("Asia/Kolkata")


@router.get("/dashboard", response_model=DashboardData)
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    doctor=Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    now_ist = datetime.now(_IST)
    today_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    today_end = now_ist.replace(hour=23, minute=59, second=59, microsecond=999999).astimezone(timezone.utc)

    total_patients, total_rx, today_rx = await _fetch_counts(
        db, doctor.id, membership, clinic_id, today_start, today_end
    )
    recent = await _fetch_recent(db, doctor.id, membership, clinic_id)
    analytics = await _fetch_analytics(db, doctor.id, membership, clinic_id, now_ist)
    clinic_summary = await _fetch_clinic_summary(
        db, doctor, membership, clinic_id, now_ist, today_start, today_end
    )

    return DashboardData(
        total_patients=total_patients,
        total_prescriptions=total_rx,
        today_prescriptions=today_rx,
        recent_prescriptions=recent,
        analytics=analytics,
        clinic=clinic_summary,
    )


def _rx_filters(doctor_id, membership, clinic_id):
    filters = [Prescription.clinic_id == clinic_id]
    if membership.role != "admin":
        filters.append(Prescription.doctor_id == doctor_id)
    return filters


def _patient_filters(doctor_id, membership, clinic_id):
    filters = [Patient.clinic_id == clinic_id, Patient.is_active == True]
    if membership.role != "admin":
        filters.append(Patient.created_by_doctor_id == doctor_id)
    return filters


def _week_start_ist(dt: datetime) -> datetime:
    return (dt - timedelta(days=dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _sex_label(sex: str) -> str:
    if sex == "M":
        return "Male"
    if sex == "F":
        return "Female"
    return "Other"


def _build_daily_counts(chart_start_ist: datetime, timestamps) -> list[DailyCount]:
    day_buckets: dict = {}
    for i in range(7):
        day = (chart_start_ist + timedelta(days=i)).date()
        day_buckets[day] = 0

    for created_at in timestamps:
        ist_date = created_at.astimezone(_IST).date()
        if ist_date in day_buckets:
            day_buckets[ist_date] += 1

    return [
        DailyCount(
            date=day.isoformat(),
            label=day.strftime("%a"),
            count=day_buckets[day],
        )
        for day in sorted(day_buckets.keys())
    ]


async def _count_by_doctor(
    db: AsyncSession,
    clinic_id: str,
    *,
    extra_filters: list | None = None,
) -> dict[str, int]:
    filters = [Prescription.clinic_id == clinic_id]
    if extra_filters:
        filters.extend(extra_filters)
    rows = (
        await db.execute(
            select(Prescription.doctor_id, func.count())
            .where(*filters)
            .group_by(Prescription.doctor_id)
        )
    ).all()
    return {doctor_id: count for doctor_id, count in rows}


async def _patients_by_doctor(db: AsyncSession, clinic_id: str) -> dict[str, int]:
    rows = (
        await db.execute(
            select(Patient.created_by_doctor_id, func.count())
            .where(
                Patient.clinic_id == clinic_id,
                Patient.is_active == True,
                Patient.created_by_doctor_id.isnot(None),
            )
            .group_by(Patient.created_by_doctor_id)
        )
    ).all()
    return {doctor_id: count for doctor_id, count in rows}


async def _fetch_clinic_summary(
    db: AsyncSession,
    doctor: Doctor,
    membership: DoctorClinic,
    clinic_id: str,
    now_ist: datetime,
    today_start,
    today_end,
) -> ClinicDashboardSummary | None:
    """Clinic team overview — clinic-mode practices and multi-doctor clinics."""
    clinic = await db.get(Clinic, clinic_id)
    if not clinic:
        return None

    doctor_rows = (
        await db.execute(
            select(Doctor, DoctorClinic.role)
            .join(DoctorClinic, DoctorClinic.doctor_id == Doctor.id)
            .where(
                DoctorClinic.clinic_id == clinic_id,
                DoctorClinic.is_active == True,
                Doctor.is_active == True,
            )
            .order_by(Doctor.name)
        )
    ).all()

    if not doctor_rows:
        return None

    is_clinic_mode = (doctor.practice_mode or "solo") == "clinic"
    if len(doctor_rows) <= 1 and not is_clinic_mode:
        return None
    if membership.role not in ("admin", "compounder"):
        return None

    week_start_utc = _week_start_ist(now_ist).astimezone(timezone.utc)
    total_rx_map = await _count_by_doctor(db, clinic_id)
    today_rx_map = await _count_by_doctor(
        db,
        clinic_id,
        extra_filters=[
            Prescription.created_at >= today_start,
            Prescription.created_at <= today_end,
        ],
    )
    week_rx_map = await _count_by_doctor(
        db,
        clinic_id,
        extra_filters=[Prescription.created_at >= week_start_utc],
    )
    patients_map = await _patients_by_doctor(db, clinic_id)

    doctors = [
        ClinicDoctorStats(
            id=doc.id,
            name=doc.name,
            speciality=doc.speciality or "General Physician",
            role=role,
            has_pin=doctor_has_pin(doc.pin_hash),
            total_prescriptions=total_rx_map.get(doc.id, 0),
            today_prescriptions=today_rx_map.get(doc.id, 0),
            week_prescriptions=week_rx_map.get(doc.id, 0),
            total_patients=patients_map.get(doc.id, 0),
        )
        for doc, role in doctor_rows
    ]

    return ClinicDashboardSummary(
        clinic_id=clinic.id,
        clinic_name=clinic.name,
        plan=clinic.plan,
        doctor_count=len(doctors),
        practice_mode=doctor.practice_mode or "solo",
        doctors=doctors,
    )


async def _fetch_counts(db, doctor_id, membership, clinic_id, today_start, today_end):
    patient_filters = _patient_filters(doctor_id, membership, clinic_id)
    prescription_filters = _rx_filters(doctor_id, membership, clinic_id)
    today_filters = [
        *prescription_filters,
        Prescription.created_at >= today_start,
        Prescription.created_at <= today_end,
    ]

    total_patients = await db.scalar(
        select(func.count()).select_from(Patient).where(*patient_filters)
    )
    total_rx = await db.scalar(
        select(func.count()).select_from(Prescription).where(*prescription_filters)
    )
    today_rx = await db.scalar(
        select(func.count()).select_from(Prescription).where(*today_filters)
    )
    return total_patients or 0, total_rx or 0, today_rx or 0


async def _fetch_analytics(db, doctor_id, membership, clinic_id, now_ist: datetime) -> DashboardAnalytics:
    rx_filters = _rx_filters(doctor_id, membership, clinic_id)
    patient_filters = _patient_filters(doctor_id, membership, clinic_id)

    week_start_ist = _week_start_ist(now_ist)
    last_week_start_ist = week_start_ist - timedelta(days=7)
    chart_start_ist = (now_ist - timedelta(days=6)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    week_start_utc = week_start_ist.astimezone(timezone.utc)
    last_week_start_utc = last_week_start_ist.astimezone(timezone.utc)
    chart_start_utc = chart_start_ist.astimezone(timezone.utc)

    week_rx = await db.scalar(
        select(func.count())
        .select_from(Prescription)
        .where(*rx_filters, Prescription.created_at >= week_start_utc)
    )
    last_week_rx = await db.scalar(
        select(func.count())
        .select_from(Prescription)
        .where(
            *rx_filters,
            Prescription.created_at >= last_week_start_utc,
            Prescription.created_at < week_start_utc,
        )
    )
    new_patients_week = await db.scalar(
        select(func.count())
        .select_from(Patient)
        .where(*patient_filters, Patient.created_at >= week_start_utc)
    )
    last_week_new_patients = await db.scalar(
        select(func.count())
        .select_from(Patient)
        .where(
            *patient_filters,
            Patient.created_at >= last_week_start_utc,
            Patient.created_at < week_start_utc,
        )
    )
    total_active_patients = await db.scalar(
        select(func.count()).select_from(Patient).where(*patient_filters)
    )
    patients_visited_week = await db.scalar(
        select(func.count(func.distinct(Prescription.patient_id)))
        .select_from(Prescription)
        .where(
            *rx_filters,
            Prescription.created_at >= week_start_utc,
            Prescription.patient_id.isnot(None),
        )
    )
    patients_with_allergies = await db.scalar(
        select(func.count(func.distinct(PatientAllergy.patient_id)))
        .select_from(PatientAllergy)
        .join(Patient, PatientAllergy.patient_id == Patient.id)
        .where(*patient_filters)
    )

    chart_rx_rows = (
        await db.execute(
            select(Prescription.created_at).where(
                *rx_filters,
                Prescription.created_at >= chart_start_utc,
            )
        )
    ).scalars().all()

    chart_patient_rows = (
        await db.execute(
            select(Patient.created_at).where(
                *patient_filters,
                Patient.created_at >= chart_start_utc,
            )
        )
    ).scalars().all()

    rx_by_day = _build_daily_counts(chart_start_ist, chart_rx_rows)
    patients_by_day = _build_daily_counts(chart_start_ist, chart_patient_rows)

    sex_rows = (
        await db.execute(
            select(Patient.sex, func.count())
            .where(*patient_filters)
            .group_by(Patient.sex)
        )
    ).all()
    patient_sex_breakdown = [
        PatientSexCount(sex=sex, label=_sex_label(sex), count=count)
        for sex, count in sorted(sex_rows, key=lambda row: -row[1])
    ]

    all_rx_rows = (
        await db.execute(
            select(Prescription.status, Prescription.structured_json).where(*rx_filters)
        )
    ).all()

    draft_total = 0
    completed_total = 0
    med_lengths: list[int] = []
    for status, structured in all_rx_rows:
        if status == "draft":
            draft_total += 1
        else:
            completed_total += 1
        meds = (structured or {}).get("medications") or []
        if meds:
            med_lengths.append(len(meds))

    avg_meds = round(sum(med_lengths) / len(med_lengths), 1) if med_lengths else 0.0

    return DashboardAnalytics(
        rx_by_day=rx_by_day,
        patients_by_day=patients_by_day,
        week_prescriptions=week_rx or 0,
        last_week_prescriptions=last_week_rx or 0,
        new_patients_week=new_patients_week or 0,
        last_week_new_patients=last_week_new_patients or 0,
        patients_visited_week=patients_visited_week or 0,
        total_active_patients=total_active_patients or 0,
        patients_with_allergies=patients_with_allergies or 0,
        patient_sex_breakdown=patient_sex_breakdown,
        draft_prescriptions=draft_total,
        completed_prescriptions=completed_total,
        avg_medications_per_rx=avg_meds,
    )


async def _fetch_recent(db, doctor_id, membership, clinic_id) -> list[RecentPrescription]:
    include_doctor = membership.role == "admin"
    stmt = (
        select(Prescription, Patient.name, Patient.id, Doctor.name)
        .select_from(Prescription)
        .outerjoin(Patient, Prescription.patient_id == Patient.id)
        .outerjoin(Doctor, Prescription.doctor_id == Doctor.id)
        .where(Prescription.clinic_id == clinic_id)
    )
    if membership.role != "admin":
        stmt = stmt.where(Prescription.doctor_id == doctor_id)

    rows = (await db.execute(
        stmt.order_by(Prescription.created_at.desc()).limit(6)
    )).all()

    result = []
    for rx, patient_name, patient_id, prescribing_doctor_name in rows:
        structured = rx.structured_json or {}
        if rx.status == "draft" and not patient_name:
            display_name = "Draft prescription"
        else:
            display_name = patient_name or "Unknown patient"
        result.append(RecentPrescription(
            id=rx.id,
            patient_id=patient_id,
            patient_name=display_name,
            diagnosis=structured.get("diagnosis") or None,
            created_at=rx.created_at,
            approved_at=rx.approved_at,
            status=rx.status,
            item_count=len(structured.get("medications", [])),
            doctor_name=prescribing_doctor_name if include_doctor else None,
        ))
    return result
