"""
Patient visit history helpers — drug extraction, Redis cache, LLM summaries.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Doctor, Patient, Prescription
from app.services.redis_client import get_redis

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300  # 5 minutes


def cache_key(patient_id: str, doctor_id: str, is_admin: bool) -> str:
    if is_admin:
        return f"patient_history:{patient_id}"
    return f"patient_history:{patient_id}:{doctor_id}"


async def invalidate_patient_history_cache(patient_id: str) -> None:
    """Drop all cached history keys for a patient (admin + per-doctor)."""
    redis_client = get_redis()
    pattern = f"patient_history:{patient_id}*"
    try:
        keys = await asyncio.to_thread(redis_client.keys, pattern)
        if keys:
            await asyncio.to_thread(redis_client.delete, *keys)
    except Exception:
        logger.exception("Failed to invalidate history cache for patient %s", patient_id)


def extract_drugs(structured: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize medications or drugs from structured_json."""
    drugs: list[dict[str, str]] = []
    for med in structured.get("medications") or []:
        if not isinstance(med, dict):
            continue
        name = (med.get("drug_name") or med.get("name") or "").strip()
        if not name:
            continue
        drugs.append(
            {
                "name": name,
                "dose": str(med.get("dosage") or med.get("dose") or "").strip(),
                "frequency": str(med.get("frequency") or "").strip(),
                "duration": str(med.get("duration") or "").strip(),
            }
        )
    if drugs:
        return drugs
    for item in structured.get("drugs") or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or item.get("drug_name") or "").strip()
        if not name:
            continue
        drugs.append(
            {
                "name": name,
                "dose": str(item.get("dose") or item.get("dosage") or "").strip(),
                "frequency": str(item.get("frequency") or "").strip(),
                "duration": str(item.get("duration") or "").strip(),
            }
        )
    return drugs


def extract_drugs_for_rx(rx: Prescription) -> list[dict[str, str]]:
    drugs = extract_drugs(rx.structured_json or {})
    if drugs:
        return drugs
    items = getattr(rx, "items", None) or []
    return [
        {
            "name": item.drug_name,
            "dose": item.dosage or "",
            "frequency": item.frequency or "",
            "duration": item.duration or "",
        }
        for item in sorted(items, key=lambda i: i.sort_order)
        if item.drug_name.strip()
    ]


def extract_chief_complaint(structured: dict[str, Any]) -> str | None:
    cc = structured.get("chief_complaint")
    if cc and str(cc).strip():
        return str(cc).strip()
    tags = structured.get("chief_complaint_tags") or []
    tag_text = ", ".join(str(t).strip() for t in tags if str(t).strip())
    if tag_text:
        return tag_text
    return None


def extract_chief_complaint_for_rx(rx: Prescription) -> str | None:
    structured = rx.structured_json or {}
    chief = extract_chief_complaint(structured)
    if chief:
        return chief
    consultation = getattr(rx, "consultation", None)
    if consultation:
        if consultation.chief_complaint and str(consultation.chief_complaint).strip():
            return str(consultation.chief_complaint).strip()
        tags = consultation.chief_complaint_tags or []
        tag_text = ", ".join(str(t).strip() for t in tags if str(t).strip())
        if tag_text:
            return tag_text
    return None


def extract_diagnosis(structured: dict[str, Any]) -> str | None:
    dx = structured.get("diagnosis")
    if dx and str(dx).strip():
        return str(dx).strip()
    return None


def extract_follow_up_date(
    structured: dict[str, Any],
    created_at: datetime,
) -> date | None:
    follow_up_days = structured.get("follow_up_days")
    if follow_up_days is not None:
        try:
            days = int(follow_up_days)
            base = created_at.date() if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc).date()
            return base + timedelta(days=days)
        except (TypeError, ValueError):
            pass
    follow_up = structured.get("follow_up")
    if follow_up and str(follow_up).strip():
        # Unstructured text — no reliable date
        return None
    return None


def pdf_url_for(prescription_id: str, pdf_s3_key: str | None, status: str = "") -> str | None:
    if pdf_s3_key or status in ("approved", "pdf_generated"):
        return f"/api/v1/prescriptions/{prescription_id}/pdf"
    return None


