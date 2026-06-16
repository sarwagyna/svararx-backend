"""Doctor approval PIN — 4-digit gesture, not a login password."""
from __future__ import annotations

import hashlib
import re
import secrets

_PIN_RE = re.compile(r"^\d{4}$")


def validate_pin_format(pin: str) -> str:
    pin = pin.strip()
    if not _PIN_RE.match(pin):
        raise ValueError("PIN must be exactly 4 digits.")
    return pin


def hash_pin(pin: str) -> str:
    pin = validate_pin_format(pin)
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${digest}"


def verify_pin(pin: str, stored_hash: str) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    try:
        pin = validate_pin_format(pin)
    except ValueError:
        return False
    salt, digest = stored_hash.split("$", 1)
    check = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 100_000).hex()
    return secrets.compare_digest(check, digest)


def doctor_has_pin(stored_hash: str) -> bool:
    return bool(stored_hash and stored_hash.strip())
