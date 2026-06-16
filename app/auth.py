"""
FastAPI auth dependencies — clinic membership helpers built on HS256 JWT auth.

get_current_doctor lives in app.core.dependencies.
Supabase JWT verification is retained only for registration / token exchange.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import jwt
from jwt.algorithms import ECAlgorithm
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.dependencies import get_access_token_payload, get_current_doctor
from app.core.tenant import resolve_membership
from app.core.security import WWW_AUTHENTICATE_HEADER
from app.database import get_db
from app.models import Doctor, DoctorClinic

logger = logging.getLogger(__name__)
_bearer = HTTPBearer()

_cached_public_key: Any = None
_cached_jwks_json: str = ""


def _get_supabase_public_key(settings: Settings):
    """Parse and cache the EC public key from the configured JWK set."""
    global _cached_public_key, _cached_jwks_json
    if _cached_public_key is not None and _cached_jwks_json == settings.supabase_jwks_json:
        return _cached_public_key

    if not settings.supabase_jwks_json:
        raise HTTPException(
            status_code=500,
            detail="Server misconfiguration: SUPABASE_JWKS_JSON is not set.",
        )

    jwks = json.loads(settings.supabase_jwks_json)
    key_data = jwks["keys"][0]
    _cached_public_key = ECAlgorithm.from_jwk(json.dumps(key_data))
    _cached_jwks_json = settings.supabase_jwks_json
    return _cached_public_key


def verify_supabase_token(
    creds: HTTPAuthorizationCredentials,
    settings: Settings,
) -> dict:
    """Verify Supabase JWT (ES256) for registration / token exchange only."""
    public_key = _get_supabase_public_key(settings)
    try:
        return jwt.decode(
            creds.credentials,
            public_key,
            algorithms=["ES256"],
            audience="authenticated",
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "require": ["exp", "sub", "iat"],
            },
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Token expired.",
            headers=WWW_AUTHENTICATE_HEADER,
        ) from None
    except jwt.InvalidTokenError as exc:
        logger.debug("Supabase JWT validation failed: %s", exc)
        raise HTTPException(
            status_code=401,
            detail="Invalid token.",
            headers=WWW_AUTHENTICATE_HEADER,
        ) from None


def get_supabase_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Dependency: verified Supabase user payload (register / token exchange)."""
    return verify_supabase_token(creds, settings)


async def get_doctor_membership(
    doctor: Doctor = Depends(get_current_doctor),
    payload: dict = Depends(get_access_token_payload),
    db: AsyncSession = Depends(get_db),
) -> DoctorClinic:
    """Return the active clinic membership matching the JWT clinic_id claim."""
    return await resolve_membership(
        db,
        doctor.id,
        payload.get("clinic_id"),
        onboarding=bool(payload.get("onboarding")),
    )


async def get_doctor_clinic_id(
    membership: DoctorClinic = Depends(get_doctor_membership),
) -> str:
    return membership.clinic_id


async def require_clinic_admin(
    membership: DoctorClinic = Depends(get_doctor_membership),
) -> DoctorClinic:
    if membership.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return membership
