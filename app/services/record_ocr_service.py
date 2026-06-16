"""
OCR text extraction and lab result structuring for patient record attachments.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from groq import Groq

from app.config import Settings, get_settings
from app.consultation_record_schemas import ClinicalTestResult

logger = logging.getLogger(__name__)

_LAB_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "lab_report_ocr.txt"
_VISION_MODEL = "llama-3.2-90b-vision-preview"
_TEXT_MODEL = "llama-3.3-70b-versatile"

_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
_PDF_TYPES = {"application/pdf"}


def _extract_json(text: str) -> dict[str, Any]:
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
    text = text.rstrip("`").strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(text[start:end])


def _pdf_text(data: bytes) -> str:
    try:
        import fitz  # pymupdf
    except ImportError:
        raise RuntimeError("PDF support requires pymupdf. Install with: pip install pymupdf")

    doc = fitz.open(stream=data, filetype="pdf")
    parts: list[str] = []
    for page in doc:
        parts.append(page.get_text("text"))
    doc.close()
    return "\n".join(p.strip() for p in parts if p.strip())


def _vision_ocr(data: bytes, mime_type: str, settings: Settings) -> str:
    client = Groq(api_key=settings.groq_api_key)
    b64 = base64.b64encode(data).decode("ascii")
    media = mime_type if mime_type in _IMAGE_TYPES else "image/jpeg"
    response = client.chat.completions.create(
        model=_VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "This is a medical document image (lab report, prescription, or clinical note). "
                            "Extract ALL visible text exactly. Preserve numbers, units, reference ranges, "
                            "patient name, dates, and test names. Return plain text only."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media};base64,{b64}"},
                    },
                ],
            }
        ],
        temperature=0,
        max_tokens=4096,
    )
    return (response.choices[0].message.content or "").strip()


def extract_text_from_file(data: bytes, mime_type: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    mime = (mime_type or "").lower()

    if mime in _PDF_TYPES:
        return _pdf_text(data)

    if mime in _IMAGE_TYPES or mime.startswith("image/"):
        return _vision_ocr(data, mime, settings)

    raise ValueError(f"Unsupported file type for OCR: {mime_type or 'unknown'}")


def _parse_sample_date(raw: Any) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y"):
        try:
            from datetime import datetime
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def structure_lab_report_text(
    ocr_text: str,
    *,
    attachment_id: str | None = None,
    settings: Settings | None = None,
) -> tuple[list[ClinicalTestResult], str | None, date | None]:
    settings = settings or get_settings()
    if not ocr_text.strip():
        return [], None, None

    system_prompt = _LAB_PROMPT_PATH.read_text(encoding="utf-8")
    client = Groq(api_key=settings.groq_api_key)
    response = client.chat.completions.create(
        model=_TEXT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Lab report OCR text:\n\n{ocr_text[:12000]}"},
        ],
        temperature=0.1,
        max_tokens=2048,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content or ""
    payload = _extract_json(raw)

    lab_name = str(payload.get("lab_name") or "").strip() or None
    sample_date = _parse_sample_date(payload.get("sample_date"))

    tests: list[ClinicalTestResult] = []
    for item in payload.get("clinical_tests") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("test_name") or "").strip()
        if not name:
            continue
        flag = str(item.get("flag") or "unknown").lower()
        if flag not in ("normal", "high", "low", "critical", "unknown"):
            flag = "unknown"
        tests.append(
            ClinicalTestResult(
                test_name=name,
                value=str(item.get("value") or ""),
                unit=str(item.get("unit") or ""),
                reference_range=str(item.get("reference_range") or ""),
                flag=flag,  # type: ignore[arg-type]
                sample_date=sample_date,
                lab_name=lab_name or "",
                notes=str(item.get("notes") or ""),
                source_attachment_id=attachment_id,
            )
        )
    return tests, lab_name, sample_date


async def run_attachment_ocr(
    data: bytes,
    mime_type: str,
    attachment_id: str,
    settings: Settings | None = None,
) -> tuple[str, list[ClinicalTestResult], str | None, date | None]:
    """Sync pipeline for use with asyncio.to_thread."""
    settings = settings or get_settings()
    ocr_text = extract_text_from_file(data, mime_type, settings)
    tests, lab_name, sample_date = structure_lab_report_text(
        ocr_text,
        attachment_id=attachment_id,
        settings=settings,
    )
    return ocr_text, tests, lab_name, sample_date
