"""
Celery tasks for background processing.
Currently: async PDF generation + S3 upload for large batches.
"""
from app.worker.celery_app import celery_app


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5)
def generate_pdf_async(self, prescription_id: str) -> dict:
    """
    Background task to regenerate a PDF for a prescription.
    Used for retry scenarios when S3 upload fails during approval.
    """
    try:
        # Import here to avoid circular imports at module load
        import asyncio
        from app.database import AsyncSessionLocal
        from app.models import Prescription, Doctor, Patient, PrescriptionStatus
        from app.schemas import StructuredPrescription
        from app.services.pdf_generator import generate_prescription_pdf
        from app.services.s3 import upload_pdf_to_s3
        from app.config import get_settings

        settings = get_settings()

        async def _run():
            async with AsyncSessionLocal() as session:
                rx = await session.get(Prescription, prescription_id)
                if not rx:
                    return {"error": "Prescription not found"}

                doctor = await session.get(Doctor, rx.doctor_id)
                patient = await session.get(Patient, rx.patient_id)
                structured = StructuredPrescription(**rx.structured_json)

                pdf_bytes = generate_prescription_pdf(
                    doctor=doctor,
                    patient=patient,
                    prescription=rx,
                    structured=structured,
                    approved_at=rx.approved_at,
                )

                s3_key = f"prescriptions/{prescription_id}.pdf"
                url = await upload_pdf_to_s3(pdf_bytes, s3_key, settings)

                rx.pdf_s3_key = s3_key
                rx.status = PrescriptionStatus.pdf_generated
                await session.commit()

                return {"pdf_url": url, "prescription_id": prescription_id}

        return asyncio.run(_run())

    except Exception as exc:
        raise self.retry(exc=exc)
