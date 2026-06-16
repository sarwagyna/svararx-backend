"""
Pydantic models for LLM-structured prescriptions.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class DrugItem(BaseModel):
    name: str
    generic_name: Optional[str] = None
    dose: str = ""
    frequency: str = ""
    duration: str = ""
    route: str = "oral"
    instructions: Optional[str] = None
    flagged: bool = False  # true if drug name was uncertain


class StructuredRx(BaseModel):
    drugs: list[DrugItem] = Field(default_factory=list)
    chief_complaint: Optional[str] = None
    diagnosis: Optional[str] = None
    follow_up_days: Optional[int] = None
    notes: Optional[str] = None
    raw_transcript: str = ""
    structuring_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
