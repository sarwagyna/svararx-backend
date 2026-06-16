"""
Authentication tests for HS256 JWT middleware.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import delete

from app.core.security import (
    ALGORITHM,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_secret_key,
)
from app.models import Doctor


@pytest.mark.asyncio
async def test_valid_access_token_returns_200(client: AsyncClient, auth_headers):
    headers, doctor, _clinic = auth_headers
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == doctor.id
    assert body["name"] == doctor.name


@pytest.mark.asyncio
async def test_expired_token_returns_401(client: AsyncClient, auth_headers):
    _headers, doctor, clinic = auth_headers
    now = datetime.now(timezone.utc)
    payload = {
        "sub": doctor.id,
        "clinic_id": clinic.id,
        "type": "access",
        "iat": now - timedelta(hours=10),
        "exp": now - timedelta(hours=1),
    }
    token = jwt.encode(payload, get_secret_key(), algorithm=ALGORITHM)
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"
    assert "expired" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_tampered_signature_returns_401(client: AsyncClient, auth_headers):
    headers, _doctor, _clinic = auth_headers
    token = headers["Authorization"].removeprefix("Bearer ")
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tampered}"},
    )
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_missing_sub_claim_returns_401(client: AsyncClient, auth_headers):
    _headers, _doctor, clinic = auth_headers
    now = datetime.now(timezone.utc)
    payload = {
        "clinic_id": clinic.id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    token = jwt.encode(payload, get_secret_key(), algorithm=ALGORITHM)
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_wrong_algorithm_returns_401(client: AsyncClient, auth_headers):
    _headers, doctor, clinic = auth_headers
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": doctor.id,
        "clinic_id": clinic.id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = jwt.encode(payload, pem, algorithm="RS256")
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_refresh_token_used_as_access_returns_401(client: AsyncClient, auth_headers):
    _headers, doctor, _clinic = auth_headers
    refresh = create_refresh_token(doctor.id)
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {refresh}"},
    )
    assert response.status_code == 401
    assert "token type" in response.json()["detail"].lower()
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_doctor_deleted_mid_session_returns_401(
    client: AsyncClient,
    make_doctor,
    valid_token,
    db_session,
):
    doctor, clinic = await make_doctor()
    token = valid_token(doctor.id, clinic.id)
    await db_session.execute(delete(Doctor).where(Doctor.id == doctor.id))
    await db_session.commit()

    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert "not found" in response.json()["detail"].lower()
    assert response.headers.get("www-authenticate") == "Bearer"


def test_decode_token_rejects_refresh_as_access():
    doctor_id = "11111111-1111-1111-1111-111111111111"
    refresh = create_refresh_token(doctor_id)
    with pytest.raises(HTTPException) as exc_info:
        decode_token(refresh, expected_type="access")
    assert exc_info.value.status_code == 401


def test_create_tokens_include_required_claims():
    doctor_id = "11111111-1111-1111-1111-111111111111"
    clinic_id = "22222222-2222-2222-2222-222222222222"
    access = create_access_token(doctor_id, clinic_id)
    refresh = create_refresh_token(doctor_id)

    access_payload = decode_token(access, expected_type="access")
    assert access_payload["sub"] == doctor_id
    assert access_payload["clinic_id"] == clinic_id
    assert access_payload["type"] == "access"

    refresh_payload = decode_token(refresh, expected_type="refresh")
    assert refresh_payload["sub"] == doctor_id
    assert refresh_payload["type"] == "refresh"
    assert refresh_payload.get("jti")
