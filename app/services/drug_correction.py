"""
Drug name correction layer using rapidfuzz.
Matches transcribed drug names against the seeded drug list.
- Score >= threshold (default 80): auto-correct
- Score < threshold: flag for doctor review (amber highlight)
"""
from rapidfuzz import process, fuzz
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import Settings
from app.schemas import StructuredPrescription, MedicationItem

# Module-level cache so we don't hit DB on every call
_drug_cache: list[tuple[str, str]] = []  # [(brand_name_upper, original_brand_name)]


async def _load_drug_names(settings: Settings) -> list[tuple[str, str]]:
    """Load drug names from DB into module cache."""
    global _drug_cache
    if _drug_cache:
        return _drug_cache

    # Import here to avoid circular imports
    from app.database import AsyncSessionLocal
    from app.models import Drug

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Drug.brand_name, Drug.generic_name))
        rows = result.all()

    cache = []
    for brand, generic in rows:
        cache.append((brand.upper(), brand))
        cache.append((generic.upper(), generic))

    _drug_cache = cache
    return cache


def invalidate_drug_cache() -> None:
    """Call this after seeding or updating the drugs table."""
    global _drug_cache
    _drug_cache = []


async def correct_drug_names(
    structured: StructuredPrescription,
    settings: Settings,
) -> StructuredPrescription:
    """
    Run fuzzy matching on each medication's drug_name.
    Mutates and returns the structured prescription with correction metadata.
    """
    drug_names = await _load_drug_names(settings)
    if not drug_names:
        # No drug list seeded yet — skip correction
        return structured

    choices = [name_upper for name_upper, _ in drug_names]
    name_map = {name_upper: original for name_upper, original in drug_names}

    corrected_meds = []
    for med in structured.medications:
        if not med.drug_name.strip():
            corrected_meds.append(med)
            continue

        query = med.drug_name.upper()
        match = process.extractOne(
            query,
            choices,
            scorer=fuzz.WRatio,
            score_cutoff=0,  # Get best match regardless, we'll threshold manually
        )

        if match is None:
            corrected_meds.append(med)
            continue

        matched_name_upper, score, _ = match
        original_name = name_map[matched_name_upper]

        if score >= settings.drug_match_threshold:
            # Auto-correct: use the canonical name from our list
            corrected_name = original_name.upper()
            corrected_meds.append(
                MedicationItem(
                    drug_name=corrected_name,
                    dosage=med.dosage,
                    frequency=med.frequency,
                    duration=med.duration,
                    instruction=med.instruction,
                    corrected_from=med.drug_name if corrected_name != med.drug_name else None,
                    correction_confidence=round(score, 1),
                    flagged=False,
                )
            )
        else:
            # Below threshold — flag for doctor review
            corrected_meds.append(
                MedicationItem(
                    drug_name=med.drug_name,
                    dosage=med.dosage,
                    frequency=med.frequency,
                    duration=med.duration,
                    instruction=med.instruction,
                    corrected_from=None,
                    correction_confidence=round(score, 1),
                    flagged=True,
                )
            )

    return StructuredPrescription(
        medications=corrected_meds,
        diagnosis=structured.diagnosis,
        advice=structured.advice,
        follow_up=structured.follow_up,
        same_as_last_time=structured.same_as_last_time,
    )
