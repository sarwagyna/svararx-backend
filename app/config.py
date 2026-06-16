from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from pydantic import computed_field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # App
    environment: str = "development"
    secret_key: str = ""
    log_level: str = "INFO"
    cors_origins: str = ""  # comma-separated production origins
    run_migrations_on_startup: bool = False

    # AI APIs
    openai_api_key: str = ""   # Legacy — kept for compatibility
    sarvam_api_key: str
    groq_api_key: str

    # Database
    database_url: str

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
