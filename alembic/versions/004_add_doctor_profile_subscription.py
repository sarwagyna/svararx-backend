"""add doctor profile subscription fields

Revision ID: 004
Revises: 003
Create Date: 2026-06-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "doctors",
        "qualifications",
        existing_type=sa.String(length=255),
        type_=sa.String(length=200),
        existing_nullable=False,
    )
    op.alter_column(
        "doctors",
        "mci_number",
        existing_type=sa.String(length=100),
        type_=sa.String(length=50),
        existing_nullable=False,
    )
    op.alter_column(
        "doctors",
        "state_council_reg",
        existing_type=sa.String(length=100),
        type_=sa.String(length=50),
        existing_nullable=True,
    )
    op.alter_column(
        "doctors",
        "speciality",
        existing_type=sa.String(length=100),
        type_=sa.String(length=100),
        existing_nullable=False,
    )

    op.add_column(
        "doctors",
        sa.Column(
            "languages",
            postgresql.ARRAY(sa.String(length=50)),
            server_default=sa.text("ARRAY['Telugu','English']::varchar[]"),
            nullable=False,
        ),
    )
    op.add_column(
        "doctors",
        sa.Column(
            "subscription_tier",
            sa.String(length=20),
            server_default="free",
            nullable=False,
        ),
    )
    op.add_column(
        "doctors",
        sa.Column("subscription_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("doctors", "subscription_expires_at")
    op.drop_column("doctors", "subscription_tier")
    op.drop_column("doctors", "languages")
    op.alter_column(
        "doctors",
        "state_council_reg",
        existing_type=sa.String(length=50),
        type_=sa.String(length=100),
        existing_nullable=True,
    )
    op.alter_column(
        "doctors",
        "mci_number",
        existing_type=sa.String(length=50),
        type_=sa.String(length=100),
        existing_nullable=False,
    )
    op.alter_column(
        "doctors",
        "qualifications",
        existing_type=sa.String(length=200),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
