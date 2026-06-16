from __future__ import annotations

import os
import re
import ssl
from functools import lru_cache
from typing import Any
from urllib.parse import quote, unquote, urlparse

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SUPABASE_DIRECT_HOST = re.compile(r"^db\.([a-z0-9]+)\.supabase\.co$", re.IGNORECASE)
_RAILWAY_ENV_KEYS = ("RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_ID", "RAILWAY_PROJECT_ID")


_SSL_QUERY_KEYS = {"ssl", "sslmode", "sslrootcert", "sslcert", "sslkey"}


def _strip_ssl_query(url: str) -> str:
    """
    Remove libpq-style ssl query params. TLS is controlled via connect_args so
    asyncpg does not receive an `ssl`/`sslmode` it cannot parse, and SQLAlchemy
    does not complain about SSL being specified twice.
    """
    if "?" not in url:
        return url
    base, _, query = url.partition("?")
    kept = [
        part
        for part in query.split("&")
        if part and part.split("=", 1)[0].lower() not in _SSL_QUERY_KEYS
    ]
    return f"{base}?{'&'.join(kept)}" if kept else base


def normalize_async_database_url(url: str) -> str:
    """Convert postgres:// or postgresql:// URLs to asyncpg driver form."""
    url = _strip_ssl_query(url.strip())
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def _parse_database_url(url: str):
    return urlparse(url.replace("postgresql+asyncpg://", "postgresql://", 1))


def database_hostname(url: str) -> str:
    return (_parse_database_url(url).hostname or "").lower()


def is_railway_deploy() -> bool:
    return any(os.environ.get(key) for key in _RAILWAY_ENV_KEYS)


def _build_asyncpg_url(user: str, password: str, host: str, port: str, db: str) -> str:
    return (
        f"postgresql+asyncpg://{quote(user, safe='')}:"
        f"{quote(password, safe='')}@{host}:{port}/{quote(db, safe='')}"
    )


def upgrade_supabase_url_for_deploy(url: str) -> str:
    """
    Supabase direct hosts (db.<ref>.supabase.co) are IPv6-only. Railway and many
    hosts cannot reach them unless outbound IPv6 is enabled. Prefer the IPv4
    pooler host from the Supabase dashboard, or set SUPABASE_DB_POOLER_HOST.
    """
    normalized = normalize_async_database_url(url)
    host = database_hostname(normalized)
    if not host or ".pooler.supabase.com" in host:
        return normalized

    match = _SUPABASE_DIRECT_HOST.match(host)
    if not match:
        return normalized

    pooler_host = os.environ.get("SUPABASE_DB_POOLER_HOST", "").strip()
    if not pooler_host:
        if is_railway_deploy():
            raise RuntimeError(
                "DATABASE_URL uses Supabase direct host "
                f"({host}), which is IPv6-only and unreachable from Railway by default. "
                "In Supabase → Project Settings → Database, copy the **Connection pooler** URI "
                "(Transaction mode, port 6543) into Railway DATABASE_URL, or set "
                "SUPABASE_DB_POOLER_HOST=aws-0-<region>.pooler.supabase.com and redeploy."
            )
        return normalized

    parsed = _parse_database_url(normalized)
    project_ref = match.group(1)
    password = unquote(parsed.password or "")
    db = (parsed.path or "/postgres").lstrip("/") or "postgres"
    port = os.environ.get("SUPABASE_DB_POOLER_PORT", "6543").strip() or "6543"
    user = parsed.username or "postgres"
    if not user.startswith("postgres."):
        user = f"postgres.{project_ref}"

    return _build_asyncpg_url(user, password, pooler_host, port, db)


