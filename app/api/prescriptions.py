"""
POST /api/v1/prescription/approve  — approve + generate PDF
GET  /api/v1/patients/{id}/history — paginated visit history
GET  /api/v1/patients/{id}/history/last — most recent visit
GET  /api/v1/patients/{id}/history/{prescription_id} — visit detail
GET  /api/v1/prescriptions/{id}    — prescription detail
"""
import base64
import logging
import time
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Prescription, PrescriptionItem, Doctor, Patient, Clinic, Consultation
from app.schemas import (
    ApproveRequest,
    ApproveResponse,
    PrescriptionSummary,
    PrescriptionDetail,
    StructuredPrescription,
    VisitHistoryItem,
    PaginatedVisitHistory,
    VisitHistoryDetail,
    HistoryDrugItem,
)
from app.services.consultation_service import (
    complete_consultation_with_prescription,
    merge_chief_complaint_into_structured,
)
from app.services.consultation_record_service import sync_record_on_prescription_approve
from app.services.allergy_service import fetch_patient_allergies, format_allergy_prompt
from app.services.pdf_generator import generate_prescription_pdf
from app.services.s3 import upload_pdf_to_s3, presign_s3_url
from app.services.patient_history import (
    verify_patient_access,
    fetch_patient_history,
    visit_to_summary_item,
    invalidate_patient_history_cache,
    get_cached_history,
    set_cached_history,
    extract_drugs,
    extract_drugs_for_rx,
    extract_chief_complaint,
    extract_chief_complaint_for_rx,
    extract_diagnosis,
    extract_follow_up_date,
    pdf_url_for,
    _history_query,
)
from app.config import get_settings, Settings
from app.auth import get_doctor_clinic_id, get_doctor_membership
from app.core.dependencies import get_current_doctor
from app.core.security import decode_approval_token
from app.core.tenant import s3_prescription_key

router = APIRouter()
logger = logging.getLogger(__name__)


def _rx_to_visit_detail(rx: Prescription) -> VisitHistoryDetail:
    structured = rx.structured_json or {}
    drugs = [
        HistoryDrugItem(name=d["name"], dose=d["dose"], frequency=d["frequency"])
        for d in extract_drugs_for_rx(rx)
    ]
    return VisitHistoryDetail(
        id=rx.id,
        created_at=rx.created_at,
        approved_at=rx.approved_at,
        status=rx.status,
        chief_complaint=extract_chief_complaint_for_rx(rx),
        diagnosis=extract_diagnosis(structured),
        drugs=drugs,
        transcript=rx.raw_transcription,
        advice=structured.get("advice") or structured.get("notes"),
        follow_up=structured.get("follow_up"),
        follow_up_date=extract_follow_up_date(structured, rx.created_at),
        pdf_url=pdf_url_for(rx.id, rx.pdf_s3_key, rx.status),
        raw_transcription=rx.raw_transcription,
        consultation_id=rx.consultation.id if rx.consultation else None,
    )


async def _load_prescription_for_detail(
    db: AsyncSession,
    prescription_id: str,
) -> Prescription | None:
    result = await db.execute(
        select(Prescription)
        .where(Prescription.id == prescription_id)
        .options(
            selectinload(Prescription.items),
            selectinload(Prescription.consultation),
        )
    )
    return result.scalar_one_or_none()


def _dict_to_visit_item(data: dict) -> VisitHistoryItem:
    return VisitHistoryItem(
        id=data["id"],
        created_at=datetime.fromisoformat(data["created_at"]),
        chief_complaint=data.get("chief_complaint"),
        diagnosis=data.get("diagnosis"),
        drugs=[HistoryDrugItem(**d) for d in data.get("drugs", [])],
        pdf_url=data.get("pdf_url"),
        status=data.get("status") or "approved",
        consultation_id=data.get("consultation_id"),
        follow_up_date=(
            date.fromisoformat(data["follow_up_date"])
            if data.get("follow_up_date")
            else None
        ),
    )


