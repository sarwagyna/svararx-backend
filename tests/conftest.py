"""
Shared pytest fixtures — test PostgreSQL, FastAPI client, factories, and mocks.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator, Callable
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# ─── Env must be set before app imports ───────────────────────
os.environ.setdefault("SECRET_KEY", "pytest-secret-key-minimum-32-characters-long")
os.environ.setdefault("SARVAM_API_KEY", "test-sarvam-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5433/svararx_test",
)
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

from app.config import get_settings
from app.core.security import create_access_token
import app.core.security as security_module
from app.db_base import Base
from app.database import get_db
from app.main import app
from app.models import Clinic, Doctor, DoctorClinic, Drug, Patient, Prescription, PrescriptionItem
from app.ml.drug_name_corrector import invalidate_drug_index
from app.services import drug_correction
from app.services import stt_service

get_settings.cache_clear()
security_module._SECRET_KEY = None

def _make_test_engine():
    """Fresh connections per checkout — avoids asyncpg loop conflicts in pytest."""
    return create_async_engine(
        os.environ["DATABASE_URL"],
        echo=False,
        poolclass=NullPool,
        connect_args={"timeout": 2},
    )


TEST_ENGINE = _make_test_engine()


async def _probe_postgres() -> bool:
    engine = _make_test_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def postgres_available() -> bool:
    """Check test Postgres once per session to avoid repeated connection timeouts."""
    return asyncio.run(_probe_postgres())


@pytest.fixture(scope="session", autouse=True)
def dispose_test_engine():
    yield
    asyncio.run(TEST_ENGINE.dispose())


TestSessionLocal = async_sessionmaker(
    TEST_ENGINE,
    class_=AsyncSession,
    expire_on_commit=False,
)

VALID_FREQUENCIES = {"OD", "BD", "TDS", "QID"}


def _reset_caches() -> None:
    drug_correction.invalidate_drug_cache()
    stt_service.invalidate_drug_cache()
    invalidate_drug_index()


@pytest.fixture(autouse=True)
def reset_secret_key_cache():
    security_module._SECRET_KEY = None
    yield
    security_module._SECRET_KEY = None


@pytest_asyncio.fixture
async def test_database(postgres_available: bool) -> AsyncGenerator[None, None]:
    """Ensure extensions and schema exist before each test."""
    if not postgres_available:
        pytest.skip("PostgreSQL test database unavailable")

    async with TEST_ENGINE.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_patients_trgm
                ON patients USING gin (name gin_trgm_ops)
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_patients_name_fts
                ON patients USING gin (to_tsvector('simple', name))
                """
            )
        )
    yield


@pytest_asyncio.fixture
async def db_session(test_database) -> AsyncGenerator[AsyncSession, None]:
    """Per-test DB session with table truncation after each test."""
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()

    async with TestSessionLocal() as cleanup:
        for table in (
            "corrections",
            "consultation_attachments",
            "vitals",
            "prescription_items",
            "prescriptions",
            "consultations",
            "patient_condition_suggestions",
            "patient_conditions",
            "patient_allergies",
            "patients",
            "doctor_clinics",
            "doctors",
            "clinics",
            "drugs",
        ):
            await cleanup.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        await cleanup.commit()
    _reset_caches()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client wired to the test database."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def make_clinic(db_session: AsyncSession) -> Callable[..., Any]:
    async def _make(**overrides) -> Clinic:
        data = {
            "name": "Raju Clinic",
            "address_line1": "Main Road",
            "city": "Ongole",
            "state": "Andhra Pradesh",
            "pincode": "523001",
            "phone": "+919876543210",
        }
        data.update(overrides)
        clinic = Clinic(**data)
        db_session.add(clinic)
        await db_session.flush()
        return clinic

    return _make


