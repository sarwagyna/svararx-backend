from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_async_database_url(url: str) -> str:
    """Convert postgres:// or postgresql:// URLs to asyncpg driver form."""
    url = url.strip()
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def resolve_database_url() -> str:
    """
    Resolve a database URL from common deployment env vars.
    Used by Alembic and Settings so migrations do not require AI API keys.
    """
    for key in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "TEST_DATABASE_URL"):
        raw = os.environ.get(key)
        if raw and raw.strip():
            return normalize_async_database_url(raw)

    user = os.environ.get("PGUSER") or os.environ.get("POSTGRES_USER")
    password = os.environ.get("PGPASSWORD") or os.environ.get("POSTGRES_PASSWORD")
    host = os.environ.get("PGHOST") or os.environ.get("POSTGRES_HOST")
    port = os.environ.get("PGPORT") or os.environ.get("POSTGRES_PORT") or "5432"
    db = os.environ.get("PGDATABASE") or os.environ.get("POSTGRES_DB")

    if user and password and db and host:
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"
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
