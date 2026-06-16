"""
PDF generation using ReportLab (pure Python, no system DLLs required).
Generates a legally compliant Indian prescription PDF on A5 paper.
All drug names are rendered in CAPITALS.
"""
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo
import logging
from pathlib import Path

_IST = ZoneInfo("Asia/Kolkata")

logger = logging.getLogger(__name__)

from reportlab.lib.pagesizes import A5
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle,
    Spacer, HRFlowable, KeepTogether,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app.models import Doctor, Patient, Prescription, Clinic
from app.schemas import StructuredPrescription
from app.services.pdf_service import format_patient_instruction

# ─── Font Registration ────────────────────────────────────────
_FONTS_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "fonts"
_TELUGU_FONT_PATH = _FONTS_DIR / "NotoSansTelugu-Regular.ttf"


def _register_telugu_font() -> str:
    if not _TELUGU_FONT_PATH.exists():
        logger.warning(
            "Telugu font not found at %s; Telugu text may not render in PDFs",
            _TELUGU_FONT_PATH,
        )
        return "Helvetica"
    try:
        pdfmetrics.registerFont(TTFont("NotoSansTelugu", str(_TELUGU_FONT_PATH)))
        return "NotoSansTelugu"
    except Exception as exc:
        logger.warning("Failed to register Telugu font: %s", exc)
        return "Helvetica"


_TELUGU_FONT = _register_telugu_font()

# ─── Colours ──────────────────────────────────────────────────
C_BLACK   = colors.HexColor("#0e0f0c")
C_GREEN   = colors.HexColor("#054d28")
C_LIME    = colors.HexColor("#9fe870")
C_GRAY    = colors.HexColor("#868685")
C_DARK    = colors.HexColor("#454745")
C_BG      = colors.HexColor("#e8ebe6")
C_BG_DIAG = colors.HexColor("#e2f6d5")
C_RED     = colors.HexColor("#c0392b")

# ─── Styles ───────────────────────────────────────────────────
def _styles():
    base = dict(fontName="Helvetica", textColor=C_BLACK, leading=14)

    clinic_name  = ParagraphStyle("clinic_name",  fontSize=16, fontName="Helvetica-Bold",   textColor=C_BLACK,  leading=20)
    doctor_name  = ParagraphStyle("doctor_name",  fontSize=11, fontName="Helvetica-Bold",   textColor=C_BLACK,  leading=14)
    doctor_meta  = ParagraphStyle("doctor_meta",  fontSize=8,  fontName="Helvetica",        textColor=C_DARK,   leading=11)
    rx_symbol    = ParagraphStyle("rx_symbol",    fontSize=28, fontName="Helvetica-Bold",   textColor=C_GREEN,  leading=32, alignment=TA_RIGHT)

    label        = ParagraphStyle("label",        fontSize=7,  fontName="Helvetica-Bold",   textColor=C_GRAY,   leading=10)
    value        = ParagraphStyle("value",        fontSize=9,  fontName="Helvetica-Bold",   textColor=C_BLACK,  leading=12)
    diag_label   = ParagraphStyle("diag_label",   fontSize=7,  fontName="Helvetica-Bold",   textColor=C_GRAY,   leading=10)
    diag_value   = ParagraphStyle("diag_value",   fontSize=10, fontName="Helvetica-Bold",   textColor=C_BLACK,  leading=13)

    drug_name    = ParagraphStyle("drug_name",    fontSize=11, fontName="Helvetica-Bold",   textColor=C_BLACK,  leading=14)
    drug_detail  = ParagraphStyle("drug_detail",  fontSize=8,  fontName="Helvetica",        textColor=C_DARK,   leading=11)
    drug_dur     = ParagraphStyle("drug_dur",     fontSize=8,  fontName="Helvetica-Bold",   textColor=C_DARK,   leading=11, alignment=TA_RIGHT)
    drug_num     = ParagraphStyle("drug_num",     fontSize=8,  fontName="Helvetica-Bold",   textColor=C_GRAY,   leading=14)

    notes_label  = ParagraphStyle("notes_label",  fontSize=7,  fontName="Helvetica-Bold",   textColor=C_GRAY,   leading=10)
    notes_value  = ParagraphStyle("notes_value",  fontSize=9,  fontName="Helvetica",        textColor=C_BLACK,  leading=12)
    sig_name     = ParagraphStyle("sig_name",     fontSize=9,  fontName="Helvetica-Bold",   textColor=C_BLACK,  leading=12, alignment=TA_RIGHT)
    sig_meta     = ParagraphStyle("sig_meta",     fontSize=7,  fontName="Helvetica",        textColor=C_GRAY,   leading=10, alignment=TA_RIGHT)
    footer_text  = ParagraphStyle("footer_text",  fontSize=6,  fontName="Helvetica-Oblique",textColor=C_GRAY,   leading=9)
    footer_id    = ParagraphStyle("footer_id",    fontSize=6,  fontName="Helvetica",        textColor=C_GRAY,   leading=9,  alignment=TA_RIGHT)
    section_hdr  = ParagraphStyle("section_hdr",  fontSize=8,  fontName="Helvetica-Bold",   textColor=C_GRAY,   leading=11)
    allergy_text = ParagraphStyle("allergy_text", fontSize=8,  fontName="Helvetica-Bold",   textColor=C_RED,    leading=11)
    telugu_detail = ParagraphStyle(
        "telugu_detail",
        fontSize=8,
        fontName=_TELUGU_FONT,
        textColor=C_DARK,
        leading=12,
    )

    return dict(
        clinic_name=clinic_name, doctor_name=doctor_name, doctor_meta=doctor_meta,
        rx_symbol=rx_symbol, label=label, value=value, diag_label=diag_label,
        diag_value=diag_value, drug_name=drug_name, drug_detail=drug_detail,
        drug_dur=drug_dur, drug_num=drug_num, notes_label=notes_label,
        notes_value=notes_value, sig_name=sig_name, sig_meta=sig_meta,
        footer_text=footer_text, footer_id=footer_id, section_hdr=section_hdr,
        allergy_text=allergy_text, telugu_detail=telugu_detail,
    )


