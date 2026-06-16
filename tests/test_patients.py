"""
Patient CRUD and search tests.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_and_get_patient(client: AsyncClient, auth_headers):
    headers, _doctor, _clinic = auth_headers
    create = await client.post(
        "/api/v1/patients",
        headers=headers,
        json={"name": "Lakshmi Devi", "age": 38, "sex": "F", "phone": "9876543210"},
    )
    assert create.status_code == 201
    created = create.json()
    assert created["name"] == "Lakshmi Devi"
    assert created["phone"] == "9876543210"

    fetched = await client.get(f"/api/v1/patients/{created['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]


@pytest.mark.asyncio
async def test_update_patient(client: AsyncClient, auth_headers, make_patient):
    headers, doctor, clinic = auth_headers
    patient = await make_patient(doctor=doctor, clinic=clinic, name="Old Name", age=30)

    update = await client.patch(
        f"/api/v1/patients/{patient.id}",
        headers=headers,
        json={"name": "New Name", "age": 31},
    )
    assert update.status_code == 200
    body = update.json()
    assert body["name"] == "New Name"
    assert body["age"] == 31


@pytest.mark.asyncio
async def test_search_phone_exact_match(client: AsyncClient, auth_headers, make_patient):
    headers, doctor, clinic = auth_headers
    phone = "9111222333"
    await make_patient(doctor=doctor, clinic=clinic, name="Exact Phone", phone=phone)

    result = await client.get("/api/v1/patients/search", headers=headers, params={"q": phone})
    assert result.status_code == 200
    names = [p["full_name"] for p in result.json()]
    assert "Exact Phone" in names
    assert result.json()[0]["phone"] == phone


@pytest.mark.asyncio
async def test_search_phone_partial_does_not_match(client: AsyncClient, auth_headers, make_patient):
    headers, doctor, clinic = auth_headers
    await make_patient(doctor=doctor, clinic=clinic, name="Partial Phone", phone="9222333444")

    result = await client.get("/api/v1/patients/search", headers=headers, params={"q": "9222333"})
    assert result.status_code == 200
    assert result.json() == []


@pytest.mark.asyncio
async def test_search_phone_not_found(client: AsyncClient, auth_headers, make_patient):
    headers, doctor, clinic = auth_headers
    await make_patient(doctor=doctor, clinic=clinic, phone="9333444555")

    result = await client.get("/api/v1/patients/search", headers=headers, params={"q": "9999999999"})
    assert result.status_code == 200
    assert result.json() == []


@pytest.mark.asyncio
async def test_search_name_returns_search_fields(client: AsyncClient, auth_headers, make_patient):
    headers, doctor, clinic = auth_headers
    await make_patient(doctor=doctor, clinic=clinic, name="Ramesh Kumar", phone="9444555666")

    result = await client.get("/api/v1/patients/search", headers=headers, params={"q": "Ramesh"})
    assert result.status_code == 200
    body = result.json()
    assert len(body) >= 1
    match = next(p for p in body if p["full_name"] == "Ramesh Kumar")
    assert match["gender"] == "M"
    assert "prescription_count" in match
    assert "last_visit_date" in match


@pytest.mark.asyncio
async def test_list_patients_paginated_with_filters(client: AsyncClient, auth_headers, make_patient):
    headers, doctor, clinic = auth_headers
    await make_patient(doctor=doctor, clinic=clinic, name="Alice Alpha", age=25, sex="F", phone="9000000001")
    await make_patient(doctor=doctor, clinic=clinic, name="Bob Beta", age=45, sex="M", phone="9000000002")

    result = await client.get(
        "/api/v1/patients",
        headers=headers,
        params={"q": "Alice", "sex": "F", "page": 1, "limit": 10},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["total"] >= 1
    assert body["page"] == 1
    assert any(item["name"] == "Alice Alpha" for item in body["items"])
    assert all(item["sex"] == "F" for item in body["items"])


@pytest.mark.asyncio
async def test_duplicate_phone_returns_409(client: AsyncClient, auth_headers, make_patient):
    headers, doctor, clinic = auth_headers
    phone = "9444555666"
    await make_patient(doctor=doctor, clinic=clinic, phone=phone)

    duplicate = await client.post(
        "/api/v1/patients",
        headers=headers,
        json={"name": "Duplicate", "age": 40, "sex": "M", "phone": phone},
    )
    assert duplicate.status_code == 409
    assert "phone" in duplicate.json()["detail"].lower()
