"""
Assemble, merge, and timeline helpers for rich consultation EMR records.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Clinic,
    Consultation,
    ConsultationAttachment,
    Doctor,
    Patient,
    PatientAllergy,
    PatientCondition,
    Prescription,
    Vital,
)
from app.consultation_record_schemas import (
    AttachmentRef,
    ClinicalTestResult,
    ConsultationRecordContent,
    ConsultationRecordOut,
    DiagnosisSection,
    FollowUpSection,
    HistorySection,
    PatientRecordSection,
    PatientConsultationListItem,
    PatientTimelineOut,
    PrescriptionMedRecord,
    TimelineEvent,
    TimelineVisit,
    TranscriptSection,
    VisitRecordSection,
    VitalsRecordSection,
)
from app.services.allergy_service import format_allergy_prompt
from app.services.patient_history import extract_drugs_for_rx


def _compute_bmi(height_cm: float | None, weight_kg: float | None) -> float | None:
    if height_cm is None or weight_kg is None or height_cm <= 0:
        return None
    height_m = height_cm / 100.0
    return round(weight_kg / (height_m * height_m), 1)


def _parse_chief_complaints(text: str | None, tags: list[str] | None) -> list[str]:
    bullets: list[str] = []
    if text and text.strip():
        parts = re.split(r"[,;]\s*|\n", text.strip())
        bullets.extend(p.strip() for p in parts if p.strip())
    for tag in tags or []:
        t = str(tag).strip()
        if t and t not in bullets:
            bullets.append(t)
    return bullets


def _content_from_dict(data: dict[str, Any]) -> ConsultationRecordContent:
    if not data:
        return ConsultationRecordContent()
    try:
        return ConsultationRecordContent.model_validate(data)
    except Exception:
        return ConsultationRecordContent()


def _meds_from_prescription(rx: Prescription | None) -> list[PrescriptionMedRecord]:
    if not rx:
        return []
    structured = rx.structured_json or {}
    meds: list[PrescriptionMedRecord] = []
    for med in structured.get("medications") or []:
        if not isinstance(med, dict):
            continue
        drug = str(med.get("drug_name") or med.get("name") or "").strip()
        if not drug:
            continue
        instruction = str(med.get("instruction") or "")
        food = ""
        lower = instruction.lower()
        if "before" in lower:
            food = "before food"
        elif "after" in lower:
            food = "after food"
        meds.append(
            PrescriptionMedRecord(
                drug_name=drug,
                strength=str(med.get("dosage") or med.get("dose") or ""),
                dose=str(med.get("dosage") or med.get("dose") or ""),
                frequency=str(med.get("frequency") or ""),
                duration=str(med.get("duration") or ""),
                food_timing=food,
                notes=instruction,
            )
        )
    if meds:
        return meds
    for item in sorted(rx.items or [], key=lambda i: i.sort_order):
        if item.drug_name.strip():
            meds.append(
                PrescriptionMedRecord(
                    drug_name=item.drug_name,
                    strength=item.dosage,
                    dose=item.dosage,
                    frequency=item.frequency,
                    duration=item.duration,
                    food_timing=item.instruction,
                    notes=item.instruction,
                )
            )
    return meds


def _merge_content_with_rx(
    content: ConsultationRecordContent,
    rx: Prescription | None,
    chief_complaint: str | None,
    tags: list[str] | None,
) -> ConsultationRecordContent:
    complaints = content.chief_complaints or _parse_chief_complaints(chief_complaint, tags)
    if not content.chief_complaints and complaints:
        content = content.model_copy(update={"chief_complaints": complaints})

    if rx and not content.prescription:
        content = content.model_copy(update={"prescription": _meds_from_prescription(rx)})

    structured = (rx.structured_json or {}) if rx else {}
    if not content.diagnosis.primary and structured.get("diagnosis"):
        content = content.model_copy(
            update={
                "diagnosis": content.diagnosis.model_copy(
                    update={"primary": str(structured.get("diagnosis", ""))}
                )
            }
        )
    if not content.advice and structured.get("advice"):
        advice_text = str(structured.get("advice", "")).strip()
        if advice_text:
            parts = [p.strip() for p in re.split(r"[\n;]", advice_text) if p.strip()]
            content = content.model_copy(update={"advice": parts})
    if not content.follow_up.instructions and structured.get("follow_up"):
        content = content.model_copy(
            update={
                "follow_up": content.follow_up.model_copy(
                    update={"instructions": str(structured.get("follow_up", ""))}
                )
            }
        )
    return content


def _build_timeline_preview(
    consultation: Consultation,
    content: ConsultationRecordContent,
    ai_summary: str | None,
) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    at = consultation.started_at
    events.append(TimelineEvent(type="consultation", label="Consultation", at=at))
    if ai_summary:
        events.append(TimelineEvent(type="ai_summary", label="AI Summary", detail=ai_summary[:120], at=at))
    if content.prescription:
        events.append(
            TimelineEvent(
                type="prescription",
                label="Prescription",
                detail=f"{len(content.prescription)} medicine(s)",
                at=at,
            )
        )
    for test in content.investigations_ordered:
        events.append(TimelineEvent(type="tests_ordered", label=test, at=at))
    for result in content.clinical_tests:
        detail = f"{result.value} {result.unit}".strip()
        if result.reference_range:
            detail = f"{detail} (ref {result.reference_range})".strip()
        events.append(
            TimelineEvent(
                type="lab_report",
                label=result.test_name or "Lab result",
                detail=detail or None,
                at=at,
            )
        )
    if content.follow_up.instructions or content.follow_up.next_visit_date:
        detail = content.follow_up.instructions or str(content.follow_up.next_visit_date)
        events.append(TimelineEvent(type="follow_up", label="Follow-up", detail=detail, at=at))
    return events


async def _load_vital_for_consultation(
    db: AsyncSession,
    consultation_id: str,
) -> Vital | None:
    row = await db.execute(
        select(Vital)
        .where(Vital.consultation_id == consultation_id)
        .order_by(Vital.recorded_at.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


async def _load_patient_context_strings(
    db: AsyncSession,
    patient_id: str,
) -> tuple[str, str, str]:
    allergy_rows = await db.execute(
        select(PatientAllergy)
        .where(PatientAllergy.patient_id == patient_id, PatientAllergy.deleted_at.is_(None))
    )
    allergies = allergy_rows.scalars().all()
    allergy_text = format_allergy_prompt(allergies) or ""

    cond_rows = await db.execute(
        select(PatientCondition)
        .where(PatientCondition.patient_id == patient_id, PatientCondition.status == "active")
    )
    conditions = cond_rows.scalars().all()
    cond_text = ", ".join(c.condition_name for c in conditions) if conditions else ""

    rx_row = await db.execute(
        select(Prescription)
        .where(Prescription.patient_id == patient_id, Prescription.status == "approved")
        .order_by(Prescription.approved_at.desc())
        .limit(1)
    )
    last_rx = rx_row.scalar_one_or_none()
    med_text = ""
    if last_rx:
        drugs = extract_drugs_for_rx(last_rx)
        med_text = ", ".join(d["name"] for d in drugs[:8])

    return allergy_text, cond_text, med_text


def _patient_section(patient: Patient | None) -> PatientRecordSection:
    if not patient:
        return PatientRecordSection()
    dob: date | None = None
    if patient.date_of_birth:
        dob = patient.date_of_birth.date()
    return PatientRecordSection(
        patient_id=patient.id,
        full_name=patient.name,
        age=patient.age,
        date_of_birth=dob,
        gender=patient.sex,
        phone=patient.phone,
        address=patient.address,
        occupation=patient.occupation,
    )


def _vitals_section(vital: Vital | None) -> VitalsRecordSection:
    if not vital:
        return VitalsRecordSection()
    weight = float(vital.weight_kg) if vital.weight_kg is not None else None
    height = float(vital.height_cm) if vital.height_cm is not None else None
    temp = float(vital.temperature_f) if vital.temperature_f is not None else None
    sugar = vital.blood_sugar_mg_dl
    sugar_type = vital.blood_sugar_type
    random_sugar = sugar if sugar_type == "random" or sugar_type is None else None
    return VitalsRecordSection(
        height_cm=height,
        weight_kg=weight,
        bmi=_compute_bmi(height, weight),
        temperature_f=temp,
        bp_systolic=vital.bp_systolic,
        bp_diastolic=vital.bp_diastolic,
        pulse_bpm=vital.pulse_bpm,
        respiratory_rate=vital.respiratory_rate,
        spo2_percent=vital.spo2_percent,
        random_blood_sugar_mg_dl=random_sugar if sugar_type != "fasting" else None,
        blood_sugar_type=sugar_type,
        recorded_at=vital.recorded_at,
    )


def _attachment_url(consultation_id: str, attachment_id: str) -> str:
    return f"/api/v1/consultations/{consultation_id}/record/attachments/{attachment_id}/file"


def _attachment_to_ref(att: ConsultationAttachment, *, presigned_url: str | None = None) -> AttachmentRef:
    return AttachmentRef(
        id=att.id,
        filename=att.filename,
        mime_type=att.mime_type,
        url=presigned_url or _attachment_url(att.consultation_id, att.id),
        uploaded_at=att.uploaded_at,
        category=att.category or "other",  # type: ignore[arg-type]
        file_size=att.file_size,
        ocr_text=att.ocr_text,
        ocr_status=att.ocr_status,
    )


async def _load_attachments(
    db: AsyncSession,
    consultation_id: str,
) -> list[ConsultationAttachment]:
    rows = await db.execute(
        select(ConsultationAttachment)
        .where(ConsultationAttachment.consultation_id == consultation_id)
        .order_by(ConsultationAttachment.uploaded_at.desc())
    )
    return list(rows.scalars().all())


def merge_clinical_tests(
    existing: list[ClinicalTestResult],
    incoming: list[ClinicalTestResult],
) -> list[ClinicalTestResult]:
    merged = list(existing)
    seen = {
        (t.test_name.strip().lower(), t.value.strip(), t.unit.strip())
        for t in merged
    }
    for test in incoming:
        key = (test.test_name.strip().lower(), test.value.strip(), test.unit.strip())
        if key in seen:
            continue
        seen.add(key)
        merged.append(test)
    return merged


async def build_consultation_record(
    db: AsyncSession,
    consultation: Consultation,
    *,
    doctor: Doctor | None = None,
    clinic: Clinic | None = None,
    patient: Patient | None = None,
    prescription: Prescription | None = None,
) -> ConsultationRecordOut:
    if patient is None and consultation.patient_id:
        patient = await db.get(Patient, consultation.patient_id)
    if doctor is None:
        doctor = await db.get(Doctor, consultation.doctor_id)
    if prescription is None and consultation.prescription_id:
        prescription = await db.get(
            Prescription,
            consultation.prescription_id,
            options=[selectinload(Prescription.items)],
        )

    vital = await _load_vital_for_consultation(db, consultation.id)
    content = _content_from_dict(consultation.record_json or {})
    tags = list(consultation.chief_complaint_tags or [])
    content = _merge_content_with_rx(
        content,
        prescription,
        consultation.chief_complaint,
        tags,
    )

    if patient and not content.history.allergy_history:
        allergy_text, cond_text, med_text = await _load_patient_context_strings(db, patient.id)
        history_updates: dict[str, str] = {}
        if allergy_text:
            history_updates["allergy_history"] = allergy_text
        if cond_text and not content.history.past_medical_history:
            history_updates["past_medical_history"] = cond_text
        if med_text and not content.history.current_medications:
            history_updates["current_medications"] = med_text
        if history_updates:
            content = content.model_copy(
                update={"history": content.history.model_copy(update=history_updates)}
            )

    clinic_name = clinic.name if clinic else (doctor.clinic_name or "") if doctor else ""
    visit = VisitRecordSection(
        visit_id=consultation.id,
        date_time=consultation.started_at,
        doctor_name=doctor.name if doctor else "",
        department_specialty=doctor.speciality if doctor else "",
        clinic_name=clinic_name,
        visit_type=consultation.visit_type or "new",
    )

    raw = consultation.raw_transcript
    corrected = consultation.corrected_transcript
    approved = consultation.approved_transcript
    if prescription and not raw:
        raw = prescription.raw_transcription
    if prescription and not corrected:
        corrected = prescription.corrected_transcription

    ai_summary = consultation.ai_summary
    timeline = _build_timeline_preview(consultation, content, ai_summary)
    attachment_rows = await _load_attachments(db, consultation.id)
    attachments = [_attachment_to_ref(a) for a in attachment_rows]

    return ConsultationRecordOut(
        patient=_patient_section(patient),
        visit=visit,
        vitals=_vitals_section(vital),
        content=content,
        ai_summary=ai_summary,
        transcripts=TranscriptSection(raw=raw, corrected=corrected, approved=approved),
        attachments=attachments,
        record_status=consultation.record_status or "draft",
        prescription_id=consultation.prescription_id,
        timeline_preview=timeline,
    )


async def build_patient_timeline(
    db: AsyncSession,
    patient_id: str,
    doctor_id: str,
    limit: int = 20,
) -> PatientTimelineOut:
    rows = await db.execute(
        select(Consultation)
        .where(
            Consultation.patient_id == patient_id,
            Consultation.doctor_id == doctor_id,
            Consultation.completed_at.isnot(None),
        )
        .order_by(Consultation.started_at.desc())
        .limit(limit)
    )
    consultations = rows.scalars().all()
    visits: list[TimelineVisit] = []

    for c in consultations:
        record = await build_consultation_record(db, c)
        visit_date = c.started_at.date()
        events = [
            TimelineEvent(
                type=e.type,
                label=e.label,
                detail=e.detail,
                at=e.at,
            )
            for e in record.timeline_preview
        ]
        if record.attachments:
            for att in record.attachments:
                events.append(
                    TimelineEvent(
                        type="attachment",
                        label=att.filename or "Attachment",
                        at=att.uploaded_at,
                    )
                )
        visits.append(TimelineVisit(visit_id=c.id, date=visit_date, events=events))

    return PatientTimelineOut(patient_id=patient_id, visits=visits)


async def list_patient_consultations(
    db: AsyncSession,
    patient_id: str,
    doctor_id: str,
    limit: int = 50,
) -> list[PatientConsultationListItem]:
    rows = await db.execute(
        select(Consultation)
        .where(
            Consultation.patient_id == patient_id,
            Consultation.doctor_id == doctor_id,
        )
        .order_by(Consultation.started_at.desc())
        .limit(limit)
    )
    consultations = rows.scalars().all()
    items: list[PatientConsultationListItem] = []

    for c in consultations:
        content = _content_from_dict(c.record_json or {})
        primary = content.diagnosis.primary or None
        if not primary and content.diagnosis.provisional:
            primary = content.diagnosis.provisional
        items.append(
            PatientConsultationListItem(
                consultation_id=c.id,
                started_at=c.started_at,
                completed_at=c.completed_at,
                visit_type=c.visit_type or "new",
                record_status=c.record_status or "draft",
                chief_complaint=c.chief_complaint,
                diagnosis_primary=primary,
                prescription_id=c.prescription_id,
                ai_summary=c.ai_summary,
            )
        )
    return items


def apply_llm_payload_to_content(
    content: ConsultationRecordContent,
    payload: dict[str, Any],
) -> tuple[ConsultationRecordContent, str | None]:
    """Merge LLM JSON into content; return updated content and ai_summary."""
    ai_summary = payload.get("ai_summary")
    if isinstance(ai_summary, str):
        ai_summary = ai_summary.strip() or None
    else:
        ai_summary = None

    updates: dict[str, Any] = {}
    if payload.get("chief_complaints"):
        updates["chief_complaints"] = payload["chief_complaints"]
    if payload.get("history"):
        updates["history"] = HistorySection.model_validate(payload["history"])
    if payload.get("examination_findings"):
        updates["examination_findings"] = payload["examination_findings"]
    if payload.get("diagnosis"):
        updates["diagnosis"] = DiagnosisSection.model_validate(payload["diagnosis"])
    if payload.get("prescription"):
        updates["prescription"] = [
            PrescriptionMedRecord.model_validate(m) for m in payload["prescription"]
        ]
    if payload.get("investigations_ordered"):
        updates["investigations_ordered"] = payload["investigations_ordered"]
    if payload.get("clinical_tests"):
        updates["clinical_tests"] = [
            ClinicalTestResult.model_validate(t) for t in payload["clinical_tests"]
        ]
    if payload.get("advice"):
        updates["advice"] = payload["advice"]
    if payload.get("follow_up"):
        updates["follow_up"] = FollowUpSection.model_validate(payload["follow_up"])

    merged = content.model_copy(update=updates) if updates else content
    return merged, ai_summary


def store_content_on_consultation(
    consultation: Consultation,
    content: ConsultationRecordContent,
    ai_summary: str | None = None,
) -> None:
    consultation.record_json = content.model_dump(mode="json")
    if ai_summary:
        consultation.ai_summary = ai_summary


async def sync_record_on_prescription_approve(
    db: AsyncSession,
    consultation: Consultation,
    prescription: Prescription,
    raw_transcription: str | None = None,
) -> None:
    """Merge approved Rx and transcripts into the consultation EMR record."""
    content = _content_from_dict(consultation.record_json or {})
    content = _merge_content_with_rx(
        content,
        prescription,
        consultation.chief_complaint,
        list(consultation.chief_complaint_tags or []),
    )
    if raw_transcription:
        consultation.raw_transcript = raw_transcription
    elif prescription.raw_transcription and not consultation.raw_transcript:
        consultation.raw_transcript = prescription.raw_transcription
    if prescription.corrected_transcription:
        consultation.corrected_transcript = prescription.corrected_transcription
    store_content_on_consultation(consultation, content)
    consultation.record_status = "approved"
