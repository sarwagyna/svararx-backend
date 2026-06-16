"""add doctor referral field

Revision ID: 011_doctor_referral
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "011_doctor_referral"
down_revision = "010_consultation_record"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "doctors",
        sa.Column("referred_by_doctor_id", sa.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_doctors_referred_by_doctor_id",
        "doctors",
        "doctors",
        ["referred_by_doctor_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_doctors_referred_by_doctor_id",
        "doctors",
        ["referred_by_doctor_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_doctors_referred_by_doctor_id", table_name="doctors")
    op.drop_constraint("fk_doctors_referred_by_doctor_id", "doctors", type_="foreignkey")
    op.drop_column("doctors", "referred_by_doctor_id")
