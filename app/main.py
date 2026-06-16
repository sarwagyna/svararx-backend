"""
SvaraRx FastAPI application entry point.
"""
from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.database import engine, Base
from app.core.security import ensure_secret_key_configured
from app.logging_config import configure_logging
from app.api import transcribe, structure, prescriptions, patients, drugs, transcribe_and_structure, dashboard, admin, auth_router, onboarding, voice, doctors, rx, consultations, vitals, allergies, conditions, consultation_records, clinic_session, clinic_admin
from app.schemas import HealthResponse, ReadinessResponse
from app.services.redis_client import get_redis

logger = logging.getLogger(__name__)

settings = get_settings()
_DEV_CORS_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?$"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if settings.is_production:
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate config and optionally sync schema in development."""
    configure_logging()
    ensure_secret_key_configured()
    if settings.environment == "development":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    yield


app = FastAPI(
    title="SvaraRx API",
    description="AI-powered voice-to-prescription documentation for Indian doctors.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

_cors_origins = settings.cors_origin_list()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=None if settings.is_production else _DEV_CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)


# Convert any unhandled exception into a JSONResponse so it flows back through
# CORSMiddleware (otherwise Starlette's ServerErrorMiddleware would bypass it,
# producing a 500 with no Access-Control-Allow-Origin header).
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    content: dict[str, str] = {"detail": "Internal Server Error"}
    if not settings.is_production:
        content["error"] = type(exc).__name__
        content["message"] = str(exc)
    return JSONResponse(status_code=500, content=content)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers,
    )


# Backwards-compat alias for any uvicorn command still targeting `asgi_app`.
asgi_app = app

# ─── Routes ───────────────────────────────────────────────────
PREFIX = "/api/v1"

app.include_router(auth_router.router, prefix=PREFIX, tags=["Auth"])
app.include_router(clinic_session.router, prefix=PREFIX, tags=["Clinic Session"])
app.include_router(clinic_admin.router, prefix=PREFIX, tags=["Clinic Admin"])
app.include_router(onboarding.router, prefix=PREFIX, tags=["Onboarding"])
app.include_router(doctors.router, prefix=PREFIX, tags=["Doctors"])
app.include_router(transcribe.router, prefix=PREFIX, tags=["Transcription"])
app.include_router(structure.router, prefix=PREFIX, tags=["Structuring"])
app.include_router(transcribe_and_structure.router, prefix=PREFIX, tags=["Transcription"])
app.include_router(voice.router, prefix=PREFIX, tags=["Voice"])
app.include_router(rx.router, prefix=PREFIX, tags=["Rx Structuring"])
app.include_router(consultations.router, prefix=PREFIX, tags=["Consultations"])
app.include_router(consultation_records.router, prefix=PREFIX, tags=["Consultation Records"])
app.include_router(prescriptions.router, prefix=PREFIX, tags=["Prescriptions"])
app.include_router(patients.router, prefix=PREFIX, tags=["Patients"])
app.include_router(vitals.router, prefix=PREFIX, tags=["Vitals"])
app.include_router(allergies.router, prefix=PREFIX, tags=["Allergies"])
app.include_router(conditions.router, prefix=PREFIX, tags=["Conditions"])
app.include_router(drugs.router, prefix=PREFIX, tags=["Drugs"])
app.include_router(dashboard.router, prefix=PREFIX, tags=["Dashboard"])
app.include_router(admin.router, prefix=PREFIX, tags=["Admin"])


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health():
    return HealthResponse(status="ok")


@app.get("/health/ready", response_model=ReadinessResponse, tags=["Health"])
async def readiness():
    db_status = "ok"
    redis_status = "ok"

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Database readiness check failed")
        db_status = "error"

    try:
        get_redis().ping()
    except Exception:
        logger.exception("Redis readiness check failed")
        redis_status = "error"

    overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"
    return ReadinessResponse(
        status=overall,
        database=db_status,
        redis=redis_status,
    )


@app.get("/", include_in_schema=False)
async def root():
    return {"message": "SvaraRx API — see /docs"}
