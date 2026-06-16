"""patient card fields and letterhead line 2

Revision ID: 005
Revises: 004
Create Date: 2026-06-13
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("doctors", sa.Column("clinic_address_line2", sa.String(length=255), nullable=True))

    op.add_column("patients", sa.Column("abha_id", sa.String(length=20), nullable=True))

    op.alter_column(
        "patients",
        "name",
        existing_type=sa.String(length=255),
        type_=sa.String(length=200),
        existing_nullable=False,
    )
    op.alter_column(
        "patients",
        "phone",
        existing_type=sa.String(length=20),
        type_=sa.String(length=15),
        existing_nullable=True,
    )

    op.create_index(
        "idx_patients_doctor_phone",
        "patients",
        ["created_by_doctor_id", "phone"],
        unique=False,
        postgresql_where=sa.text("phone IS NOT NULL AND is_active = true"),
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_patients_doctor_phone
        ON patients (created_by_doctor_id, phone)
        WHERE phone IS NOT NULL AND is_active = true
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_patients_name_fts
        ON patients USING gin (to_tsvector('simple', name))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_patients_name_fts")
    op.execute("DROP INDEX IF EXISTS uq_patients_doctor_phone")
    op.drop_index("idx_patients_doctor_phone", table_name="patients")

    op.drop_column("patients", "abha_id")
    op.drop_column("doctors", "clinic_address_line2")

    op.alter_column(
        "patients",
        "phone",
        existing_type=sa.String(length=15),
        type_=sa.String(length=20),
        existing_nullable=True,
    )
    op.alter_column(
        "patients",
        "name",
        existing_type=sa.String(length=200),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