def generate_prescription_pdf(
    doctor: Doctor,
    patient: Patient,
    clinic: Clinic,
    prescription: Prescription,
    structured: StructuredPrescription,
    approved_at: datetime,
    known_allergies: list[str] | None = None,
) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A5,
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=12*mm, bottomMargin=12*mm,
    )

    s = _styles()
    W = A5[0] - 24*mm   # usable width

    story = []

    # ── HEADER ────────────────────────────────────────────────
    addr_parts = [clinic.address_line1]
    if clinic.address_line2:
        addr_parts.append(clinic.address_line2)
    addr_parts.append(f"{clinic.city}, {clinic.state} {clinic.pincode}".strip())
    clinic_addr = ", ".join(p for p in addr_parts if p)
    mci_line = f"MCI Reg: {doctor.mci_number}"
    if clinic.phone:
        mci_line += f"  ·  Ph: {clinic.phone}"

    left_col = [
        Paragraph(clinic.name, s["clinic_name"]),
        Spacer(1, 2),
        Paragraph(f"{doctor.name}, {doctor.qualifications}", s["doctor_name"]),
        Paragraph(mci_line, s["doctor_meta"]),
        Paragraph(clinic_addr, s["doctor_meta"]),
    ]
    right_col = [Paragraph("℞", s["rx_symbol"])]

    header_data = [[left_col, right_col]]
    header_table = Table(header_data, colWidths=[W * 0.82, W * 0.18])
    header_table.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_BLACK, spaceAfter=6))

    # ── PATIENT INFO ──────────────────────────────────────────
    approved_at_ist = approved_at.astimezone(_IST)
    date_str = approved_at_ist.strftime("%d %b %Y")
    time_str = approved_at_ist.strftime("%I:%M %p")

    def _patient_cell(label_text, value_text):
        return [Paragraph(label_text, s["label"]), Paragraph(value_text, s["value"])]

    patient_cells = [
        _patient_cell("PATIENT", patient.name or "—"),
        _patient_cell("AGE / SEX", f"{patient.age or '?'}Y / {patient.sex or '?'}"),
    ]
    if patient.phone:
        patient_cells.append(_patient_cell("PHONE", patient.phone))
    patient_cells.append(_patient_cell("DATE", date_str))
    patient_cells.append(_patient_cell("TIME", time_str))

    col_w = W / len(patient_cells)
    pat_table = Table([patient_cells], colWidths=[col_w] * len(patient_cells))
    pat_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), C_BG),
        ("ROUNDEDCORNERS", [4]),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING",   (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(pat_table)
    story.append(Spacer(1, 6))

    if known_allergies:
        allergy_line = "Known allergies: " + ", ".join(known_allergies)
        story.append(Paragraph(allergy_line, s["allergy_text"]))
        story.append(Spacer(1, 6))

    # ── DIAGNOSIS ─────────────────────────────────────────────
    if structured.diagnosis:
        diag_data = [[
            Paragraph("DIAGNOSIS / COMPLAINT", s["diag_label"]),
            Paragraph(structured.diagnosis, s["diag_value"]),
        ]]
        diag_table = Table(diag_data, colWidths=[W])
        diag_table.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), C_BG_DIAG),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ("LINEBEFORETABLE", (0, 0), (0, -1), 3, C_LIME),
        ]))
        story.append(diag_table)
        story.append(Spacer(1, 6))

    # ── MEDICATIONS ───────────────────────────────────────────
    story.append(Paragraph("MEDICATIONS", s["section_hdr"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BG, spaceBefore=2, spaceAfter=4))

    named_meds = [m for m in structured.medications if m.drug_name.strip()]
    for i, med in enumerate(named_meds):
        detail_parts = []
        if med.dosage:
            detail_parts.append(med.dosage)
        if med.frequency:
            detail_parts.append(med.frequency)
        if med.instruction:
            detail_parts.append(med.instruction)
        detail_str = "  —  ".join(detail_parts) if detail_parts else ""

        left_content = [
            Paragraph(med.drug_name.upper(), s["drug_name"]),
        ]
        if detail_str:
            left_content.append(Paragraph(detail_str, s["drug_detail"]))

        row_data = [[
            Paragraph(f"{i+1}.", s["drug_num"]),
            left_content,
            Paragraph(med.duration or "", s["drug_dur"]),
        ]]
        med_table = Table(row_data, colWidths=[10*mm, W - 10*mm - 22*mm, 22*mm])
        med_table.setStyle(TableStyle([
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ("LINEBELOW",    (0, 0), (-1, -1), 0.5, colors.HexColor("#f0f0f0")),
        ]))
        story.append(med_table)

    story.append(Spacer(1, 6))

    # ── PATIENT INSTRUCTIONS ──────────────────────────────────
    patient_instructions = []
    for med in named_meds:
        formatted = format_patient_instruction(
            drug_name=med.drug_name,
            dosage=med.dosage,
            frequency=med.frequency,
            duration=med.duration,
            instruction=med.instruction
        )
        patient_instructions.append({
            "drug_name": med.drug_name,
            "dosage": med.dosage,
            "instruction_english": formatted["english"],
            "instruction_telugu": formatted["telugu"]
        })
    
    if patient_instructions:
        if _TELUGU_FONT == "NotoSansTelugu":
            patient_section_title = (
                'PATIENT INSTRUCTIONS  |  '
                '<font name="NotoSansTelugu">రోగి సూచనలు</font>'
            )
        else:
            patient_section_title = "PATIENT INSTRUCTIONS"
        story.append(Paragraph(patient_section_title, s["section_hdr"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=C_BG, spaceBefore=2, spaceAfter=4))
        
        for i, med in enumerate(patient_instructions):
            patient_instr_content = [
                Paragraph(f"{med['drug_name'].upper()} {med['dosage']}", s["drug_name"]),
                Paragraph(med['instruction_english'], s["drug_detail"]),
                Paragraph(med['instruction_telugu'], s["telugu_detail"]),
            ]
            
            row_data = [[
                Paragraph(f"{i+1}.", s["drug_num"]),
                patient_instr_content,
            ]]
            patient_instr_table = Table(row_data, colWidths=[10*mm, W - 10*mm])
            patient_instr_table.setStyle(TableStyle([
                ("VALIGN",       (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING",  (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING",   (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
                ("LINEBELOW",    (0, 0), (-1, -1), 0.5, colors.HexColor("#f0f0f0")),
            ]))
            story.append(patient_instr_table)
        
        story.append(Spacer(1, 6))

    # ── ADVICE / FOLLOW-UP ────────────────────────────────────
    if structured.advice or structured.follow_up:
        notes_content = []
        if structured.advice:
            notes_content += [
                Paragraph("ADVICE", s["notes_label"]),
                Paragraph(structured.advice, s["notes_value"]),
            ]
        if structured.follow_up:
            if structured.advice:
                notes_content.append(Spacer(1, 4))
            notes_content += [
                Paragraph("FOLLOW-UP", s["notes_label"]),
                Paragraph(structured.follow_up, s["notes_value"]),
            ]

        notes_data = [[notes_content]]
        notes_table = Table(notes_data, colWidths=[W])
        notes_table.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), C_BG),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING",   (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
            ("ROUNDEDCORNERS", [4]),
        ]))
        story.append(notes_table)
        story.append(Spacer(1, 8))

    # ── SIGNATURE ─────────────────────────────────────────────
    approved_at_str = approved_at_ist.strftime("%d %b %Y, %I:%M %p IST")
    sig_content = [
        HRFlowable(width="100%", thickness=1, color=C_BLACK, spaceAfter=3),
        Paragraph(doctor.name, s["sig_name"]),
        Paragraph(doctor.qualifications, s["sig_meta"]),
        Paragraph(f"MCI: {doctor.mci_number}", s["sig_meta"]),
        Paragraph(approved_at_str, s["sig_meta"]),
    ]
    sig_data = [["", sig_content]]
    sig_table = Table(sig_data, colWidths=[W * 0.45, W * 0.55])
    sig_table.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    story.append(sig_table)

    # ── FOOTER ────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BG, spaceBefore=8, spaceAfter=4))
    rx_id = str(prescription.id)[:8].upper()
    footer_data = [[
        Paragraph("This prescription was dictated and approved by the doctor. AI assisted documentation only.", s["footer_text"]),
        Paragraph(f"Rx ID: {rx_id}", s["footer_id"]),
    ]]
    footer_table = Table(footer_data, colWidths=[W * 0.72, W * 0.28])
    footer_table.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    story.append(footer_table)

    doc.build(story)
    return buf.getvalue()


