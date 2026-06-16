"""
Patient chronic condition lookup and prescription-based suggestion logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    PatientCondition,
    PatientConditionSuggestion,
    Prescription,
    PrescriptionItem,
)

DIABETES_DRUGS = ("metformin", "glibenclamide")
HYPERTENSIVE_DRUGS = ("amlodipine", "losartan", "atenolol")
MIN_EVIDENCE_PRESCRIPTIONS = 3

CONDITION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Type 2 Diabetes", DIABETES_DRUGS),
    ("Hypertension", HYPERTENSIVE_DRUGS),
)


@dataclass
class ConditionRecord:
    id: str
    condition_name: str
    condition_code: str | None
    status: str


async def fetch_patient_conditions_for_context(
    db: AsyncSession,
    patient_id: str,
) -> list[str]:
    """Active + monitoring conditions for LLM context."""
    rows = (
        await db.execute(
            select(PatientCondition.condition_name).where(
                PatientCondition.patient_id == patient_id,
                PatientCondition.status.in_(("active", "monitoring")),
            )
        )
    ).all()
    return [row[0] for row in rows]


def format_conditions_prompt(conditions: list[str]) -> str:
    if not conditions:
        return ""
    return ", ".join(conditions)


async def count_prescriptions_with_drug(
    db: AsyncSession,
    patient_id: str,
    drug_patterns: tuple[str, ...],
) -> int:
    """Count distinct approved prescriptions containing any of the drug patterns."""
    lowered = [p.lower() for p in drug_patterns]
    like_clauses = [func.lower(PrescriptionItem.drug_name).contains(p) for p in lowered]

    from sqlalchemy import or_

    stmt = (
        select(func.count(func.distinct(Prescription.id)))
        .select_from(Prescription)
        .join(PrescriptionItem, PrescriptionItem.prescription_id == Prescription.id)
        .where(
            Prescription.patient_id == patient_id,
            Prescription.status == "approved",
            or_(*like_clauses),
        )
    )
    result = await db.execute(stmt)
    return int(result.scalar() or 0)


async def patient_has_active_condition(
    db: AsyncSession,
    patient_id: str,
    condition_name: str,
) -> bool:
    row = (
        await db.execute(
            select(PatientCondition.id).where(
                PatientCondition.patient_id == patient_id,
                PatientCondition.condition_name == condition_name,
                PatientCondition.status.in_(("active", "monitoring")),
            )
        )
    ).first()
    return row is not None


async def upsert_condition_suggestion(
    db: AsyncSession,
    patient_id: str,
    condition_name: str,
    evidence_count: int,
) -> PatientConditionSuggestion | None:
    """Create or refresh a pending suggestion. Returns None if skipped."""
    if await patient_has_active_condition(db, patient_id, condition_name):
        return None

    existing = (
        await db.execute(
            select(PatientConditionSuggestion).where(
                PatientConditionSuggestion.patient_id == patient_id,
                PatientConditionSuggestion.condition_name == condition_name,
                PatientConditionSuggestion.status == "pending",
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.evidence_count = evidence_count
        existing.suggested_at = datetime.now(timezone.utc)
        return existing

    dismissed = (
        await db.execute(
            select(PatientConditionSuggestion.id).where(
                PatientConditionSuggestion.patient_id == patient_id,
                PatientConditionSuggestion.condition_name == condition_name,
                PatientConditionSuggestion.status == "dismissed",
            )
        )
    ).first()
    if dismissed:
        return None

    suggestion = PatientConditionSuggestion(
        patient_id=patient_id,
        condition_name=condition_name,
        evidence_count=evidence_count,
        status="pending",
    )
    db.add(suggestion)
    return suggestion


async def evaluate_patient_suggestions(
    db: AsyncSession,
    patient_id: str,
) -> list[PatientConditionSuggestion]:
    """Run drug-pattern rules and return new/updated pending suggestions."""
    created: list[PatientConditionSuggestion] = []
    for condition_name, drug_patterns in CONDITION_RULES:
        count = await count_prescriptions_with_drug(db, patient_id, drug_patterns)
        if count >= MIN_EVIDENCE_PRESCRIPTIONS:
            suggestion = await upsert_condition_suggestion(
                db, patient_id, condition_name, count
            )
            if suggestion:
                created.append(suggestion)
    return created
