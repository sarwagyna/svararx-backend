"""
Rich consultation / EMR record — AI-first visit documentation beyond the Rx slip.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

VisitType = Literal["new", "follow_up", "emergency"]
RecordStatus = Literal["draft", "approved"]
TimelineEventType = Literal[
    "consultation",
    "ai_summary",
    "prescription",
    "tests_ordered",
    "follow_up",
    "attachment",
    "lab_report",
]


class PatientRecordSection(BaseModel):
    patient_id: Optional[str] = None
    full_name: str = ""
    age: Optional[int] = None
    date_of_birth: Optional[date] = None
    gender: str = ""
    phone: Optional[str] = None
    address: Optional[str] = None
    occupation: Optional[str] = None


class VisitRecordSection(BaseModel):
    visit_id: str
    date_time: datetime
    doctor_name: str = ""
    department_specialty: str = ""
    clinic_name: str = ""
    visit_type: VisitType = "new"


class VitalsRecordSection(BaseModel):
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    bmi: Optional[float] = None
    temperature_f: Optional[float] = None
    bp_systolic: Optional[int] = None
    bp_diastolic: Optional[int] = None
    pulse_bpm: Optional[int] = None
    respiratory_rate: Optional[int] = None
    spo2_percent: Optional[int] = None
    random_blood_sugar_mg_dl: Optional[int] = None
    blood_sugar_type: Optional[str] = None
    recorded_at: Optional[datetime] = None


class HistorySection(BaseModel):
    present_illness: str = ""
    past_medical_history: str = ""
    surgical_history: str = ""
    family_history: str = ""
    allergy_history: str = ""
    current_medications: str = ""


class DiagnosisSection(BaseModel):
    primary: str = ""
    secondary: list[str] = Field(default_factory=list)
    icd_code: Optional[str] = None
    provisional: str = ""


class PrescriptionMedRecord(BaseModel):
    drug_name: str = ""
    strength: str = ""
    dose: str = ""
    frequency: str = ""
    route: str = "oral"
    duration: str = ""
    food_timing: str = ""
    notes: str = ""


class FollowUpSection(BaseModel):
    instructions: str = ""
    next_visit_date: Optional[date] = None


TestResultFlag = Literal["normal", "high", "low", "critical", "unknown"]
AttachmentCategory = Literal["lab_report", "imaging", "document", "other"]


class ClinicalTestResult(BaseModel):
    test_name: str = ""
    value: str = ""
    unit: str = ""
    reference_range: str = ""
    flag: TestResultFlag = "unknown"
    sample_date: Optional[date] = None
    lab_name: str = ""
    notes: str = ""
    source_attachment_id: Optional[str] = None


class TranscriptSection(BaseModel):
    raw: Optional[str] = None
    corrected: Optional[str] = None
    approved: Optional[str] = None


class AttachmentRef(BaseModel):
    id: str = ""
    filename: str = ""
    mime_type: Optional[str] = None
    url: Optional[str] = None
    uploaded_at: Optional[datetime] = None
    category: AttachmentCategory = "other"
    file_size: Optional[int] = None
    ocr_text: Optional[str] = None
    ocr_status: Optional[str] = None


class ConsultationRecordContent(BaseModel):
    """Editable clinical body stored in consultations.record_json."""

    chief_complaints: list[str] = Field(default_factory=list)
    history: HistorySection = Field(default_factory=HistorySection)
    examination_findings: list[str] = Field(default_factory=list)
    diagnosis: DiagnosisSection = Field(default_factory=DiagnosisSection)
    prescription: list[PrescriptionMedRecord] = Field(default_factory=list)
    investigations_ordered: list[str] = Field(default_factory=list)
    clinical_tests: list[ClinicalTestResult] = Field(default_factory=list)
    advice: list[str] = Field(default_factory=list)
    follow_up: FollowUpSection = Field(default_factory=FollowUpSection)


class TimelineEvent(BaseModel):
    type: TimelineEventType
    label: str
    detail: Optional[str] = None
    at: Optional[datetime] = None


class TimelineVisit(BaseModel):
    visit_id: str
    date: date
    events: list[TimelineEvent] = Field(default_factory=list)


class ConsultationRecordOut(BaseModel):
    patient: PatientRecordSection
    visit: VisitRecordSection
    vitals: VitalsRecordSection
    content: ConsultationRecordContent
    ai_summary: Optional[str] = None
    transcripts: TranscriptSection = Field(default_factory=TranscriptSection)
    attachments: list[AttachmentRef] = Field(default_factory=list)
    record_status: RecordStatus = "draft"
    prescription_id: Optional[str] = None
    timeline_preview: list[TimelineEvent] = Field(default_factory=list)


class ConsultationRecordUpdate(BaseModel):
    content: Optional[ConsultationRecordContent] = None
    ai_summary: Optional[str] = None
    approved_transcript: Optional[str] = None
    record_status: Optional[RecordStatus] = None
    visit_type: Optional[VisitType] = None


class ConsultationRecordGenerateRequest(BaseModel):
    transcript: str = Field(min_length=1)
    use_llm: bool = True


class RecordAttachmentOcrResponse(BaseModel):
    attachment_id: str
    ocr_text: str
    clinical_tests: list[ClinicalTestResult] = Field(default_factory=list)
    lab_name: Optional[str] = None
    sample_date: Optional[date] = None
    merged_into_record: bool = False
    record: Optional[ConsultationRecordOut] = None


class PatientTimelineOut(BaseModel):
    patient_id: str
    visits: list[TimelineVisit] = Field(default_factory=list)


class PatientConsultationListItem(BaseModel):
    consultation_id: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    visit_type: VisitType = "new"
    record_status: RecordStatus = "draft"
    chief_complaint: Optional[str] = None
    diagnosis_primary: Optional[str] = None
    prescription_id: Optional[str] = None
    ai_summary: Optional[str] = None
