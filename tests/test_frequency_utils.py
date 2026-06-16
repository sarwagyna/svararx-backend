"""Unit tests for prescription frequency normalization."""

from app.services.frequency_utils import VALID_FREQUENCIES, normalize_frequency


def test_normalize_frequency_codes():
    assert normalize_frequency("od") == "OD"
    assert normalize_frequency("BD") == "BD"
    assert normalize_frequency("tds") == "TDS"
    assert normalize_frequency("qid") == "QID"
    assert normalize_frequency("hs") == "HS"
    assert normalize_frequency("OD daily") == "OD"


def test_normalize_frequency_sos_and_weekly():
    assert normalize_frequency("prn") == "SOS"
    assert normalize_frequency("as needed") == "SOS"
    assert normalize_frequency("weekly") == "WEEKLY"
    assert normalize_frequency("WEEKLY") == "WEEKLY"


def test_normalize_frequency_empty():
    assert normalize_frequency("") == ""
    assert normalize_frequency("  ") == ""


def test_normalize_frequency_unknown_passthrough():
    assert normalize_frequency("stat") == "STAT"


def test_valid_frequencies_include_extended_codes():
    assert "SOS" in VALID_FREQUENCIES
    assert "HS" in VALID_FREQUENCIES
    assert "WEEKLY" in VALID_FREQUENCIES
