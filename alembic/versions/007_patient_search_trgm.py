"""pg_trgm index for patient name search; nullable prescription patient_id.

Revision ID: 007_patient_search_trgm
Revises: 006_consultations
Create Date: 2026-06-13
"""
from alembic import op
import sqlalchemy as sa

revision = "007_patient_search_trgm"
down_revision = "006_patient_allergies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_patients_trgm
        ON patients USING gin (name gin_trgm_ops)
        """
    )
    op.alter_column(
        "prescriptions",
        "patient_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "prescriptions",
        "patient_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
    op.execute("DROP INDEX IF EXISTS idx_patients_trgm")
