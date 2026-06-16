"""
Store and retrieve patient record attachment files (S3 or local disk).
"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.config import Settings
from app.services.s3 import upload_bytes_to_s3

_LOCAL_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "record_attachments"


def _sanitize_filename(name: str) -> str:
    base = Path(name).name.replace("..", "").strip()
    return base or "upload"


def build_storage_key(clinic_id: str, consultation_id: str, attachment_id: str, filename: str) -> str:
    safe = _sanitize_filename(filename)
    return f"clinics/{clinic_id}/record-attachments/{consultation_id}/{attachment_id}_{safe}"


def local_file_path(storage_key: str) -> Path:
    return _LOCAL_ROOT / storage_key


async def store_record_file(
    data: bytes,
    clinic_id: str,
    consultation_id: str,
    attachment_id: str,
    filename: str,
    mime_type: str,
    settings: Settings,
) -> tuple[str, str | None]:
    """
    Persist file bytes. Returns (storage_key, presigned_or_api_url).
    Uses S3 when configured; otherwise local disk with API download path.
    """
    storage_key = build_storage_key(clinic_id, consultation_id, attachment_id, filename)

    if settings.aws_access_key_id and settings.aws_secret_access_key and settings.aws_s3_bucket:
        url = await upload_bytes_to_s3(
            data,
            storage_key,
            settings,
            content_type=mime_type or "application/octet-stream",
            expiry_seconds=86400,
        )
        return storage_key, url

    path = local_file_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return storage_key, None


def read_record_file(storage_key: str) -> bytes | None:
    path = local_file_path(storage_key)
    if path.is_file():
        return path.read_bytes()
    return None


def delete_record_file(storage_key: str) -> None:
    path = local_file_path(storage_key)
    if path.is_file():
        path.unlink(missing_ok=True)


def new_attachment_id() -> str:
    return str(uuid.uuid4())
