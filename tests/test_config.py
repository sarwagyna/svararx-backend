"""Unit tests for database URL resolution used by Alembic and Settings."""

import os

from app.config import normalize_async_database_url, resolve_database_url


def test_normalize_async_database_url_postgres_scheme():
    assert (
        normalize_async_database_url("postgres://user:pass@host:5432/db")
        == "postgresql+asyncpg://user:pass@host:5432/db"
    )


def test_normalize_async_database_url_postgresql_scheme():
    assert (
        normalize_async_database_url("postgresql://user:pass@host:5432/db")
        == "postgresql+asyncpg://user:pass@host:5432/db"
    )


def test_normalize_async_database_url_keeps_asyncpg():
    url = "postgresql+asyncpg://user:pass@host:5432/db"
    assert normalize_async_database_url(url) == url


def test_resolve_database_url_from_database_url(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://deploy:secret@db.example.com:5432/svararx",
    )
    monkeypatch.delenv("DATABASE_PRIVATE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    assert (
        resolve_database_url()
        == "postgresql+asyncpg://deploy:secret@db.example.com:5432/svararx"
    )


def test_resolve_database_url_from_pg_vars(monkeypatch):
    for key in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "TEST_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PGUSER", "deploy")
    monkeypatch.setenv("PGPASSWORD", "secret")
    monkeypatch.setenv("PGHOST", "db.example.com")
    monkeypatch.setenv("PGPORT", "5432")
    monkeypatch.setenv("PGDATABASE", "svararx")
    assert (
        resolve_database_url()
        == "postgresql+asyncpg://deploy:secret@db.example.com:5432/svararx"
    )
