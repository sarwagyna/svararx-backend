"""
FastAPI dependencies for authenticated API routes.
"""
from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import WWW_AUTHENTICATE_HEADER, decode_token
from app.database import get_db
from app.models import Doctor
from fastapi import HTTPException

_bearer = HTTPBearer()


async def get_access_token_payload(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Decode and return the Bearer access token payload."""
    return decode_token(credentials.credentials, expected_type="access")


async def get_current_doctor(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> Doctor:
    """
    Decode the Bearer access token and load the matching active Doctor row.
    """
    payload = decode_token(credentials.credentials, expected_type="access")
    doctor_id = payload["sub"]

    result = await db.execute(
        select(Doctor).where(
            Doctor.id == doctor_id,
            Doctor.is_active == True,
        )
    )
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(
            status_code=401,
            detail="Doctor account not found.",
            headers=WWW_AUTHENTICATE_HEADER,
        )
    return doctor
