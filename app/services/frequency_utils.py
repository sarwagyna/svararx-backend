"""Shared prescription frequency normalization."""

VALID_FREQUENCIES = frozenset({"OD", "BD", "TDS", "QID", "SOS", "HS", "WEEKLY"})


def normalize_frequency(freq: str) -> str:
    raw = (freq or "").strip().upper()
    if not raw:
        return ""
    if (
        raw == "SOS"
        or raw == "PRN"
        or "SOS" in raw
        or "PRN" in raw
        or "AS NEEDED" in raw
        or "WHEN NEEDED" in raw
        or "AS REQUIRED" in raw
    ):
        return "SOS"
    if raw == "WEEKLY" or raw.startswith("WEEKLY"):
        return "WEEKLY"
    for code in ("OD", "BD", "TDS", "QID", "HS"):
        if raw.startswith(code):
            return code
    return raw
