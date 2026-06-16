"""
Patient CRUD, search, and recent list.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_doctor_clinic_id, get_doctor_membership
from app.core.dependencies import get_current_doctor
from app.database import get_db
from app.models import Doctor, Patient, Prescription, PatientAllergy
from app.schemas import (
    PatientCreate,
    PatientOut,
    PatientRecentOut,
    PatientSearchOut,
    PatientUpdate,
    PatientListItem,
    PaginatedPatientList,
)

router = APIRouter()

PHONE_DIGITS = re.compile(r"^\d{10}$")
ABHA_PATTERN = re.compile(r"^[\d]{2}-[\d]{4}-[\d]{4}-[\d]{4}$", re.IGNORECASE)


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw.strip())
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if not PHONE_DIGITS.match(digits):
        raise HTTPException(status_code=422, detail="Phone must be a 10-digit mobile number.")
    return digits


def _normalize_sex(sex: str) -> str:
    s = sex.strip().upper()
    if s == "OTHER":
        return "O"
    return s


def _patient_scope(doctor: Doctor, membership, clinic_id: str):
    stmt = select(Patient).where(Patient.clinic_id == clinic_id, Patient.is_active == True)
    if membership.role != "admin":
        stmt = stmt.where(Patient.created_by_doctor_id == doctor.id)
    return stmt


def _prescription_stats_subquery(doctor_id: str):
    return (
        select(
            Prescription.patient_id.label("patient_id"),
            func.max(Prescription.created_at).label("last_visit_date"),
            func.count(Prescription.id).label("prescription_count"),
        )
        .where(
            Prescription.doctor_id == doctor_id,
            Prescription.patient_id.isnot(None),
        )
        .group_by(Prescription.patient_id)
        .subquery()
    )


def _to_search_out(patient: Patient, last_visit, rx_count: int) -> PatientSearchOut:
    return PatientSearchOut(
        id=patient.id,
        full_name=patient.name,
        age=patient.age,
        gender=patient.sex,
        phone=patient.phone,
        last_visit_date=last_visit,
        prescription_count=rx_count or 0,
    )


async def _fetch_search_results(
    db: AsyncSession,
    doctor: Doctor,
    stmt,
    limit: int,
) -> list[PatientSearchOut]:
    patients = (await db.execute(stmt.limit(limit))).scalars().all()
    if not patients:
        return []

    stats = _prescription_stats_subquery(doctor.id)
    patient_ids = [p.id for p in patients]
    rows = (
        await db.execute(
            select(
                Patient,
                stats.c.last_visit_date,
                func.coalesce(stats.c.prescription_count, 0),
            )
            .outerjoin(stats, Patient.id == stats.c.patient_id)
            .where(Patient.id.in_(patient_ids))
        )
    ).all()
    by_id = {p.id: (p, lv, int(cnt)) for p, lv, cnt in rows}
    return [_to_search_out(*by_id[pid]) for pid in patient_ids if pid in by_id]


async def _phone_conflict(
    db: AsyncSession,
    doctor_id: str,
    phone: str,
    exclude_patient_id: str | None = None,
) -> None:
    if not phone:
        return
    stmt = select(Patient).where(
        Patient.created_by_doctor_id == doctor_id,
        Patient.phone == phone,
        Patient.is_active == True,
    )
    if exclude_patient_id:
        stmt = stmt.where(Patient.id != exclude_patient_id)
    conflict = (await db.execute(stmt)).scalar_one_or_none()
    if conflict:
        raise HTTPException(
            status_code=409,
            detail="Phone number already registered for this doctor.",
        )


def _apply_list_search(stmt, q: str):
    """Search by name, phone, or ABHA for /patients list endpoint."""
    term = q.strip()
    if not term:
        return stmt

    digits = re.sub(r"\D", "", term)
    if PHONE_DIGITS.match(digits):
        return stmt.where(Patient.phone == digits)

    if ABHA_PATTERN.match(term) or ("-" in term and re.fullmatch(r"[\d-]+", term)):
        abha = term.upper().replace(" ", "")
        return stmt.where(
            or_(
                Patient.abha_id == abha,
                Patient.abha_id == abha.replace("-", ""),
                func.replace(Patient.abha_id, "-", "") == abha.replace("-", ""),
            )
        )

    pattern = f"%{term}%"
    ts_vector = func.to_tsvector("simple", Patient.name)
    ts_query = func.plainto_tsquery("simple", term)
    if digits:
        return stmt.where(
            or_(
                ts_vector.op("@@")(ts_query),
                func.similarity(Patient.name, term) > 0.2,
                Patient.name.ilike(pattern),
                Patient.phone.ilike(f"%{digits}%"),
            )
        )
    return stmt.where(
        or_(
            ts_vector.op("@@")(ts_query),
            func.similarity(Patient.name, term) > 0.2,
            Patient.name.ilike(pattern),
        )
    )


def _patient_stats_subquery(doctor_id: str):
    return (
        select(
            Prescription.patient_id.label("patient_id"),
            func.max(Prescription.created_at).label("last_visit_at"),
            func.count(Prescription.id).label("prescription_count"),
        )
        .where(
            Prescription.doctor_id == doctor_id,
            Prescription.patient_id.isnot(None),
        )
        .group_by(Prescription.patient_id)
        .subquery()
    )


async def _build_patient_list_items(
    db: AsyncSession,
    patients: list[Patient],
    doctor_id: str,
) -> list[PatientListItem]:
    if not patients:
        return []

    from app.api.allergies import allergy_counts_for_patients

    patient_ids = [p.id for p in patients]
    stats = _patient_stats_subquery(doctor_id)
    rows = (
        await db.execute(
            select(
                Patient.id,
                stats.c.last_visit_at,
                func.coalesce(stats.c.prescription_count, 0),
            )
            .select_from(Patient)
            .outerjoin(stats, Patient.id == stats.c.patient_id)
            .where(Patient.id.in_(patient_ids))
        )
    ).all()
    stats_by_id = {pid: (lv, int(cnt)) for pid, lv, cnt in rows}
    allergy_counts = await allergy_counts_for_patients(db, patient_ids)

    items: list[PatientListItem] = []
    for patient in patients:
        last_visit, rx_count = stats_by_id.get(patient.id, (None, 0))
        items.append(
            PatientListItem(
                id=patient.id,
                name=patient.name,
                age=patient.age,
                sex=patient.sex,
                phone=patient.phone,
                abha_id=patient.abha_id,
                created_at=patient.created_at,
                allergy_count=allergy_counts.get(patient.id, 0),
                last_visit_at=last_visit,
                prescription_count=rx_count,
            )
        )
    return items


async def _apply_patient_updates(
    patient: Patient,
    body: PatientUpdate,
    db: AsyncSession,
    doctor: Doctor,
) -> None:
    updates = body.model_dump(exclude_unset=True)
    if "phone" in updates and updates["phone"]:
        phone = _normalize_phone(updates["phone"])
        await _phone_conflict(db, doctor.id, phone, exclude_patient_id=patient.id)
        patient.phone = phone
    if "name" in updates and updates["name"] is not None:
        patient.name = updates["name"].strip()
    if "age" in updates and updates["age"] is not None:
        patient.age = updates["age"]
    if "sex" in updates and updates["sex"] is not None:
        patient.sex = _normalize_sex(updates["sex"])
    if "abha_id" in updates:
        patient.abha_id = updates["abha_id"].strip() if updates["abha_id"] else None


@router.get("/patients/search", response_model=list[PatientSearchOut])
async def search_patients(
    q: str = Query(default="", min_length=0),
    limit: int = Query(default=10, le=50),
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    term = q.strip()
    if not term:
        return []

    base = _patient_scope(doctor, membership, clinic_id)

    if PHONE_DIGITS.match(term):
        stmt = base.where(Patient.phone == term).limit(limit)
        patients = (await db.execute(stmt)).scalars().all()
        if not patients:
            return []
        stats = _prescription_stats_subquery(doctor.id)
        rows = (
            await db.execute(
                select(
                    Patient,
                    stats.c.last_visit_date,
                    func.coalesce(stats.c.prescription_count, 0),
                )
                .outerjoin(stats, Patient.id == stats.c.patient_id)
                .where(Patient.id.in_([p.id for p in patients]))
            )
        ).all()
        return [_to_search_out(p, lv, int(cnt)) for p, lv, cnt in rows]

    if ABHA_PATTERN.match(term) or ("-" in term and re.fullmatch(r"[\d-]+", term)):
        abha = term.upper().replace(" ", "")
        stmt = base.where(
            or_(
                Patient.abha_id == abha,
                Patient.abha_id == abha.replace("-", ""),
                func.replace(Patient.abha_id, "-", "") == abha.replace("-", ""),
            )
        ).limit(limit)
        return await _fetch_search_results(db, doctor, stmt, limit)

    ts_vector = func.to_tsvector("simple", Patient.name)
    ts_query = func.plainto_tsquery("simple", term)
    stmt = (
        base.where(
            or_(
                ts_vector.op("@@")(ts_query),
                func.similarity(Patient.name, term) > 0.2,
            )
        )
        .order_by(
            func.ts_rank(ts_vector, ts_query).desc(),
            func.similarity(Patient.name, term).desc(),
        )
        .limit(limit)
    )
    return await _fetch_search_results(db, doctor, stmt, limit)


@router.get("/patients/recent", response_model=list[PatientRecentOut])
async def recent_patients(
    limit: int = Query(default=8, le=20),
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    last_visit = func.max(Prescription.created_at).label("last_visit_at")
    stmt = (
        select(Patient, last_visit)
        .join(Prescription, Prescription.patient_id == Patient.id)
        .where(
            Prescription.doctor_id == doctor.id,
            Patient.clinic_id == clinic_id,
            Patient.is_active == True,
        )
        .group_by(Patient.id)
        .order_by(last_visit.desc())
        .limit(limit)
    )
    if membership.role != "admin":
        stmt = stmt.where(Patient.created_by_doctor_id == doctor.id)

    rows = (await db.execute(stmt)).all()
    results: list[PatientRecentOut] = []
    for patient, visit_at in rows:
        results.append(
            PatientRecentOut(
                id=patient.id,
                name=patient.name,
                age=patient.age,
                sex=patient.sex,
                phone=patient.phone,
                abha_id=patient.abha_id,
                created_at=patient.created_at,
                last_visit_at=visit_at,
            )
        )
    return results


@router.get("/patients", response_model=PaginatedPatientList)
async def list_patients(
    q: str = Query(default="", description="Search by name, phone, or ABHA"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=30, ge=1, le=100),
    sex: str | None = Query(default=None, pattern="^(M|F|O)$"),
    age_min: int | None = Query(default=None, ge=1, le=119),
    age_max: int | None = Query(default=None, ge=1, le=119),
    sort: str = Query(
        default="name_asc",
        pattern="^(name_asc|name_desc|recent_visit|created_desc)$",
    ),
    has_allergies: bool | None = Query(default=None),
    visited_within_days: int | None = Query(default=None, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    stats = _patient_stats_subquery(doctor.id)
    stmt = (
        select(Patient)
        .select_from(Patient)
        .outerjoin(stats, Patient.id == stats.c.patient_id)
        .where(Patient.clinic_id == clinic_id, Patient.is_active == True)
    )
    if membership.role != "admin":
        stmt = stmt.where(Patient.created_by_doctor_id == doctor.id)

    stmt = _apply_list_search(stmt, q)

    if sex:
        stmt = stmt.where(Patient.sex == sex)
    if age_min is not None:
        stmt = stmt.where(Patient.age >= age_min)
    if age_max is not None:
        stmt = stmt.where(Patient.age <= age_max)
    if has_allergies is True:
        stmt = stmt.where(
            exists(
                select(PatientAllergy.id).where(
                    PatientAllergy.patient_id == Patient.id,
                    PatientAllergy.deleted_at.is_(None),
                )
            )
        )
    elif has_allergies is False:
        stmt = stmt.where(
            ~exists(
                select(PatientAllergy.id).where(
                    PatientAllergy.patient_id == Patient.id,
                    PatientAllergy.deleted_at.is_(None),
                )
            )
        )
    if visited_within_days is not None:
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=visited_within_days)
        stmt = stmt.where(stats.c.last_visit_at >= cutoff)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    if sort == "name_desc":
        stmt = stmt.order_by(Patient.name.desc())
    elif sort == "recent_visit":
        stmt = stmt.order_by(stats.c.last_visit_at.desc().nullslast(), Patient.name)
    elif sort == "created_desc":
        stmt = stmt.order_by(Patient.created_at.desc())
    else:
        stmt = stmt.order_by(Patient.name)

    offset = (page - 1) * limit
    patients = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    items = await _build_patient_list_items(db, list(patients), doctor.id)

    return PaginatedPatientList(
        items=items,
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/patients/{patient_id}", response_model=PatientOut)
async def get_patient(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    patient = await db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    if patient.clinic_id != clinic_id:
        raise HTTPException(status_code=403, detail="Patient does not belong to your clinic.")
    if membership.role != "admin" and patient.created_by_doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Access denied for this patient.")
    return patient


@router.post("/patients", response_model=PatientOut, status_code=201)
async def create_patient(
    body: PatientCreate,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    phone = _normalize_phone(body.phone)
    await _phone_conflict(db, doctor.id, phone)

    patient = Patient(
        clinic_id=clinic_id,
        created_by_doctor_id=doctor.id,
        name=body.name.strip(),
        age=body.age,
        sex=_normalize_sex(body.sex),
        phone=phone,
        abha_id=body.abha_id.strip() if body.abha_id else None,
    )
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    return patient


@router.put("/patients/{patient_id}", response_model=PatientOut)
async def update_patient(
    patient_id: str,
    body: PatientUpdate,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    patient = await db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    if patient.clinic_id != clinic_id:
        raise HTTPException(status_code=403, detail="Patient does not belong to your clinic.")
    if membership.role != "admin" and patient.created_by_doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Access denied for this patient.")

    await _apply_patient_updates(patient, body, db, doctor)
    await db.commit()
    await db.refresh(patient)
    return patient


@router.patch("/patients/{patient_id}", response_model=PatientOut)
async def patch_patient(
    patient_id: str,
    body: PatientUpdate,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    """Backward-compatible partial update."""
    return await update_patient(
        patient_id, body, db, doctor, membership, clinic_id
    )
