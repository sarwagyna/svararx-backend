"""add auth_user_id to doctors

Revision ID: 001
Revises:
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "doctors",
        sa.Column("auth_user_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.create_index(
        "ix_doctors_auth_user_id", "doctors", ["auth_user_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_doctors_auth_user_id", table_name="doctors")
    op.drop_column("doctors", "auth_user_id")
