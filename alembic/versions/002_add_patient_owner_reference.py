"""add created_by_doctor_id to patients

Revision ID: 002
Revises: 001
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "patients",
        sa.Column(
            "created_by_doctor_id",
            postgresql.UUID(as_uuid=False),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_patients_created_by_doctor_id",
        "patients",
        ["created_by_doctor_id"],
    )
    op.create_foreign_key(
        "fk_patients_created_by_doctor_id_doctors",
        "patients",
        "doctors",
        ["created_by_doctor_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Backfill existing patients with the longest-standing active doctor for each clinic.
    op.execute(
        """
        UPDATE patients
        SET created_by_doctor_id = sub.doctor_id
        FROM (
            SELECT dc.clinic_id, dc.doctor_id
            FROM doctor_clinics dc
            JOIN (
                SELECT clinic_id, MIN(joined_at) AS first_joined
                FROM doctor_clinics
                WHERE is_active = TRUE
                GROUP BY clinic_id
            ) first_dc
            ON dc.clinic_id = first_dc.clinic_id
            AND dc.joined_at = first_dc.first_joined
            WHERE dc.is_active = TRUE
        ) sub
        WHERE patients.clinic_id = sub.clinic_id
          AND patients.created_by_doctor_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_patients_created_by_doctor_id_doctors",
        "patients",
        type_="foreignkey",
    )
    op.drop_index("ix_patients_created_by_doctor_id", table_name="patients")
    op.drop_column("patients", "created_by_doctor_id")
