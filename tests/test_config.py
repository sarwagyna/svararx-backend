"""Unit tests for database URL resolution used by Alembic and Settings."""

import os

import pytest

from app.config import (
    asyncpg_connect_args,
    normalize_async_database_url,
    resolve_database_url,
    upgrade_supabase_url_for_deploy,
)


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
    monkeypatch.delenv("DATABASE_POOLER_URL", raising=False)
    monkeypatch.delenv("DATABASE_PRIVATE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    assert (
        resolve_database_url()
        == "postgresql+asyncpg://deploy:secret@db.example.com:5432/svararx"
    )


def test_resolve_database_url_prefers_pooler_url(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_POOLER_URL",
        "postgres://postgres.ref:secret@aws-0-ap-south-1.pooler.supabase.com:6543/postgres",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://deploy:secret@db.example.com:5432/svararx",
    )
    assert (
        resolve_database_url()
        == "postgresql+asyncpg://postgres.ref:secret@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
    )


def test_resolve_database_url_from_pg_vars(monkeypatch):
    for key in ("DATABASE_URL", "DATABASE_POOLER_URL", "DATABASE_PRIVATE_URL", "TEST_DATABASE_URL"):
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


def test_upgrade_supabase_direct_with_pooler_host(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("SUPABASE_DB_POOLER_HOST", "aws-0-ap-south-1.pooler.supabase.com")
    monkeypatch.setenv("SUPABASE_DB_POOLER_PORT", "6543")
    url = "postgresql://postgres:secret@db.abc123.supabase.co:5432/postgres"
    assert (
        upgrade_supabase_url_for_deploy(url)
        == "postgresql+asyncpg://postgres.abc123:secret@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
    )


def test_upgrade_supabase_direct_on_railway_raises(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("SUPABASE_DB_POOLER_HOST", raising=False)
    url = "postgresql://postgres:secret@db.abc123.supabase.co:5432/postgres"
    with pytest.raises(RuntimeError, match="Connection pooler"):
        upgrade_supabase_url_for_deploy(url)


def test_asyncpg_connect_args_for_supabase(monkeypatch):
    monkeypatch.delenv("DB_SSL_MODE", raising=False)
    monkeypatch.delenv("DB_SSL_CA_CERT", raising=False)
    monkeypatch.delenv("DB_SSL_CA_CERT_PEM", raising=False)
    url = "postgresql+asyncpg://postgres:secret@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
    args = asyncpg_connect_args(url)
    assert "ssl" in args
    ctx = args["ssl"]
    assert ctx.verify_mode.name == "CERT_NONE"


def test_asyncpg_connect_args_no_tls_for_local():
    url = "postgresql+asyncpg://postgres:secret@localhost:5432/svararx"
    assert asyncpg_connect_args(url) == {}


def test_asyncpg_connect_args_disables_cache_on_transaction_pooler(monkeypatch):
    monkeypatch.delenv("DB_SSL_MODE", raising=False)
    url = "postgresql+asyncpg://postgres.ref:secret@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
    args = asyncpg_connect_args(url)
    assert args.get("statement_cache_size") == 0
    assert "ssl" in args


def test_asyncpg_connect_args_session_pooler_keeps_cache(monkeypatch):
    monkeypatch.delenv("DB_SSL_MODE", raising=False)
    url = "postgresql+asyncpg://postgres.ref:secret@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
    args = asyncpg_connect_args(url)
    assert "statement_cache_size" not in args


def _settings(**overrides):
    from app.config import Settings

    base = dict(secret_key="x" * 32, sarvam_api_key="k", groq_api_key="k")
    base.update(overrides)
    return Settings(**base)


def test_resolve_stt_engine_explicit():
    assert _settings(stt_engine="whisper").resolve_stt_engine() == "whisper"
    assert _settings(stt_engine="sarvam").resolve_stt_engine() == "sarvam"


def test_resolve_stt_engine_auto_in_production():
    assert _settings(environment="production").resolve_stt_engine() == "sarvam"


def test_resolve_stt_engine_auto_in_development():
    assert _settings(environment="development").resolve_stt_engine() == "auto"


def test_normalize_strips_ssl_query():
    url = "postgresql+asyncpg://postgres:secret@db.example.com:5432/postgres?ssl=require"
    assert (
        normalize_async_database_url(url)
        == "postgresql+asyncpg://postgres:secret@db.example.com:5432/postgres"
    )


def test_normalize_strips_sslmode_keeps_others():
    url = "postgres://u:p@host:5432/db?sslmode=require&application_name=svararx"
    assert (
        normalize_async_database_url(url)
        == "postgresql+asyncpg://u:p@host:5432/db?application_name=svararx"
    )
