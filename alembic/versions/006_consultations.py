"""Add consultations table for chief complaint capture.

Revision ID: 006_consultations
Revises: 005_patient_card_letterhead
Create Date: 2026-06-13
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "006_consultations"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consultations",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("doctor_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("chief_complaint", sa.Text(), nullable=True),
        sa.Column(
            "chief_complaint_tags",
            postgresql.ARRAY(sa.String(100)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prescription_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"]),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.ForeignKeyConstraint(["prescription_id"], ["prescriptions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prescription_id"),
    )
    op.create_index("idx_consultations_doctor_active", "consultations", ["doctor_id", "completed_at"])


def downgrade() -> None:
    op.drop_index("idx_consultations_doctor_active", table_name="consultations")
    op.drop_table("consultations")
