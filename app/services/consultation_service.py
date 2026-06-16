"""Consultation ↔ prescription linking helpers."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Consultation


async def complete_consultation_with_prescription(
    db: AsyncSession,
    consultation_id: str | None,
    doctor_id: str,
    prescription_id: str,
) -> None:
    if not consultation_id:
        return

    consultation = await db.get(Consultation, consultation_id)
    if not consultation or consultation.doctor_id != doctor_id:
        raise HTTPException(status_code=404, detail="Consultation not found.")
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
