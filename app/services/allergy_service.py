"""
Patient allergy lookup, drug matching, and prescription cross-checking.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Drug, PatientAllergy
from app.schemas import MedicationItem, StructuredPrescription

ALLERGY_MATCH_THRESHOLD = 85


@dataclass
class AllergyRecord:
    id: str
    drug_name: str
    drug_generic: Optional[str]
    reaction: Optional[str]
    severity: str

    def match_names(self) -> list[str]:
        names = [self.drug_name.upper()]
        if self.drug_generic:
            names.append(self.drug_generic.upper())
        return names


async def fetch_patient_allergies(db: AsyncSession, patient_id: str) -> list[AllergyRecord]:
    rows = (
        await db.execute(
            select(PatientAllergy).where(
                PatientAllergy.patient_id == patient_id,
                PatientAllergy.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    return [
        AllergyRecord(
            id=a.id,
            drug_name=a.drug_name,
            drug_generic=a.drug_generic,
            reaction=a.reaction,
            severity=a.severity,
        )
        for a in rows
    ]


def allergies_to_context(allergies: list[AllergyRecord]) -> list[dict[str, Any]]:
    return [
        {
            "drug_name": a.drug_name,
            "drug_generic": a.drug_generic,
            "reaction": a.reaction,
            "severity": a.severity,
        }
        for a in allergies
    ]


def format_allergy_prompt(allergies: list[AllergyRecord]) -> str:
    if not allergies:
        return ""
    parts = []
    for a in allergies:
        label = a.drug_name
        if a.drug_generic and a.drug_generic.upper() != a.drug_name.upper():
            label = f"{a.drug_name} ({a.drug_generic})"
        if a.reaction:
            label = f"{label} — {a.reaction}"
        parts.append(label)
    return "; ".join(parts)


async def resolve_drug_generic(db: AsyncSession, drug_name: str, settings: Settings) -> Optional[str]:
    """Try to match drug_name against the drugs DB and return generic_name."""
    result = await db.execute(select(Drug.brand_name, Drug.generic_name).where(Drug.is_active == True))
    rows = result.all()
    if not rows:
        return None

    choices: list[str] = []
    generic_map: dict[str, str] = {}
    for brand, generic in rows:
        for name in (brand, generic):
            key = name.upper()
            choices.append(key)
            generic_map[key] = generic

    match = process.extractOne(
        drug_name.upper(),
        choices,
        scorer=fuzz.WRatio,
        score_cutoff=settings.drug_match_threshold,
    )
    if not match:
        return None
    matched_key, _, _ = match
    return generic_map.get(matched_key)


def _best_allergy_match(drug_name: str, allergies: list[AllergyRecord]) -> Optional[AllergyRecord]:
    query = drug_name.upper().strip()
    if not query or not allergies:
        return None

    best: Optional[tuple[AllergyRecord, float]] = None
    for allergy in allergies:
        for candidate in allergy.match_names():
            score = fuzz.WRatio(query, candidate)
            if score >= ALLERGY_MATCH_THRESHOLD and (best is None or score > best[1]):
                best = (allergy, score)
    return best[0] if best else None


def check_drug_against_allergies(
    drug_name: str,
    allergies: list[AllergyRecord],
) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Returns (is_match, allergy_drug_name, reaction).
    """
    match = _best_allergy_match(drug_name, allergies)
    if not match:
        return False, None, None
    return True, match.drug_name, match.reaction


def apply_allergy_flags_to_medications(
    medications: list[dict[str, Any]],
    allergies: list[AllergyRecord],
) -> list[dict[str, Any]]:
    """Cross-check medications against allergies; set flagged + allergy fields."""
    if not allergies:
        return medications

    updated = []
    for med in medications:
        drug_name = str(med.get("drug_name", "")).strip()
        if not drug_name:
            updated.append(med)
            continue

        is_match, allergy_drug, reaction = check_drug_against_allergies(drug_name, allergies)
        if is_match:
            med = {
                **med,
                "flagged": True,
                "allergy_drug": allergy_drug,
                "allergy_warning": reaction or "allergic",
            }
        updated.append(med)
    return updated


def apply_allergy_flags_to_prescription(
    structured: StructuredPrescription,
    allergies: list[AllergyRecord],
) -> StructuredPrescription:
    if not allergies:
        return structured

    updated_meds: list[MedicationItem] = []
    for med in structured.medications:
        is_match, allergy_drug, reaction = check_drug_against_allergies(med.drug_name, allergies)
        if is_match:
            updated_meds.append(
                med.model_copy(
                    update={
                        "flagged": True,
                        "allergy_drug": allergy_drug,
                        "allergy_warning": reaction or "allergic",
                    }
                )
            )
        else:
            updated_meds.append(med)

    return structured.model_copy(update={"medications": updated_meds})
