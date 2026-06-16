"""
Unit tests for vitals flag detection.
"""
from app.services.vitals_flags import compute_vital_flags, format_vitals_for_llm


def test_compute_high_bp_flag():
    flags = compute_vital_flags(bp_systolic=185)
    assert {"flag": "high_bp"} in flags


def test_compute_low_bp_flag():
    flags = compute_vital_flags(bp_systolic=85)
    assert {"flag": "low_bp"} in flags


def test_compute_high_sugar_fasting_only():
    flags = compute_vital_flags(blood_sugar_mg_dl=130, blood_sugar_type="fasting")
    assert {"flag": "high_sugar"} in flags
    flags_pp = compute_vital_flags(blood_sugar_mg_dl=200, blood_sugar_type="pp")
    assert flags_pp == []


def test_compute_low_spo2():
    flags = compute_vital_flags(spo2_percent=90)
    assert {"flag": "low_spo2"} in flags


def test_format_vitals_for_llm():
    text = format_vitals_for_llm(
        bp_systolic=140,
        bp_diastolic=90,
        weight_kg=82,
        blood_sugar_mg_dl=210,
        blood_sugar_type="pp",
    )
    assert "BP 140/90" in text
    assert "82kg" in text
    assert "210mg/dL" in text