@pytest_asyncio.fixture
async def make_doctor(db_session: AsyncSession, make_clinic) -> Callable[..., Any]:
    async def _make(*, role: str = "admin", clinic: Clinic | None = None, **overrides) -> tuple[Doctor, Clinic]:
        clinic_obj = clinic or await make_clinic()
        doctor_data = {
            "name": "Dr. Anand Raju",
            "qualifications": "MBBS, MD",
            "mci_number": f"MCI-{uuid4().hex[:8]}",
            "speciality": "General Practitioner",
            "pin_hash": "unused",
            "is_active": True,
        }
        doctor_data.update(overrides)
        doctor = Doctor(**doctor_data)
        db_session.add(doctor)
        await db_session.flush()
        db_session.add(
            DoctorClinic(
                doctor_id=doctor.id,
                clinic_id=clinic_obj.id,
                role=role,
                is_active=True,
            )
        )
        await db_session.commit()
        await db_session.refresh(doctor)
        await db_session.refresh(clinic_obj)
        return doctor, clinic_obj

    return _make


@pytest_asyncio.fixture
async def make_patient(db_session: AsyncSession, make_doctor) -> Callable[..., Any]:
    async def _make(*, doctor: Doctor | None = None, clinic: Clinic | None = None, **overrides) -> Patient:
        if doctor is None or clinic is None:
            doctor, clinic = await make_doctor()
        data = {
            "clinic_id": clinic.id,
            "created_by_doctor_id": doctor.id,
            "name": "Rama Rao",
            "age": 45,
            "sex": "M",
            "phone": "+919988776655",
            "is_active": True,
        }
        data.update(overrides)
        patient = Patient(**data)
        db_session.add(patient)
        await db_session.commit()
        await db_session.refresh(patient)
        return patient

    return _make


@pytest_asyncio.fixture
async def make_prescription(db_session: AsyncSession, make_patient) -> Callable[..., Any]:
    async def _make(*, patient: Patient | None = None, **overrides) -> Prescription:
        if patient is None:
            patient = await make_patient()
        structured = overrides.pop(
            "structured_json",
            {
                "medications": [
                    {
                        "drug_name": "METFORMIN",
                        "dosage": "500mg",
                        "frequency": "BD",
                        "duration": "30 days",
                        "instruction": "after food",
                    }
                ],
                "diagnosis": "Type 2 diabetes",
                "advice": "",
                "follow_up": "",
                "same_as_last_time": False,
            },
        )
        data = {
            "clinic_id": patient.clinic_id,
            "doctor_id": patient.created_by_doctor_id,
            "patient_id": patient.id,
            "structured_json": structured,
            "status": "approved",
            "approved_at": datetime.now(timezone.utc),
            "raw_transcription": "Metformin twice daily",
        }
        data.update(overrides)
        rx = Prescription(**data)
        db_session.add(rx)
        await db_session.flush()
        for i, med in enumerate(structured.get("medications", [])):
            db_session.add(
                PrescriptionItem(
                    prescription_id=rx.id,
                    drug_name=med["drug_name"].upper(),
                    dosage=med.get("dosage", ""),
                    frequency=med.get("frequency", ""),
                    duration=med.get("duration", ""),
                    instruction=med.get("instruction", ""),
                    sort_order=i,
                )
            )
        await db_session.commit()
        await db_session.refresh(rx)
        return rx

    return _make


@pytest_asyncio.fixture
async def make_drug(db_session: AsyncSession) -> Callable[..., Any]:
    async def _make(**overrides) -> Drug:
        data = {
            "brand_name": "Metformin",
            "generic_name": "Metformin",
            "category": "Antidiabetic",
            "schedule": "H",
            "common_dosages": ["500mg"],
            "standard_frequencies": ["BD"],
            "typical_duration": "30 days",
            "phonetic_variants": ["met for min"],
            "is_active": True,
        }
        data.update(overrides)
        drug = Drug(**data)
        db_session.add(drug)
        await db_session.commit()
        await db_session.refresh(drug)
        _reset_caches()
        return drug

    return _make


