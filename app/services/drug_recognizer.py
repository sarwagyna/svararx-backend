"""
Drug name recognition — checks whether a drug name matches the known drug index.
Used by rx_structurer to flag uncertain drug names after LLM extraction.
"""
from __future__ import annotations

from rapidfuzz import fuzz, process

from app.config import Settings
from app.services import drug_correction


async def recognize_drug(name: str, settings: Settings) -> bool:
    """
    Return True if the drug name matches the seeded drug list above threshold.
    """
    query = name.strip()
    if not query:
        return False

    drug_names = await drug_correction._load_drug_names(settings)
    if not drug_names:
        return False

    choices = [name_upper for name_upper, _ in drug_names]
    match = process.extractOne(
        query.upper(),
        choices,
        scorer=fuzz.WRatio,
        score_cutoff=0,
    )
    if match is None:
        return False

    _, score, _ = match
    return score >= settings.drug_match_threshold
