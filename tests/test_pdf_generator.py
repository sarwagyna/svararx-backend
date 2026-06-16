"""Prescription PDF generation tests."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.models import Clinic, Doctor, Patient, Prescription
from app.schemas import MedicationItem, StructuredPrescription
from app.services.pdf_generator import _TELUGU_FONT, generate_prescription_pdf


def _sample_pdf() -> bytes:
    doctor = Doctor(
        id="d1",
        name="Dr. Demo",
        qualifications="MBBS",
        mci_number="AP-1235",
        speciality="GP",
        pin_hash="",
        is_active=True,
    )
    clinic = Clinic(
        id="c1",
        name="Demo Clinic",
        address_line1="Main Road",
        city="Vizag",
        state="AP",
        pincode="530001",
        phone="",
    )
    patient = Patient(
        id="p1",
        clinic_id="c1",
        name="Test Patient",
        age=30,
        sex="M",
        phone="9999999999",
    )
    rx = Prescription(
        id=str(uuid4()),
        clinic_id="c1",
        doctor_id="d1",
        patient_id="p1",
        structured_json={},
        status="draft",
    )
    structured = StructuredPrescription(
        medications=[
            MedicationItem(
                drug_name="PARACETAMOL",
                dosage="500mg",
                frequency="TDS",
                duration="5 days",
                instruction="after food",
            )
        ],
        diagnosis="Fever",
        advice="Rest",
        follow_up="5 days",
    )
    return generate_prescription_pdf(
        doctor, patient, clinic, rx, structured, datetime.now(timezone.utc)
    )


def test_telugu_font_registered():
    assert _TELUGU_FONT == "NotoSansTelugu"


def test_prescription_pdf_embeds_telugu_font():
    pdf = _sample_pdf()
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 3500
    assert b"NotoSansTelugu" in pdf
