"""consultation EMR record fields and extended vitals

Revision ID: 010_consultation_record
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "010_consultation_record"
down_revision = "009_patient_conditions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "consultations",
        sa.Column("visit_type", sa.String(20), nullable=False, server_default="new"),
    )
    op.add_column(
        "consultations",
        sa.Column(
            "record_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "consultations",
        sa.Column("record_status", sa.String(20), nullable=False, server_default="draft"),
    )
    op.add_column("consultations", sa.Column("raw_transcript", sa.Text(), nullable=True))
    op.add_column("consultations", sa.Column("corrected_transcript", sa.Text(), nullable=True))
    op.add_column("consultations", sa.Column("approved_transcript", sa.Text(), nullable=True))
    op.add_column("consultations", sa.Column("ai_summary", sa.Text(), nullable=True))

    op.add_column("patients", sa.Column("address", sa.String(500), nullable=True))
    op.add_column("patients", sa.Column("occupation", sa.String(200), nullable=True))

    op.add_column("vitals", sa.Column("height_cm", sa.Numeric(5, 1), nullable=True))
    op.add_column("vitals", sa.Column("respiratory_rate", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("vitals", "respiratory_rate")
    op.drop_column("vitals", "height_cm")
    op.drop_column("patients", "occupation")
    op.drop_column("patients", "address")
    op.drop_column("consultations", "ai_summary")
    op.drop_column("consultations", "approved_transcript")
    op.drop_column("consultations", "corrected_transcript")
    op.drop_column("consultations", "raw_transcript")
    op.drop_column("consultations", "record_status")
    op.drop_column("consultations", "record_json")
    op.drop_column("consultations", "visit_type")
