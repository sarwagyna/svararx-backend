"""
HS256 JWT creation and verification for SvaraRx API tokens.

Access tokens carry doctor_id (sub) + clinic_id, 8-hour expiry.
Refresh tokens carry doctor_id (sub) + jti, 30-day expiry.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 8
REFRESH_TOKEN_EXPIRE_DAYS = 30
APPROVAL_TOKEN_EXPIRE_MINUTES = 5
REQUIRED_CLAIMS = ["exp", "sub", "iat", "type"]
WWW_AUTHENTICATE_HEADER = {"WWW-Authenticate": "Bearer"}

_SECRET_KEY: str | None = None


def _auth_error(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=detail,
        headers=WWW_AUTHENTICATE_HEADER,
    )


def load_secret_key() -> str:
    """Load SECRET_KEY from settings; raise RuntimeError if missing."""
    from app.config import get_settings

    key = get_settings().secret_key.strip()
    if not key:
        raise RuntimeError(
            "SECRET_KEY environment variable is required and must be non-empty."
        )
    return key


def get_secret_key() -> str:
    """Return cached SECRET_KEY, loading on first access."""
    global _SECRET_KEY
    if _SECRET_KEY is None:
        _SECRET_KEY = load_secret_key()
    return _SECRET_KEY


def ensure_secret_key_configured() -> None:
    """Call during application startup to fail fast on misconfiguration."""
    get_secret_key()


def decode_token(token: str, *, expected_type: str = "access") -> dict[str, Any]:
    """
    Decode and validate a JWT.

    Enforces HS256, signature verification, exp/iat checks, and required claims.
    Raises HTTPException(401) with WWW-Authenticate: Bearer on any failure.
    """
    try:
        payload = jwt.decode(
            token,
            get_secret_key(),
            algorithms=[ALGORITHM],
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "require": REQUIRED_CLAIMS,
            },
        )
    except jwt.ExpiredSignatureError:
        raise _auth_error("Token expired.") from None
    except jwt.InvalidTokenError:
        raise _auth_error("Invalid token.") from None

    token_type = payload.get("type")
    if token_type != expected_type:
        raise _auth_error("Invalid token type.")

    if not payload.get("sub"):
        raise _auth_error("Token missing subject claim.")

    if expected_type == "access" and payload.get("onboarding") and not payload.get("clinic_id"):
        return payload

    if expected_type == "access" and not payload.get("clinic_id"):
        raise _auth_error("Token missing clinic_id claim.")

    return payload


def create_access_token(doctor_id: str, clinic_id: str | None = None, *, onboarding: bool = False) -> str:
    """Issue an 8-hour access token for one clinic day."""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": doctor_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    if clinic_id:
        payload["clinic_id"] = clinic_id
    if onboarding:
        payload["onboarding"] = True
    return jwt.encode(payload, get_secret_key(), algorithm=ALGORITHM)


def create_refresh_token(doctor_id: str) -> str:
    """Issue a 30-day refresh token with a unique jti for revocation."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": doctor_id,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, get_secret_key(), algorithm=ALGORITHM)


def create_approval_token(
    doctor_id: str,
    clinic_id: str,
    *,
    prescription_id: str | None = None,
    issued_by: str | None = None,
) -> str:
    """Short-lived token that unlocks a single prescription approval."""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": doctor_id,
        "clinic_id": clinic_id,
        "type": "approval",
        "iat": now,
        "exp": now + timedelta(minutes=APPROVAL_TOKEN_EXPIRE_MINUTES),
        "jti": str(uuid.uuid4()),
    }
    if prescription_id:
        payload["prescription_id"] = prescription_id
    if issued_by:
        payload["issued_by"] = issued_by
    return jwt.encode(payload, get_secret_key(), algorithm=ALGORITHM)


def decode_approval_token(token: str) -> dict[str, Any]:
    """Validate an approval gesture token."""
    try:
        payload = jwt.decode(
            token,
            get_secret_key(),
            algorithms=[ALGORITHM],
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "require": ["exp", "sub", "iat", "type", "clinic_id"],
            },
        )
    except jwt.ExpiredSignatureError:
        raise _auth_error("Approval PIN expired. Ask the doctor to enter PIN again.") from None
    except jwt.InvalidTokenError:
        raise _auth_error("Invalid approval token.") from None

    if payload.get("type") != "approval":
        raise _auth_error("Invalid approval token type.")
    if not payload.get("sub") or not payload.get("clinic_id"):
        raise _auth_error("Approval token missing required claims.")
    return payload
