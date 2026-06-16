"""add vitals table

Revision ID: 008_add_vitals
Revises: 007_patient_search_trgm
Create Date: 2026-06-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "008_add_vitals"
down_revision = "007_patient_search_trgm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vitals",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("consultation_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("patient_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("doctor_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("bp_systolic", sa.Integer(), nullable=True),
        sa.Column("bp_diastolic", sa.Integer(), nullable=True),
        sa.Column("weight_kg", sa.Numeric(5, 2), nullable=True),
        sa.Column("blood_sugar_mg_dl", sa.Integer(), nullable=True),
        sa.Column("blood_sugar_type", sa.String(length=10), nullable=True),
        sa.Column("spo2_percent", sa.Integer(), nullable=True),
        sa.Column("temperature_f", sa.Numeric(4, 1), nullable=True),
        sa.Column("pulse_bpm", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["consultation_id"], ["consultations.id"]),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"]),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "blood_sugar_type IS NULL OR blood_sugar_type IN ('fasting', 'pp', 'random')",
            name="ck_vitals_blood_sugar_type",
        ),
    )
    op.create_index("idx_vitals_patient", "vitals", ["patient_id", sa.text("recorded_at DESC")])


def downgrade() -> None:
    op.drop_index("idx_vitals_patient", table_name="vitals")
    op.drop_table("vitals")
