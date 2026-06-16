"""
Pydantic v2 request/response schemas for SvaraRx API.
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


# ─── Transcription ────────────────────────────────────────────
class TranscribeResponse(BaseModel):
    transcription: str
    confidence: Optional[float] = None
    corrected: bool = False  # True if self-correction was applied
    original: Optional[str] = None  # Pre-correction text if corrected=True


# ─── Structuring ──────────────────────────────────────────────
class MedicationItem(BaseModel):
    drug_name: str = Field(description="Drug name in CAPITALS")
    dosage: str = Field(default="", description="Dosage — blank if not stated")
    frequency: str = Field(default="", description="OD / BD / TDS / QID / SOS")
    duration: str = Field(default="")
    instruction: str = Field(default="")
    # Drug correction metadata
    corrected_from: Optional[str] = None
    correction_confidence: Optional[float] = None
    flagged: bool = False  # True if below confidence threshold or allergy match
    allergy_drug: Optional[str] = None
    allergy_warning: Optional[str] = None  # reaction text for allergy banner


class StructuredPrescription(BaseModel):
    medications: list[MedicationItem] = []
    diagnosis: str = ""
    advice: str = ""
    follow_up: str = ""
    same_as_last_time: bool = False  # LLM returned this flag
    chief_complaint: Optional[str] = None
    chief_complaint_tags: list[str] = Field(default_factory=list)


class StructureResponse(BaseModel):
    structured: StructuredPrescription
    raw_llm_output: Optional[str] = None


# ─── Patient ──────────────────────────────────────────────────
class PatientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    age: int = Field(ge=1, le=119)
    sex: str = Field(default="M", pattern="^(M|F|O|Other)$")
    phone: str = Field(min_length=10, max_length=15)
    abha_id: Optional[str] = Field(default=None, max_length=20)


class PatientUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    age: Optional[int] = Field(default=None, ge=1, le=119)
    sex: Optional[str] = Field(default=None, pattern="^(M|F|O|Other)$")
    phone: Optional[str] = Field(default=None, min_length=10, max_length=15)
    abha_id: Optional[str] = Field(default=None, max_length=20)


class PatientOut(BaseModel):
    id: str
    name: str
    age: int
    sex: str
    phone: Optional[str]
    abha_id: Optional[str] = None
    created_at: datetime
    allergy_count: int = 0

    model_config = {"from_attributes": True}


class PatientAllergyCreate(BaseModel):
    drug_name: str = Field(min_length=1, max_length=200)
    reaction: Optional[str] = Field(default=None, max_length=500)
    severity: str = Field(default="unknown", pattern="^(mild|moderate|severe|unknown)$")


class PatientAllergyOut(BaseModel):
    id: str
    patient_id: str
    drug_name: str
    drug_generic: Optional[str] = None
    reaction: Optional[str] = None
    severity: str
    reported_at: datetime

    model_config = {"from_attributes": True}


class PatientConditionCreate(BaseModel):
    condition_name: str = Field(min_length=1, max_length=200)
    condition_code: Optional[str] = Field(default=None, max_length=10)
    diagnosed_at: Optional[date] = None


class PatientConditionUpdate(BaseModel):
    status: Optional[str] = Field(
        default=None,
        pattern="^(active|resolved|monitoring)$",
    )


class PatientConditionOut(BaseModel):
    id: str
    patient_id: str
    condition_name: str
    condition_code: Optional[str] = None
    diagnosed_at: Optional[date] = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PatientConditionSuggestionOut(BaseModel):
    id: str
    patient_id: str
    condition_name: str
    evidence_count: int
    status: str
    suggested_at: datetime

    model_config = {"from_attributes": True}


class AllergyAcknowledgment(BaseModel):
    drug_name: str
    allergy_drug: str
    reaction: Optional[str] = None


class PatientRecentOut(PatientOut):
    last_visit_at: Optional[datetime] = None


class PatientSearchOut(BaseModel):
    id: str
    full_name: str
    age: int
    gender: str
    phone: Optional[str] = None
    last_visit_date: Optional[datetime] = None
    prescription_count: int = 0


class PatientListItem(PatientOut):
    last_visit_at: Optional[datetime] = None
    prescription_count: int = 0


class PaginatedPatientList(BaseModel):
    items: list[PatientListItem]
    total: int
    page: int
    limit: int


class LinkPatientRequest(BaseModel):
    patient_id: str


# ─── Doctor Auth ─────────────────────────────────────────────
class DoctorRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    qualifications: str = Field(min_length=1, max_length=255)
    mci_number: str = Field(min_length=1, max_length=100)
    speciality: str = "General Practitioner"
    clinic_name: Optional[str] = None
    clinic_address_line1: Optional[str] = None
    clinic_address_line2: Optional[str] = None
    clinic_city: Optional[str] = None
    clinic_state: Optional[str] = None
    clinic_pincode: Optional[str] = None
    clinic_phone: Optional[str] = None


class DoctorRegisterResponse(BaseModel):
    id: str
    name: str
    qualifications: str
    mci_number: str
    speciality: str
    clinic_id: Optional[str] = None
    clinic_role: Optional[str] = None
    onboarding_step: int = 0
    onboarding_completed: bool = False

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class ClinicMembershipOut(BaseModel):
    clinic_id: str
    clinic_name: str
    role: str


class SwitchClinicRequest(BaseModel):
    clinic_id: str


class ClinicDoctorCard(BaseModel):
    id: str
    name: str
    speciality: str
    has_pin: bool


class ClinicUxContext(BaseModel):
    clinic_id: str
    clinic_name: str
    plan: str
    doctor_count: int
    is_solo: bool
    practice_mode: str = "solo"
    uses_clinic_layer: bool = False
    membership_role: str
    default_path: str
    requires_doctor_selection: bool
    requires_pin_to_approve: bool
    can_prescribe_directly: bool
    active_doctor_id: str
    active_doctor_name: str
    doctors: list[ClinicDoctorCard] = []


class SetPinRequest(BaseModel):
    pin: str = Field(min_length=4, max_length=4)


class VerifyPinRequest(BaseModel):
    doctor_id: str
    pin: str = Field(min_length=4, max_length=4)
    prescription_id: Optional[str] = None


class VerifyPinResponse(BaseModel):
    approval_token: str
    doctor_id: str
    doctor_name: str
    expires_in_seconds: int = 300


class ActAsDoctorRequest(BaseModel):
    doctor_id: str
    pin: str = Field(min_length=4, max_length=4)


class VoiceCaptureResponse(BaseModel):
    recording_id: str
    duration_seconds: float


class VoiceTranscribeRequest(BaseModel):
    recording_id: str


class VoiceTranscribeResponse(BaseModel):
    transcript: str
    engine_used: str
    confidence: float
    corrections_made: int
    duration_ms: int


# ─── Prescription Approval ────────────────────────────────────
class ApproveRequest(BaseModel):
    patient_id: str
    raw_transcription: Optional[str] = None
    structured: StructuredPrescription
    prescription_id: Optional[str] = None
    consultation_id: Optional[str] = None
    allergy_acknowledgments: list[AllergyAcknowledgment] = []
    approval_token: Optional[str] = None
    approving_doctor_id: Optional[str] = None


class ApproveResponse(BaseModel):
    prescription_id: str
    pdf_url: Optional[str] = None  # S3 pre-signed URL or base64
    pdf_base64: Optional[str] = None
    status: str
    pdf_generation_time_ms: int
    upload_time_ms: int
    approval_time_ms: int
    sla_exceeded: bool = False


# ─── Prescription History ─────────────────────────────────────
class PrescriptionSummary(BaseModel):
    id: str
    created_at: datetime
    approved_at: Optional[datetime]
    status: str
    diagnosis: Optional[str]
    pdf_s3_key: Optional[str]
    item_count: int

    model_config = {"from_attributes": True}


class HistoryDrugItem(BaseModel):
    name: str
    dose: str = ""
    frequency: str = ""


class VisitHistoryItem(BaseModel):
    id: str
    created_at: datetime
    chief_complaint: Optional[str] = None
    diagnosis: Optional[str] = None
    drugs: list[HistoryDrugItem] = []
    pdf_url: Optional[str] = None
    follow_up_date: Optional[date] = None
    status: str = "approved"
    consultation_id: Optional[str] = None


class PaginatedVisitHistory(BaseModel):
    items: list[VisitHistoryItem]
    total: int
    page: int
    limit: int


class VisitHistoryDetail(BaseModel):
    id: str
    created_at: datetime
    approved_at: Optional[datetime] = None
    status: str
    chief_complaint: Optional[str] = None
    diagnosis: Optional[str] = None
    drugs: list[HistoryDrugItem] = []
    transcript: Optional[str] = None
    advice: Optional[str] = None
    follow_up: Optional[str] = None
    follow_up_date: Optional[date] = None
    pdf_url: Optional[str] = None
    raw_transcription: Optional[str] = None
    consultation_id: Optional[str] = None


# ─── Consultations ────────────────────────────────────────────
class ConsultationStartRequest(BaseModel):
    patient_id: Optional[str] = None
    chief_complaint: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    visit_type: str = Field(default="new", pattern="^(new|follow_up|emergency)$")


class ConsultationCompleteRequest(BaseModel):
    prescription_id: Optional[str] = None


class ConsultationOut(BaseModel):
    id: str
    doctor_id: str
    patient_id: Optional[str] = None
    chief_complaint: Optional[str] = None
    chief_complaint_tags: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: Optional[datetime] = None
    prescription_id: Optional[str] = None

    model_config = {"from_attributes": True}


# ─── Admin ────────────────────────────────────────────────────
class ClinicInfo(BaseModel):
    id: str
    name: str
    address_line1: str
    address_line2: Optional[str]
    city: str
    state: str
    pincode: str
    phone: Optional[str]
    plan: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class DoctorInfo(BaseModel):
    id: str
    name: str
    qualifications: str
    mci_number: str
    speciality: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminOverview(BaseModel):
    clinic: ClinicInfo
    doctors: list[DoctorInfo]
    total_patients: int
    total_prescriptions: int
    total_drugs: int
    prescriptions_this_month: int


# ─── Dashboard ───────────────────────────────────────────────
class RecentPrescription(BaseModel):
    id: str
    patient_id: Optional[str] = None
    patient_name: str
    diagnosis: Optional[str]
    created_at: datetime
    approved_at: Optional[datetime]
    status: str
    item_count: int
    doctor_name: Optional[str] = None


class ClinicDoctorStats(BaseModel):
    id: str
    name: str
    speciality: str
    role: str
    has_pin: bool
    total_prescriptions: int
    today_prescriptions: int
    week_prescriptions: int
    total_patients: int


class ClinicDashboardSummary(BaseModel):
    clinic_id: str
    clinic_name: str
    plan: str
    doctor_count: int
    practice_mode: str
    doctors: list[ClinicDoctorStats] = []


class DailyCount(BaseModel):
    date: str
    label: str
    count: int


class PatientSexCount(BaseModel):
    sex: str
    label: str
    count: int


class DashboardAnalytics(BaseModel):
    rx_by_day: list[DailyCount]
    patients_by_day: list[DailyCount]
    week_prescriptions: int
    last_week_prescriptions: int
    new_patients_week: int
    last_week_new_patients: int
    patients_visited_week: int
    total_active_patients: int
    patients_with_allergies: int
    patient_sex_breakdown: list[PatientSexCount]
    draft_prescriptions: int
    completed_prescriptions: int
    avg_medications_per_rx: float


class DashboardData(BaseModel):
    total_patients: int
    total_prescriptions: int
    today_prescriptions: int
    recent_prescriptions: list[RecentPrescription]
    analytics: DashboardAnalytics
    clinic: ClinicDashboardSummary | None = None


# ─── Prescription Detail ─────────────────────────────────────
class PrescriptionDetail(BaseModel):
    id: str
    patient_id: Optional[str] = None
    created_at: datetime
    approved_at: Optional[datetime]
    status: str
    structured: StructuredPrescription
    pdf_s3_key: Optional[str]
    raw_transcription: Optional[str]


# ─── Drug Search ──────────────────────────────────────────────
class DrugResult(BaseModel):
    id: str
    brand_name: str
    generic_name: str
    category: Optional[str]
    medicine_type: str = "allopathic"
    score: Optional[float] = None  # fuzzy match score


# ─── Health ───────────────────────────────────────────────────
class HealthResponse(BaseModel):
    status: str
    version: str = "0.1.0"


class ReadinessResponse(BaseModel):
    status: str
    database: str
    redis: str
    version: str = "0.1.0"


# ─── Onboarding ───────────────────────────────────────────────
class OnboardingStatusResponse(BaseModel):
    step: int
    completed: bool
    practice_mode: str = "solo"
    is_solo_onboarding: bool = True
    needs_practice_mode_choice: bool = True
    full_name: str = ""
    qualifications: str = ""
    mci_reg_number: str = ""
    state_council_reg: str = ""
    specialization: str = ""
    clinic_name: str = ""
    clinic_address: str = ""
    clinic_city: str = ""
    clinic_state: str = "Andhra Pradesh"
    clinic_pin: str = ""
    clinic_phone: str = ""
    clinic_logo_url: str | None = None
    signature_url: str | None = None
    referral_code: str = ""


class OnboardingStep1Request(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    qualifications: str = Field(min_length=1, max_length=200)
    mci_reg_number: str = Field(min_length=1, max_length=50)
    state_council_reg: str = Field(default="", max_length=50)
    specialization: str = Field(min_length=1, max_length=100)
    referral_code: str = Field(default="", max_length=50)
    practice_mode: str = Field(default="solo", pattern="^(solo|clinic)$")


class PracticeModeRequest(BaseModel):
    practice_mode: str = Field(pattern="^(solo|clinic)$")


class OnboardingSoloSetupRequest(BaseModel):
    """Minimal practice info for solo doctors — clinic layer stays invisible in UX."""
    practice_city: str = Field(default="", max_length=100)
    practice_phone: str = Field(default="", max_length=20)
    approval_pin: str = Field(default="", min_length=0, max_length=4)


class OnboardingStep2Request(BaseModel):
    clinic_name: str = Field(min_length=1, max_length=255)
    clinic_address: str = Field(min_length=1, max_length=500)
    clinic_city: str = Field(min_length=1, max_length=100)
    clinic_state: str = Field(default="Andhra Pradesh", max_length=100)
    clinic_pin: str = Field(min_length=4, max_length=10)
    clinic_phone: str = Field(default="", max_length=20)


class OnboardingStepResponse(BaseModel):
    step: int
    completed: bool


# ─── Clinic admin ─────────────────────────────────────────────
class ClinicSettingsResponse(BaseModel):
    clinic_id: str
    clinic_name: str
    clinic_address: str
    clinic_address_line2: str = ""
    clinic_city: str
    clinic_state: str
    clinic_pin: str
    clinic_phone: str = ""
    plan: str = "free"


class ClinicSettingsUpdateRequest(BaseModel):
    clinic_name: str = Field(min_length=1, max_length=255)
    clinic_address: str = Field(min_length=1, max_length=500)
    clinic_address_line2: str = Field(default="", max_length=255)
    clinic_city: str = Field(min_length=1, max_length=100)
    clinic_state: str = Field(default="Andhra Pradesh", max_length=100)
    clinic_pin: str = Field(min_length=4, max_length=10)
    clinic_phone: str = Field(default="", max_length=20)


class CreateClinicDoctorRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    qualifications: str = Field(min_length=1, max_length=200)
    mci_reg_number: str = Field(min_length=1, max_length=50)
    state_council_reg: str = Field(default="", max_length=50)
    specialization: str = Field(min_length=1, max_length=100)
    approval_pin: str = Field(min_length=4, max_length=4)
    role: str = Field(default="doctor", pattern="^(doctor|compounder)$")


# ─── Doctor Profile (Settings) ────────────────────────────────
class DoctorMeResponse(BaseModel):
    id: str
    full_name: str
    qualifications: str
    mci_reg_number: str
    state_council_reg: str | None = None
    specialization: str
    languages: list[str] = Field(default_factory=lambda: ["Telugu", "English"])
    clinic_name: str | None = None
    clinic_address: str | None = None
    clinic_address_line2: str | None = None
    clinic_city: str | None = None
    clinic_state: str | None = None
    clinic_pin: str | None = None
    clinic_phone: str | None = None
    clinic_logo_url: str | None = None
    signature_url: str | None = None
    onboarding_completed: bool = False
    onboarding_step: int = 0
    subscription_tier: str = "free"
    subscription_expires_at: datetime | None = None
    practice_mode: str = "solo"
    has_approval_pin: bool = False


class ReferralStatsResponse(BaseModel):
    total_referrals: int = 0
    paid_referrals: int = 0
    pending_referrals: int = 0
    earnings_inr: int = 0
    reward_per_referral_inr: int = 500


class UpgradeToClinicRequest(BaseModel):
    clinic_name: str = Field(min_length=1, max_length=255)
    clinic_address: str = Field(min_length=1, max_length=500)
    clinic_city: str = Field(min_length=1, max_length=100)
    clinic_state: str = Field(default="Andhra Pradesh", max_length=100)
    clinic_pin: str = Field(min_length=4, max_length=10)
    clinic_phone: str = Field(default="", max_length=20)
    approval_pin: str = Field(default="", max_length=4)


class DoctorMeUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    qualifications: str | None = Field(default=None, min_length=1, max_length=200)
    mci_reg_number: str | None = Field(default=None, min_length=1, max_length=50)
    state_council_reg: str | None = Field(default=None, max_length=50)
    specialization: str | None = Field(default=None, min_length=1, max_length=100)
    languages: list[str] | None = None
    clinic_name: str | None = Field(default=None, min_length=1, max_length=255)
    clinic_address: str | None = Field(default=None, min_length=1, max_length=500)
    clinic_city: str | None = Field(default=None, min_length=1, max_length=100)
    clinic_state: str | None = Field(default=None, max_length=100)
    clinic_pin: str | None = Field(default=None, min_length=4, max_length=10)
    clinic_phone: str | None = Field(default=None, max_length=20)


class UploadUrlResponse(BaseModel):
    signature_url: str | None = None
    clinic_logo_url: str | None = None


class LetterheadUpdateRequest(BaseModel):
    clinic_name: str = Field(min_length=1, max_length=255)
    clinic_address: str = Field(min_length=1, max_length=500)
    clinic_address_line2: str = Field(default="", max_length=255)
    clinic_city: str = Field(min_length=1, max_length=100)
    clinic_state: str = Field(default="Andhra Pradesh", max_length=100)
    clinic_pin: str = Field(min_length=4, max_length=10)
    clinic_phone: str = Field(default="", max_length=20)


class LetterheadResponse(BaseModel):
    clinic_name: str = ""
    clinic_address: str = ""
    clinic_address_line2: str = ""
    clinic_city: str = ""
    clinic_state: str = "Andhra Pradesh"
    clinic_pin: str = ""
    clinic_phone: str = ""
    clinic_logo_url: str | None = None
    doctor_name: str = ""
    qualifications: str = ""
    mci_reg_number: str = ""
    state_council_reg: str | None = None
    signature_url: str | None = None


class LetterheadPreviewResponse(BaseModel):
    pdf_base64: str
    filename: str = "letterhead-preview.pdf"


# ─── Vitals ───────────────────────────────────────────────────
class VitalFlag(BaseModel):
    flag: str


class VitalCreate(BaseModel):
    patient_id: str
    consultation_id: Optional[str] = None
    bp_systolic: Optional[int] = Field(default=None, ge=40, le=300)
    bp_diastolic: Optional[int] = Field(default=None, ge=20, le=200)
    weight_kg: Optional[float] = Field(default=None, ge=0.5, le=500)
    blood_sugar_mg_dl: Optional[int] = Field(default=None, ge=20, le=600)
    blood_sugar_type: Optional[str] = Field(default=None, pattern="^(fasting|pp|random)$")
    spo2_percent: Optional[int] = Field(default=None, ge=50, le=100)
    temperature_f: Optional[float] = Field(default=None, ge=90, le=110)
    pulse_bpm: Optional[int] = Field(default=None, ge=20, le=250)
    height_cm: Optional[float] = Field(default=None, ge=30, le=250)
    respiratory_rate: Optional[int] = Field(default=None, ge=4, le=60)


class VitalOut(BaseModel):
    id: str
    consultation_id: Optional[str] = None
    patient_id: str
    doctor_id: str
    bp_systolic: Optional[int] = None
    bp_diastolic: Optional[int] = None
    weight_kg: Optional[float] = None
    blood_sugar_mg_dl: Optional[int] = None
    blood_sugar_type: Optional[str] = None
    spo2_percent: Optional[int] = None
    temperature_f: Optional[float] = None
    pulse_bpm: Optional[int] = None
    height_cm: Optional[float] = None
    respiratory_rate: Optional[int] = None
    recorded_at: datetime
    flags: list[VitalFlag] = []

    model_config = {"from_attributes": True}


class VitalRecordResponse(BaseModel):
    vitals: VitalOut
    flags: list[VitalFlag] = []