def visit_to_summary_item(rx: Prescription) -> dict[str, Any]:
    structured = rx.structured_json or {}
    drugs = extract_drugs_for_rx(rx)
    chief = extract_chief_complaint_for_rx(rx)
    return {
        "id": rx.id,
        "created_at": rx.created_at.isoformat(),
        "chief_complaint": chief,
        "diagnosis": extract_diagnosis(structured),
        "drugs": [{"name": d["name"], "dose": d["dose"], "frequency": d["frequency"]} for d in drugs],
        "pdf_url": pdf_url_for(rx.id, rx.pdf_s3_key, rx.status),
        "status": rx.status,
        "consultation_id": rx.consultation.id if rx.consultation else None,
        "follow_up_date": (
            extract_follow_up_date(structured, rx.created_at).isoformat()
            if extract_follow_up_date(structured, rx.created_at)
            else None
        ),
    }


def format_visit_summary(visit_index: int, rx: Prescription) -> str:
    """Format one visit for LLM context injection."""
    structured = rx.structured_json or {}
    created = rx.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    date_str = created.strftime("%d %b %Y")

    chief = extract_chief_complaint(structured)
    diagnosis = extract_diagnosis(structured)
    complaint_parts = [p for p in [chief, diagnosis] if p]
    complaint_text = ", ".join(complaint_parts) if complaint_parts else "Consultation"

    drug_parts: list[str] = []
    for d in extract_drugs(structured):
        parts = [d["name"]]
        if d["dose"]:
            parts.append(d["dose"])
        if d["frequency"]:
            parts.append(d["frequency"])
        if d["duration"]:
            parts.append(d["duration"])
        drug_parts.append(" ".join(parts))

    drugs_text = ", ".join(drug_parts) if drug_parts else "None"
    return f"Visit {visit_index} ({date_str}): {complaint_text}. Drugs: {drugs_text}."


def build_visits_summary(prescriptions: list[Prescription]) -> str:
    if not prescriptions:
        return "No prior visits."
    lines = [
        format_visit_summary(i + 1, rx)
        for i, rx in enumerate(prescriptions)
    ]
    return " ".join(lines)


async def verify_patient_access(
    db: AsyncSession,
    patient_id: str,
    clinic_id: str,
    doctor: Doctor,
    membership,
) -> Patient:
    from fastapi import HTTPException

    patient = await db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    if patient.clinic_id != clinic_id:
        raise HTTPException(status_code=403, detail="Patient does not belong to your clinic.")
    if membership.role != "admin" and patient.created_by_doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Access denied for this patient.")
    return patient


def _history_query(patient_id: str, doctor: Doctor, membership):
    stmt = (
        select(Prescription)
        .where(Prescription.patient_id == patient_id)
        .options(
            selectinload(Prescription.consultation),
            selectinload(Prescription.items),
        )
    )
    if membership.role != "admin":
        stmt = stmt.where(Prescription.doctor_id == doctor.id)
    return stmt.order_by(Prescription.created_at.desc())


async def fetch_patient_history(
    db: AsyncSession,
    patient_id: str,
    doctor: Doctor,
    membership,
    page: int = 1,
    limit: int = 20,
) -> tuple[list[Prescription], int]:
    base = _history_query(patient_id, doctor, membership)
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * limit
    result = await db.execute(base.offset(offset).limit(limit))
    return list(result.scalars().all()), total


async def fetch_last_n_prescriptions(
    db: AsyncSession,
    patient_id: str,
    clinic_id: str,
    n: int = 3,
) -> list[Prescription]:
    result = await db.execute(
        select(Prescription)
        .where(
            Prescription.patient_id == patient_id,
            Prescription.clinic_id == clinic_id,
        )
        .order_by(Prescription.created_at.desc())
        .limit(n)
    )
    return list(result.scalars().all())


async def get_cached_history(
    patient_id: str,
    doctor: Doctor,
    membership,
) -> list[dict[str, Any]] | None:
    redis_client = get_redis()
    key = cache_key(patient_id, doctor.id, membership.role == "admin")
    try:
        raw = await asyncio.to_thread(redis_client.get, key)
        if raw:
            return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except Exception:
        logger.exception("History cache read failed for %s", patient_id)
    return None


async def set_cached_history(
    patient_id: str,
    doctor: Doctor,
    membership,
    items: list[dict[str, Any]],
) -> None:
    redis_client = get_redis()
    key = cache_key(patient_id, doctor.id, membership.role == "admin")
    try:
        payload = json.dumps(items).encode("utf-8")
        await asyncio.to_thread(redis_client.setex, key, CACHE_TTL_SECONDS, payload)
    except Exception:
        logger.exception("History cache write failed for %s", patient_id)