def generate_sample_prescription_pdf(
    doctor: Doctor,
    clinic: Clinic,
) -> bytes:
    """Sample PDF with fake patient data for letterhead preview."""
    from datetime import datetime, timezone
    from uuid import uuid4

    sample_patient = Patient(
        id=str(uuid4()),
        clinic_id=clinic.id,
        name="Ravi Kumar",
        age=45,
        sex="M",
        phone="9876543210",
    )
    sample_rx = Prescription(
        id=str(uuid4()),
        clinic_id=clinic.id,
        doctor_id=doctor.id,
        patient_id=sample_patient.id,
        structured_json={},
        status="draft",
    )
    from app.schemas import MedicationItem, StructuredPrescription

    sample_structured = StructuredPrescription(
        medications=[
            MedicationItem(
                drug_name="METFORMIN",
                dosage="500mg",
                frequency="BD",
                duration="30 days",
                instruction="After food",
            )
        ],
        diagnosis="Type 2 Diabetes Mellitus",
        advice="Low sugar diet, regular exercise",
        follow_up="Review in 4 weeks",
    )
    return generate_prescription_pdf(
        doctor=doctor,
        patient=sample_patient,
        clinic=clinic,
        prescription=sample_rx,
        structured=sample_structured,
        approved_at=datetime.now(timezone.utc),
    )