def _ssl_context_for(host: str) -> ssl.SSLContext | None:
    """
    Build an SSL context for managed Postgres endpoints.

    Supabase (and many hosts) terminate TLS with a certificate that is not in
    the container's default trust store, which raises
    "certificate verify failed: self-signed certificate in certificate chain".

    Behaviour:
      - DB_SSL_CA_CERT (path) or DB_SSL_CA_CERT_PEM (inline PEM) → verify-full.
      - DB_SSL_MODE=require|prefer (default) → encrypt without chain verification.
      - DB_SSL_MODE=verify-full → use the system trust store.
      - DB_SSL_MODE=disable → no TLS.
    """
    mode = os.environ.get("DB_SSL_MODE", "require").strip().lower()
    if mode == "disable":
        return None

    ca_path = os.environ.get("DB_SSL_CA_CERT", "").strip()
    ca_pem = os.environ.get("DB_SSL_CA_CERT_PEM", "").strip()

    if ca_path or ca_pem:
        ctx = ssl.create_default_context(
            cafile=ca_path or None,
            cadata=ca_pem or None,
        )
        return ctx

    if mode in ("verify-ca", "verify-full"):
        return ssl.create_default_context()

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def asyncpg_connect_args(url: str) -> dict[str, Any]:
    """TLS is required for Supabase and most managed Postgres endpoints."""
    host = database_hostname(url)
    needs_tls = (
        host.endswith(".supabase.co")
        or host.endswith(".supabase.com")
        or os.environ.get("DB_SSL_MODE", "").strip().lower()
        not in ("", "disable")
    )
    if not needs_tls:
        return {}

    ctx = _ssl_context_for(host)
    if ctx is None:
        return {}
    return {"ssl": ctx}


def resolve_database_url() -> str:
    """
    Resolve a database URL from common deployment env vars.
    Used by Alembic and Settings so migrations do not require AI API keys.
    """
    for key in ("DATABASE_POOLER_URL", "DATABASE_URL", "DATABASE_PRIVATE_URL", "TEST_DATABASE_URL"):
        raw = os.environ.get(key)
        if raw and raw.strip():
            return upgrade_supabase_url_for_deploy(raw)

    user = os.environ.get("PGUSER") or os.environ.get("POSTGRES_USER")
    password = os.environ.get("PGPASSWORD") or os.environ.get("POSTGRES_PASSWORD")
    host = os.environ.get("PGHOST") or os.environ.get("POSTGRES_HOST")
    port = os.environ.get("PGPORT") or os.environ.get("POSTGRES_PORT") or "5432"
    db = os.environ.get("PGDATABASE") or os.environ.get("POSTGRES_DB")

    if user and password and db and host:
        return upgrade_supabase_url_for_deploy(_build_asyncpg_url(user, password, host, port, db))
    return ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # App
    environment: str = "development"
    secret_key: str = ""
    log_level: str = "INFO"
    cors_origins: str = ""  # comma-separated production origins
    run_migrations_on_startup: bool = False

    # AI APIs — optional at import/migration time; required for voice/LLM endpoints
    openai_api_key: str = ""   # Legacy — kept for compatibility
    sarvam_api_key: str = ""
    groq_api_key: str = ""

    # Database
    database_url: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Supabase
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwks_json: str = ""  # ES256 JWK set from /auth/v1/.well-known/jwks.json

    # Drug correction
    drug_match_threshold: int = 80

    # Latency / SLA
    sla_threshold_seconds: int = 35
    stt_timeout_seconds: int = 25
    groq_timeout_seconds: int = 20

    # AWS S3
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-south-1"
    aws_s3_bucket: str = ""

    @model_validator(mode="before")
    @classmethod
    def _populate_database_url(cls, data: Any) -> Any:
        if isinstance(data, dict) and not (data.get("database_url") or "").strip():
            resolved = resolve_database_url()
            if resolved:
                data["database_url"] = resolved
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip():
            return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if not self.is_production:
            return [
                "http://localhost:3000",
                "https://localhost:3000",
                "http://127.0.0.1:3000",
                "https://127.0.0.1:3000",
            ]
        return []


@lru_cache
def get_settings() -> Settings:
    return Settings()
