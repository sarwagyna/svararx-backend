"""Consultation ↔ prescription linking helpers."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Consultation

logger = logging.getLogger(__name__)


async def complete_consultation_with_prescription(
    db: AsyncSession,
    consultation_id: str | None,
    doctor_id: str,
    prescription_id: str,
) -> None:
    """
    Best-effort link of a consultation to its prescription.

    Linking is a side effect of approval — a stale/mismatched consultation id
    must never fail the approval (the prescription is already committed by then),
    so we log and skip instead of raising.
    """
    if not consultation_id:
        return

    consultation = await db.get(Consultation, consultation_id)
    if not consultation or consultation.doctor_id != doctor_id:
        logger.warning(
            "Skipping consultation link: %s not found or doctor mismatch (doctor=%s)",
            consultation_id,
            doctor_id,
        )
        return
    if consultation.completed_at:
        return

    consultation.prescription_id = prescription_id
    consultation.completed_at = datetime.now(timezone.utc)


def merge_chief_complaint_into_structured(
    structured: dict,
    chief_complaint: str | None,
    tags: list[str] | None = None,
) -> dict:
    if chief_complaint and not structured.get("chief_complaint"):
        structured = {**structured, "chief_complaint": chief_complaint}
    if tags and not structured.get("chief_complaint_tags"):
        structured = {**structured, "chief_complaint_tags": tags}
    return structured
