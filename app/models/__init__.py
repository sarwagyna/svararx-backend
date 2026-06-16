"""
SQLAlchemy ORM models — mirrors the Supabase schema exactly.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Optional
from sqlalchemy import (
    String, SmallInteger, Integer, Text, DateTime, Date, ForeignKey, Boolean, Numeric, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

from app.database import Base


class PrescriptionStatus(str, enum.Enum):
    draft = "draft"
    approved = "approved"


# ─── Clinic ───────────────────────────────────────────────────
class Clinic(Base):
    __tablename__ = "clinics"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    pincode: Mapped[str] = mapped_column(String(10), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    letterhead_s3_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    letterhead_type: Mapped[str] = mapped_column(String(20), default="generated")
    plan: Mapped[str] = mapped_column(String(20), default="free")
    prescription_count: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    doctors: Mapped[list["DoctorClinic"]] = relationship(back_populates="clinic")
    patients: Mapped[list["Patient"]] = relationship(back_populates="clinic")
    prescriptions: Mapped[list["Prescription"]] = relationship(back_populates="clinic")


# ─── Doctor ───────────────────────────────────────────────────
class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    auth_user_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), nullable=True, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    qualifications: Mapped[str] = mapped_column(String(200), nullable=False)
    mci_number: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    speciality: Mapped[str] = mapped_column(String(100), default="General Practitioner")
    state_council_reg: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    pin_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    onboarding_step: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    clinic_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    clinic_address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    clinic_address_line2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    clinic_city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    clinic_state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, server_default="Andhra Pradesh")
    clinic_pin: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    clinic_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    clinic_logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    signature_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    languages: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)),
        nullable=False,
        server_default=text("ARRAY['Telugu','English']::varchar[]"),
    )
    subscription_tier: Mapped[str] = mapped_column(String(20), default="free", server_default="free")
    subscription_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    voice_calibration_s3_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    referred_by_doctor_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("doctors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    practice_mode: Mapped[str] = mapped_column(String(20), default="solo", server_default="solo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    clinics: Mapped[list["DoctorClinic"]] = relationship(back_populates="doctor")
    prescriptions: Mapped[list["Prescription"]] = relationship(back_populates="doctor")
    owned_patients: Mapped[list["Patient"]] = relationship(back_populates="created_by_doctor")
    consultations: Mapped[list["Consultation"]] = relationship(back_populates="doctor")


# ─── DoctorClinic (many-to-many) ──────────────────────────────
class DoctorClinic(Base):
    __tablename__ = "doctor_clinics"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    doctor_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    clinic_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="doctor")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    doctor: Mapped["Doctor"] = relationship(back_populates="clinics")
    clinic: Mapped["Clinic"] = relationship(back_populates="doctors")


# ─── Patient ──────────────────────────────────────────────────
class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    clinic_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False)
    created_by_doctor_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("doctors.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    age: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sex: Mapped[str] = mapped_column(String(10), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    abha_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    date_of_birth: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    occupation: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    guardian_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    guardian_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    created_by_doctor: Mapped[Optional["Doctor"]] = relationship(back_populates="owned_patients")
    prescriptions: Mapped[list["Prescription"]] = relationship(back_populates="patient")
    allergies: Mapped[list["PatientAllergy"]] = relationship(back_populates="patient")
    conditions: Mapped[list["PatientCondition"]] = relationship(back_populates="patient")
    condition_suggestions: Mapped[list["PatientConditionSuggestion"]] = relationship(
        back_populates="patient"
    )
    clinic: Mapped["Clinic"] = relationship(back_populates="patients")


# ─── PatientAllergy ───────────────────────────────────────────
class PatientAllergy(Base):
    __tablename__ = "patient_allergies"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    patient_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    drug_name: Mapped[str] = mapped_column(String(200), nullable=False)
    drug_generic: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    reaction: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="unknown")
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reported_by_doctor_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("doctors.id"), nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    patient: Mapped["Patient"] = relationship(back_populates="allergies")
    reported_by: Mapped[Optional["Doctor"]] = relationship()


# ─── PatientCondition ─────────────────────────────────────────
class PatientCondition(Base):
    __tablename__ = "patient_conditions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    patient_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    condition_name: Mapped[str] = mapped_column(String(200), nullable=False)
    condition_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    diagnosed_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    added_by_doctor_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("doctors.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    patient: Mapped["Patient"] = relationship(back_populates="conditions")
    added_by: Mapped[Optional["Doctor"]] = relationship()


# ─── PatientConditionSuggestion ───────────────────────────────
class PatientConditionSuggestion(Base):
    __tablename__ = "patient_condition_suggestions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    patient_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    condition_name: Mapped[str] = mapped_column(String(200), nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    suggested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_by_doctor_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("doctors.id"), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    patient: Mapped["Patient"] = relationship(back_populates="condition_suggestions")
    reviewed_by: Mapped[Optional["Doctor"]] = relationship()


# ─── Prescription ─────────────────────────────────────────────
class Prescription(Base):
    __tablename__ = "prescriptions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    clinic_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("clinics.id"), nullable=False)
    doctor_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("doctors.id"), nullable=False)
    patient_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("patients.id"), nullable=True)
    raw_transcription: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    corrected_transcription: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    structured_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    pdf_s3_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    pdf_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_amendment: Mapped[bool] = mapped_column(Boolean, default=False)
    amends_prescription_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("prescriptions.id"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    doctor: Mapped["Doctor"] = relationship(back_populates="prescriptions")
    patient: Mapped["Patient"] = relationship(back_populates="prescriptions")
    clinic: Mapped["Clinic"] = relationship(back_populates="prescriptions")
    items: Mapped[list["PrescriptionItem"]] = relationship(
        back_populates="prescription", cascade="all, delete-orphan"
    )
    consultation: Mapped[Optional["Consultation"]] = relationship(
        back_populates="prescription", uselist=False
    )


# ─── Consultation ─────────────────────────────────────────────
class Consultation(Base):
    __tablename__ = "consultations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    doctor_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("doctors.id"), nullable=False, index=True)
    clinic_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False, index=True)
    patient_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("patients.id"), nullable=True)
    chief_complaint: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chief_complaint_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)),
        nullable=False,
        server_default=text("'{}'::varchar[]"),
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    prescription_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("prescriptions.id"), nullable=True, unique=True
    )
    visit_type: Mapped[str] = mapped_column(String(20), default="new", server_default="new")
    record_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    record_status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    raw_transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    corrected_transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    doctor: Mapped["Doctor"] = relationship(back_populates="consultations")
    patient: Mapped[Optional["Patient"]] = relationship()
    prescription: Mapped[Optional["Prescription"]] = relationship(back_populates="consultation")
    attachments: Mapped[list["ConsultationAttachment"]] = relationship(
        back_populates="consultation",
        cascade="all, delete-orphan",
    )


# ─── Consultation attachment (patient record files) ───────────
class ConsultationAttachment(Base):
    __tablename__ = "consultation_attachments"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    consultation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("consultations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("patients.id"), nullable=True)
    doctor_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("doctors.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(30), default="other", server_default="other")
    ocr_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ocr_status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    consultation: Mapped["Consultation"] = relationship(back_populates="attachments")


# ─── PrescriptionItem ─────────────────────────────────────────
class PrescriptionItem(Base):
    __tablename__ = "prescription_items"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    prescription_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=False)
    drug_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dosage: Mapped[str] = mapped_column(String(50), default="")
    frequency: Mapped[str] = mapped_column(String(10), default="")
    duration: Mapped[str] = mapped_column(String(50), default="")
    instruction: Mapped[str] = mapped_column(String(255), default="")
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    prescription: Mapped["Prescription"] = relationship(back_populates="items")


# ─── Drug ─────────────────────────────────────────────────────
class Drug(Base):
    __tablename__ = "drugs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    brand_name: Mapped[str] = mapped_column(String(255), nullable=False)
    generic_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="Other")
    schedule: Mapped[str] = mapped_column(String(10), nullable=False, default="H")
    common_dosages: Mapped[list] = mapped_column(JSONB, default=list)
    standard_frequencies: Mapped[list] = mapped_column(JSONB, default=list)
    typical_duration: Mapped[str] = mapped_column(String(50), default="5 days")
    phonetic_variants: Mapped[list] = mapped_column(JSONB, default=list)
    route: Mapped[str] = mapped_column(String(50), default="oral")
    medicine_type: Mapped[str] = mapped_column(String(20), default="allopathic")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ─── Vitals ───────────────────────────────────────────────────
class Vital(Base):
    __tablename__ = "vitals"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    consultation_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("consultations.id"), nullable=True
    )
    patient_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("patients.id"), nullable=False)
    doctor_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("doctors.id"), nullable=False)
    bp_systolic: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bp_diastolic: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    blood_sugar_mg_dl: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    blood_sugar_type: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    spo2_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    temperature_f: Mapped[Optional[float]] = mapped_column(Numeric(4, 1), nullable=True)
    pulse_bpm: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height_cm: Mapped[Optional[float]] = mapped_column(Numeric(5, 1), nullable=True)
    respiratory_rate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    patient: Mapped["Patient"] = relationship()
    doctor: Mapped["Doctor"] = relationship()
    consultation: Mapped[Optional["Consultation"]] = relationship()


# ─── Correction (STT training flywheel) ───────────────────────
class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    prescription_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("prescriptions.id"), nullable=True)
    doctor_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("doctors.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(50), nullable=False)
    wrong_value: Mapped[str] = mapped_column(Text, nullable=False)
    correct_value: Mapped[str] = mapped_column(Text, nullable=False)
    medication_index: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    correction_type: Mapped[str] = mapped_column(String(20), default="stt_error")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