@pytest_asyncio.fixture
async def seed_common_drugs(db_session: AsyncSession) -> None:
    drugs = [
        ("Metformin", "Metformin"),
        ("Atorvastatin", "Atorvastatin"),
        ("Paracetamol", "Paracetamol"),
        ("Azithromycin", "Azithromycin"),
        ("Amlodipine", "Amlodipine"),
        ("Pantoprazole", "Pantoprazole"),
        ("Cetirizine", "Cetirizine"),
        ("Omeprazole", "Omeprazole"),
        ("Amoxicillin", "Amoxicillin"),
    ]
    for brand, generic in drugs:
        db_session.add(
            Drug(
                brand_name=brand,
                generic_name=generic,
                category="General",
                schedule="H",
                is_active=True,
            )
        )
    await db_session.commit()
    _reset_caches()


@pytest.fixture
def valid_token() -> Callable[[str, str], str]:
    """Generate a real HS256 access token for a doctor/clinic pair."""

    def _token(doctor_id: str, clinic_id: str) -> str:
        return create_access_token(doctor_id, clinic_id)

    return _token


@pytest_asyncio.fixture
async def auth_headers(make_doctor, valid_token) -> tuple[dict[str, str], Doctor, Clinic]:
    doctor, clinic = await make_doctor()
    token = valid_token(doctor.id, clinic.id)
    return {"Authorization": f"Bearer {token}"}, doctor, clinic


@pytest.fixture
def mock_whisper_response(mocker):
    def _apply(transcript: str):
        mock_client = mocker.AsyncMock()
        mock_response = mocker.Mock()
        mock_response.text = transcript
        mock_client.audio.transcriptions.create = mocker.AsyncMock(return_value=mock_response)
        return mocker.patch("app.api.transcribe.AsyncOpenAI", return_value=mock_client)

    return _apply


@pytest.fixture
def mock_rx_groq(mocker):
    """Mock Groq in rx_structurer with a canned LLM JSON payload."""

    def _apply(llm_payload: dict):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps(llm_payload)))
        ]
        mock_client.chat.completions.create.return_value = mock_response
        return mocker.patch("app.services.rx_structurer.Groq", return_value=mock_client)

    return _apply


@pytest.fixture
def mock_groq_response(mocker):
    def _apply(structured: dict):
        output = {
            "medications": structured.get("medications", []),
            "diagnosis": structured.get("diagnosis", ""),
            "advice": structured.get("advice", ""),
            "follow_up": structured.get("follow_up", ""),
            "incomplete_fields": structured.get("incomplete_fields", []),
            "same_as_last_time": structured.get("same_as_last_time", False),
            "parse_error": False,
        }
        return mocker.patch(
            "app.api.transcribe_and_structure.structure_prescription",
            return_value=output,
        )

    return _apply


@pytest.fixture
def mock_sarvam_response(mocker):
    def _apply(transcript: str):
        return mocker.patch(
            "app.api.transcribe_and_structure.transcribe_audio",
            new=mocker.AsyncMock(
                return_value={
                    "raw": transcript,
                    "corrected": transcript,
                    "corrections": [],
                    "low_confidence": [],
                }
            ),
        )

    return _apply


@pytest_asyncio.fixture
async def unit_client():
    """HTTP client without PostgreSQL — for mocked service tests."""
    from unittest.mock import AsyncMock

    async def override_get_db():
        session = AsyncMock()
        yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def mock_redis(mocker):
    import fakeredis

    import app.services.redis_client as redis_client_module

    fake = fakeredis.FakeRedis(decode_responses=False)
    redis_client_module._client = None
    mocker.patch.object(redis_client_module, "get_redis", return_value=fake)
    mocker.patch("redis.from_url", return_value=fake)
    mocker.patch("redis.Redis.from_url", return_value=fake)
    yield fake
    redis_client_module._client = None
