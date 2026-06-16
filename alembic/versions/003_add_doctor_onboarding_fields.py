"""add doctor onboarding fields

Revision ID: 003
Revises: 002
Create Date: 2026-06-13
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "doctors",
        sa.Column("onboarding_step", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "doctors",
        sa.Column("onboarding_completed", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "doctors",
        sa.Column("state_council_reg", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "doctors",
        sa.Column("clinic_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "doctors",
        sa.Column("clinic_address", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "doctors",
        sa.Column("clinic_city", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "doctors",
        sa.Column("clinic_state", sa.String(length=100), server_default="Andhra Pradesh", nullable=True),
    )
    op.add_column(
        "doctors",
        sa.Column("clinic_pin", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "doctors",
        sa.Column("clinic_phone", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "doctors",
        sa.Column("clinic_logo_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "doctors",
        sa.Column("signature_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "doctors",
        sa.Column("voice_calibration_s3_key", sa.String(length=500), nullable=True),
    )

    # Doctors with an active clinic membership have already completed setup.
    op.execute(
        """
        UPDATE doctors d
        SET onboarding_completed = TRUE,
            onboarding_step = 4
        WHERE EXISTS (
            SELECT 1
            FROM doctor_clinics dc
            WHERE dc.doctor_id = d.id
              AND dc.is_active = TRUE
        )
        """
    )


def downgrade() -> None:
    op.drop_column("doctors", "voice_calibration_s3_key")
    op.drop_column("doctors", "signature_url")
    op.drop_column("doctors", "clinic_logo_url")
    op.drop_column("doctors", "clinic_phone")
    op.drop_column("doctors", "clinic_pin")
    op.drop_column("doctors", "clinic_state")
    op.drop_column("doctors", "clinic_city")
    op.drop_column("doctors", "clinic_address")
    op.drop_column("doctors", "clinic_name")
    op.drop_column("doctors", "state_council_reg")
    op.drop_column("doctors", "onboarding_completed")
    op.drop_column("doctors", "onboarding_step")