@router.post("/prescription/approve", response_model=ApproveResponse)
async def approve_prescription(
    body: ApproveRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    doctor: Doctor = Depends(get_current_doctor),
    membership = Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    t_start = time.monotonic()

    approving_doctor = doctor
    if body.approval_token:
        approval = decode_approval_token(body.approval_token)
        if approval["clinic_id"] != clinic_id:
            raise HTTPException(status_code=403, detail="Approval token clinic mismatch.")
        if body.prescription_id and approval.get("prescription_id"):
            if approval["prescription_id"] != body.prescription_id:
                raise HTTPException(status_code=403, detail="Approval token prescription mismatch.")
        approving_doctor = await db.get(Doctor, approval["sub"])
        if not approving_doctor or not approving_doctor.is_active:
            raise HTTPException(status_code=403, detail="Approving doctor not found.")
    elif membership.role == "compounder":
        raise HTTPException(
            status_code=403,
            detail="Doctor PIN required to approve. Compounder cannot approve directly.",
        )
    elif body.approving_doctor_id and body.approving_doctor_id != doctor.id:
        approving_doctor = await db.get(Doctor, body.approving_doctor_id)
        if not approving_doctor:
            raise HTTPException(status_code=404, detail="Approving doctor not found.")

    patient = await db.get(Patient, body.patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")

    if patient.clinic_id != clinic_id:
        raise HTTPException(status_code=403, detail="Patient does not belong to your clinic.")
    if membership.role != "admin" and membership.role != "compounder" and patient.created_by_doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Access denied for this patient.")

    clinic = await db.get(Clinic, clinic_id)
    if not clinic:
        raise HTTPException(status_code=404, detail="Clinic not found.")

    named_meds = [m for m in body.structured.medications if m.drug_name.strip()]
    if not named_meds:
        raise HTTPException(
            status_code=422,
            detail="Prescription must have at least one named medication.",
        )

    allergy_flagged = [
        m for m in named_meds
        if m.allergy_drug or m.allergy_warning
    ]
    if allergy_flagged:
        ack_drugs = {a.drug_name.upper() for a in body.allergy_acknowledgments}
        missing = [m.drug_name for m in allergy_flagged if m.drug_name.upper() not in ack_drugs]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"Allergy acknowledgment required for: {', '.join(missing)}",
            )

    now = datetime.now(timezone.utc)

    structured_data = body.structured.model_dump()
    if body.consultation_id:
        consultation = await db.get(Consultation, body.consultation_id)
        if consultation and consultation.doctor_id == approving_doctor.id:
            structured_data = merge_chief_complaint_into_structured(
                structured_data,
                consultation.chief_complaint,
                list(consultation.chief_complaint_tags or []),
            )
    if body.allergy_acknowledgments:
        structured_data["allergy_acknowledgments"] = [
            {**ack.model_dump(), "acknowledged_at": now.isoformat()}
            for ack in body.allergy_acknowledgments
        ]

    # Look up the draft if an id was supplied. A missing draft (stale id from an
    # aborted attempt) is not fatal — fall through and create a fresh approved
    # prescription instead of returning 404.
    prescription = await db.get(Prescription, body.prescription_id) if body.prescription_id else None
    if prescription:
        if prescription.clinic_id != clinic_id:
            raise HTTPException(status_code=403, detail="Prescription does not belong to your clinic.")
        if membership.role != "admin" and membership.role != "compounder" and prescription.doctor_id != doctor.id:
            raise HTTPException(status_code=403, detail="Access denied for this prescription.")
        if prescription.status != "draft":
            # Idempotent re-approval: a duplicate submit (e.g. double-click or a
            # retry after a flaky network response) lands here once the draft has
            # already been approved. Return the existing prescription instead of a
            # 409 so the client lands on the success state rather than an error.
            if prescription.status in ("approved", "pdf_generated"):
                approval_time_ms = int((time.monotonic() - t_start) * 1000)
                return ApproveResponse(
                    prescription_id=prescription.id,
                    pdf_url=presign_s3_url(prescription.pdf_s3_key, settings)
                    or pdf_url_for(prescription.id, prescription.pdf_s3_key, prescription.status),
                    pdf_base64=None,
                    status=prescription.status,
                    pdf_generation_time_ms=0,
                    upload_time_ms=0,
                    approval_time_ms=approval_time_ms,
                    sla_exceeded=False,
                )
            raise HTTPException(status_code=409, detail="Prescription is not a draft.")
        prescription.patient_id = body.patient_id
        prescription.doctor_id = approving_doctor.id
        prescription.raw_transcription = body.raw_transcription
        prescription.structured_json = structured_data
        prescription.approved_at = now
        existing_items = (
            await db.execute(
                select(PrescriptionItem).where(PrescriptionItem.prescription_id == prescription.id)
            )
        ).scalars().all()
        for item in existing_items:
            await db.delete(item)
        await db.flush()
    else:
        prescription = Prescription(
            clinic_id=clinic_id,
            doctor_id=approving_doctor.id,
            patient_id=body.patient_id,
            raw_transcription=body.raw_transcription,
            structured_json=structured_data,
            status="draft",
            approved_at=now,
        )
        db.add(prescription)
        await db.flush()

    for i, med in enumerate(named_meds):
        db.add(PrescriptionItem(
            prescription_id=prescription.id,
            drug_name=med.drug_name.upper(),
            dosage=med.dosage,
            frequency=med.frequency,
            duration=med.duration,
            instruction=med.instruction,
            sort_order=i,
        ))

    pdf_start = time.monotonic()
    allergy_records = await fetch_patient_allergies(db, body.patient_id)
    known_allergies = format_allergy_prompt(allergy_records)
    allergy_list = [part.strip() for part in known_allergies.split(";")] if known_allergies else []
    pdf_bytes = generate_prescription_pdf(
        doctor=approving_doctor,
        patient=patient,
        clinic=clinic,
        prescription=prescription,
        structured=body.structured,
        approved_at=now,
        known_allergies=allergy_list,
    )
    pdf_generation_time_ms = int((time.monotonic() - pdf_start) * 1000)

    s3_key = s3_prescription_key(clinic_id, prescription.id)
    pdf_url = None
    pdf_base64 = None
    upload_start = time.monotonic()

    try:
        pdf_url = await upload_pdf_to_s3(pdf_bytes, s3_key, settings)
        prescription.pdf_s3_key = s3_key
    except Exception:
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
    upload_time_ms = int((time.monotonic() - upload_start) * 1000)

    prescription.status = "approved"
    await db.commit()

    if body.consultation_id:
        await complete_consultation_with_prescription(
            db, body.consultation_id, approving_doctor.id, prescription.id
        )
        consultation = await db.get(Consultation, body.consultation_id)
        if consultation:
            await sync_record_on_prescription_approve(
                db,
                consultation,
                prescription,
                raw_transcription=body.raw_transcription,
            )
        await db.commit()

    await invalidate_patient_history_cache(body.patient_id)

    approval_time_ms = int((time.monotonic() - t_start) * 1000)
    if approval_time_ms > settings.sla_threshold_seconds * 1000:
        logger.warning(
            "Prescription approval exceeded SLA (%ds): pdf=%dms upload=%dms total=%dms",
            settings.sla_threshold_seconds,
            pdf_generation_time_ms,
            upload_time_ms,
            approval_time_ms,
        )

    sla_exceeded = approval_time_ms > settings.sla_threshold_seconds * 1000
    return ApproveResponse(
        prescription_id=prescription.id,
        pdf_url=pdf_url,
        pdf_base64=pdf_base64,
        status=prescription.status,
        pdf_generation_time_ms=pdf_generation_time_ms,
        upload_time_ms=upload_time_ms,
        approval_time_ms=approval_time_ms,
        sla_exceeded=sla_exceeded,
    )


@router.get("/patients/{patient_id}/history/last", response_model=VisitHistoryDetail)
async def get_patient_last_visit(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    await verify_patient_access(db, patient_id, clinic_id, doctor, membership)

    result = await db.execute(_history_query(patient_id, doctor, membership).limit(1))
    rx = result.scalar_one_or_none()
    if not rx:
        raise HTTPException(status_code=404, detail="No visit history for this patient.")

    return _rx_to_visit_detail(rx)


@router.get("/patients/{patient_id}/history/{prescription_id}", response_model=VisitHistoryDetail)
async def get_patient_visit_detail(
    patient_id: str,
    prescription_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    await verify_patient_access(db, patient_id, clinic_id, doctor, membership)

    rx = await _load_prescription_for_detail(db, prescription_id)
    if not rx or rx.patient_id != patient_id:
        raise HTTPException(status_code=404, detail="Prescription not found.")
    if rx.clinic_id != clinic_id:
        raise HTTPException(status_code=403, detail="Prescription does not belong to your clinic.")
    if membership.role != "admin" and rx.doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Access denied for this prescription.")

    return _rx_to_visit_detail(rx)


@router.get("/patients/{patient_id}/history", response_model=PaginatedVisitHistory)
async def get_patient_history(
    patient_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    fresh: bool = Query(default=False, description="Bypass cached history"),
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    await verify_patient_access(db, patient_id, clinic_id, doctor, membership)

    cached = None if fresh else await get_cached_history(patient_id, doctor, membership)
    if cached is None:
        all_rx, total_count = await fetch_patient_history(
            db, patient_id, doctor, membership, page=1, limit=500
        )
        cached = [visit_to_summary_item(rx) for rx in all_rx]
        await set_cached_history(patient_id, doctor, membership, cached)
    else:
        total_count = len(cached)

    offset = (page - 1) * limit
    page_slice = cached[offset : offset + limit]

    return PaginatedVisitHistory(
        items=[_dict_to_visit_item(item) for item in page_slice],
        total=total_count,
        page=page,
        limit=limit,
    )


@router.get("/prescriptions/{prescription_id}/pdf")
async def download_prescription_pdf(
    prescription_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership=Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    rx = await db.get(Prescription, prescription_id)
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found.")
    if rx.clinic_id != clinic_id:
        raise HTTPException(status_code=403, detail="Prescription does not belong to your clinic.")
    if membership.role != "admin" and rx.doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Access denied for this prescription.")

    patient = await db.get(Patient, rx.patient_id)
    clinic = await db.get(Clinic, clinic_id)
    rx_doctor = await db.get(Doctor, rx.doctor_id)
    if not patient or not clinic or not rx_doctor:
        raise HTTPException(status_code=404, detail="Prescription data incomplete.")

    structured_data = rx.structured_json or {}
    structured = StructuredPrescription(**structured_data)
    approved_at = rx.approved_at or datetime.now(timezone.utc)
    pdf_bytes = generate_prescription_pdf(
        doctor=rx_doctor,
        patient=patient,
        clinic=clinic,
        prescription=rx,
        structured=structured,
        approved_at=approved_at,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="prescription-{prescription_id}.pdf"',
        },
    )


@router.get("/prescriptions/{prescription_id}", response_model=PrescriptionDetail)
async def get_prescription(
    prescription_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    membership = Depends(get_doctor_membership),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    rx = await db.get(Prescription, prescription_id)
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found.")

    if rx.clinic_id != clinic_id:
        raise HTTPException(status_code=403, detail="Prescription does not belong to your clinic.")
    if membership.role != "admin" and rx.doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Access denied for this prescription.")

    structured_data = rx.structured_json or {}
    return PrescriptionDetail(
        id=rx.id,
        patient_id=rx.patient_id,
        created_at=rx.created_at,
        approved_at=rx.approved_at,
        status=rx.status,
        structured=StructuredPrescription(**structured_data),
        pdf_s3_key=rx.pdf_s3_key,
        raw_transcription=rx.raw_transcription,
    )
