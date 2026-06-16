"""
Consultation EMR record — rich visit documentation API.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_doctor_clinic_id, get_doctor_membership
from app.config import get_settings
from app.core.dependencies import get_current_doctor
from app.database import get_db
from app.models import Clinic, Consultation, ConsultationAttachment, Doctor
from app.consultation_record_schemas import (
    AttachmentRef,
    ConsultationRecordGenerateRequest,
    ConsultationRecordOut,
    ConsultationRecordUpdate,
    PatientTimelineOut,
    PatientConsultationListItem,
    RecordAttachmentOcrResponse,
)
from app.services.consultation_record_service import (
    _attachment_to_ref,
    _content_from_dict,
    _load_patient_context_strings,
    _load_vital_for_consultation,
    build_consultation_record,
    build_patient_timeline,
    list_patient_consultations,
    merge_clinical_tests,
    store_content_on_consultation,
)
from app.services.consultation_record_structurer import structure_consultation_record
from app.services.patient_history import verify_patient_access
from app.services.record_file_storage import (
    delete_record_file,
    new_attachment_id,
    read_record_file,
    store_record_file,
)
from app.services.s3 import download_bytes_from_s3, delete_object_from_s3
from app.services.record_ocr_service import run_attachment_ocr
from app.services.vitals_flags import format_vitals_for_llm

router = APIRouter()

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ALLOWED_ATTACHMENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}


async def _get_consultation_for_doctor(
    db: AsyncSession,
    consultation_id: str,
    doctor_id: str,
    clinic_id: str | None = None,
) -> Consultation:
    consultation = await db.get(Consultation, consultation_id)
    if not consultation or consultation.doctor_id != doctor_id:
        raise HTTPException(status_code=404, detail="Consultation not found.")
    if clinic_id and consultation.clinic_id != clinic_id:
        raise HTTPException(status_code=403, detail="Consultation does not belong to your clinic.")
    return consultation


async def _get_attachment_for_consultation(
    db: AsyncSession,
    consultation_id: str,
    attachment_id: str,
    doctor_id: str,
) -> tuple[Consultation, ConsultationAttachment]:
    consultation = await _get_consultation_for_doctor(db, consultation_id, doctor_id)
    att = await db.get(ConsultationAttachment, attachment_id)
    if not att or att.consultation_id != consultation_id:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    return consultation, att


@router.get("/consultations/{consultation_id}/record", response_model=ConsultationRecordOut)
async def get_consultation_record(
    consultation_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    consultation = await _get_consultation_for_doctor(db, consultation_id, doctor.id, clinic_id)
    clinic = await db.get(Clinic, clinic_id)
    return await build_consultation_record(db, consultation, doctor=doctor, clinic=clinic)


@router.put("/consultations/{consultation_id}/record", response_model=ConsultationRecordOut)
async def update_consultation_record(
    consultation_id: str,
    body: ConsultationRecordUpdate,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    consultation = await _get_consultation_for_doctor(db, consultation_id, doctor.id, clinic_id)

    if body.visit_type:
        consultation.visit_type = body.visit_type
    if body.content is not None:
        store_content_on_consultation(consultation, body.content, body.ai_summary)
    elif body.ai_summary is not None:
        consultation.ai_summary = body.ai_summary
    if body.approved_transcript is not None:
        consultation.approved_transcript = body.approved_transcript
    if body.record_status is not None:
        consultation.record_status = body.record_status

    await db.commit()
    await db.refresh(consultation)
    clinic = await db.get(Clinic, clinic_id)
    return await build_consultation_record(db, consultation, doctor=doctor, clinic=clinic)


@router.post("/consultations/{consultation_id}/record/generate", response_model=ConsultationRecordOut)
async def generate_consultation_record(
    consultation_id: str,
    body: ConsultationRecordGenerateRequest,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    consultation = await _get_consultation_for_doctor(db, consultation_id, doctor.id, clinic_id)
    clinic = await db.get(Clinic, clinic_id)

    existing = _content_from_dict(consultation.record_json or {})
    patient_context: dict = {
        "chief_complaint": consultation.chief_complaint,
        "allergy_text": "",
        "conditions_text": "",
        "vitals_summary": None,
    }

    if consultation.patient_id:
        allergy_text, cond_text, _ = await _load_patient_context_strings(db, consultation.patient_id)
        patient_context["allergy_text"] = allergy_text
        patient_context["conditions_text"] = cond_text

    vital = await _load_vital_for_consultation(db, consultation.id)
    if vital:
        patient_context["vitals_summary"] = format_vitals_for_llm(
            bp_systolic=vital.bp_systolic,
            bp_diastolic=vital.bp_diastolic,
            weight_kg=float(vital.weight_kg) if vital.weight_kg else None,
            blood_sugar_mg_dl=vital.blood_sugar_mg_dl,
            blood_sugar_type=vital.blood_sugar_type,
            spo2_percent=vital.spo2_percent,
            temperature_f=float(vital.temperature_f) if vital.temperature_f else None,
            pulse_bpm=vital.pulse_bpm,
        )

    if body.use_llm:
        try:
            content, ai_summary = await structure_consultation_record(
                body.transcript,
                patient_context,
                existing=existing,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"LLM structuring failed: {exc}")
    else:
        content, ai_summary = existing, consultation.ai_summary

    consultation.raw_transcript = body.transcript
    store_content_on_consultation(consultation, content, ai_summary)
    await db.commit()
    await db.refresh(consultation)

    return await build_consultation_record(db, consultation, doctor=doctor, clinic=clinic)


@router.post(
    "/consultations/{consultation_id}/record/attachments",
    response_model=AttachmentRef,
)
async def upload_record_attachment(
    consultation_id: str,
    file: UploadFile = File(...),
    category: str = Form(default="other"),
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    consultation = await _get_consultation_for_doctor(db, consultation_id, doctor.id, clinic_id)
    settings = get_settings()

    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_ATTACHMENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Supported formats: PDF, JPEG, PNG, WebP.",
        )

    data = await file.read()
    if len(data) < 50:
        raise HTTPException(status_code=400, detail="File is empty.")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 10 MB limit.")

    cat = category if category in ("lab_report", "imaging", "document", "other") else "other"
    attachment_id = new_attachment_id()
    filename = file.filename or "upload"

    try:
        storage_key, presigned_url = await store_record_file(
            data,
            clinic_id,
            consultation_id,
            attachment_id,
            filename,
            mime,
            settings,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"File storage failed: {exc}") from exc

    att = ConsultationAttachment(
        id=attachment_id,
        consultation_id=consultation_id,
        patient_id=consultation.patient_id,
        doctor_id=doctor.id,
        filename=filename,
        mime_type=mime,
        file_size=len(data),
        storage_key=storage_key,
        category=cat,
        ocr_status="pending",
    )
    db.add(att)
    await db.commit()
    await db.refresh(att)
    return _attachment_to_ref(att, presigned_url=presigned_url)


async def _read_attachment_bytes(att: ConsultationAttachment) -> bytes:
    settings = get_settings()
    data = read_record_file(att.storage_key)
    if data is not None:
        return data
    if settings.aws_access_key_id and settings.aws_secret_access_key and settings.aws_s3_bucket:
        return await asyncio.to_thread(download_bytes_from_s3, att.storage_key, settings)
    raise HTTPException(status_code=404, detail="File not found.")


@router.get(
    "/consultations/{consultation_id}/record/attachments/{attachment_id}/file",
)
async def download_record_attachment(
    consultation_id: str,
    attachment_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
):
    _, att = await _get_attachment_for_consultation(
        db, consultation_id, attachment_id, doctor.id
    )
    data = await _read_attachment_bytes(att)

    return Response(
        content=data,
        media_type=att.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{att.filename}"'},
    )


@router.delete(
    "/consultations/{consultation_id}/record/attachments/{attachment_id}",
    status_code=204,
)
async def delete_record_attachment(
    consultation_id: str,
    attachment_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
):
    _, att = await _get_attachment_for_consultation(
        db, consultation_id, attachment_id, doctor.id
    )
    settings = get_settings()
    delete_record_file(att.storage_key)
    if settings.aws_access_key_id and settings.aws_s3_bucket:
        await asyncio.to_thread(delete_object_from_s3, att.storage_key, settings)
    await db.delete(att)
    await db.commit()


@router.post(
    "/consultations/{consultation_id}/record/attachments/{attachment_id}/ocr",
    response_model=RecordAttachmentOcrResponse,
)
async def ocr_record_attachment(
    consultation_id: str,
    attachment_id: str,
    merge: bool = True,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
):
    consultation, att = await _get_attachment_for_consultation(
        db, consultation_id, attachment_id, doctor.id
    )
    settings = get_settings()
    try:
        data = await _read_attachment_bytes(att)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail="File not found.") from exc

    try:
        ocr_text, tests, lab_name, sample_date = await asyncio.to_thread(
            run_attachment_ocr,
            data,
            att.mime_type or "application/octet-stream",
            att.id,
            settings,
        )
    except Exception as exc:
        att.ocr_status = "failed"
        await db.commit()
        raise HTTPException(status_code=502, detail=f"OCR failed: {exc}") from exc

    att.ocr_text = ocr_text
    att.ocr_status = "done"
    merged = False
    record_out: ConsultationRecordOut | None = None

    if merge and tests:
        content = _content_from_dict(consultation.record_json or {})
        content = content.model_copy(
            update={"clinical_tests": merge_clinical_tests(content.clinical_tests, tests)}
        )
        store_content_on_consultation(consultation, content)
        merged = True

    await db.commit()
    await db.refresh(consultation)
    await db.refresh(att)
    clinic = await db.get(Clinic, clinic_id)
    if merged:
        record_out = await build_consultation_record(
            db, consultation, doctor=doctor, clinic=clinic
        )

    return RecordAttachmentOcrResponse(
        attachment_id=att.id,
        ocr_text=ocr_text,
        clinical_tests=tests,
        lab_name=lab_name,
        sample_date=sample_date,
        merged_into_record=merged,
        record=record_out,
    )


@router.get("/patients/{patient_id}/consultations", response_model=list[PatientConsultationListItem])
async def get_patient_consultations(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
    membership=Depends(get_doctor_membership),
):
    await verify_patient_access(db, patient_id, clinic_id, doctor, membership)
    return await list_patient_consultations(db, patient_id, doctor.id)


@router.get("/patients/{patient_id}/timeline", response_model=PatientTimelineOut)
async def get_patient_timeline(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    doctor: Doctor = Depends(get_current_doctor),
    clinic_id: str = Depends(get_doctor_clinic_id),
    membership=Depends(get_doctor_membership),
):
    await verify_patient_access(db, patient_id, clinic_id, doctor, membership)
    return await build_patient_timeline(db, patient_id, doctor.id)
