"""
Abnormal vitals flag detection for API responses.
"""
from __future__ import annotations

from typing import Literal

VitalFlag = Literal["high_bp", "low_bp", "high_sugar", "low_spo2"]


def compute_vital_flags(
    *,
    bp_systolic: int | None = None,
    blood_sugar_mg_dl: int | None = None,
    blood_sugar_type: str | None = None,
    spo2_percent: int | None = None,
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []

    if bp_systolic is not None:
        if bp_systolic > 180:
            flags.append({"flag": "high_bp"})
        elif bp_systolic < 90:
            flags.append({"flag": "low_bp"})

    if (
        blood_sugar_mg_dl is not None
        and blood_sugar_type == "fasting"
        and blood_sugar_mg_dl > 126
    ):
        flags.append({"flag": "high_sugar"})

    if spo2_percent is not None and spo2_percent < 94:
        flags.append({"flag": "low_spo2"})

    return flags


def format_vitals_for_llm(
    *,
    bp_systolic: int | None = None,
    bp_diastolic: int | None = None,
    weight_kg: float | None = None,
    blood_sugar_mg_dl: int | None = None,
    blood_sugar_type: str | None = None,
    spo2_percent: int | None = None,
    temperature_f: float | None = None,
    pulse_bpm: int | None = None,
) -> str | None:
    parts: list[str] = []
    if bp_systolic is not None or bp_diastolic is not None:
        sys_val = bp_systolic if bp_systolic is not None else "—"
        dia_val = bp_diastolic if bp_diastolic is not None else "—"
        parts.append(f"BP {sys_val}/{dia_val}")
    if weight_kg is not None:
        parts.append(f"Weight {weight_kg}kg")
    if blood_sugar_mg_dl is not None:
        label = f" ({blood_sugar_type.upper()})" if blood_sugar_type else ""
        parts.append(f"Blood sugar {blood_sugar_mg_dl}mg/dL{label}")
    if spo2_percent is not None:
        parts.append(f"SpO2 {spo2_percent}%")
    if temperature_f is not None:
        parts.append(f"Temp {temperature_f}°F")
    if pulse_bpm is not None:
        parts.append(f"Pulse {pulse_bpm}bpm")
    return " · ".join(parts) if parts else None
