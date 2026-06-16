"""Add practice_mode to doctors for tier-aware onboarding."""
from alembic import op
import sqlalchemy as sa

revision = "014_doctor_practice_mode"
down_revision = "013_consultation_clinic_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "doctors",
        sa.Column("practice_mode", sa.String(20), nullable=False, server_default="solo"),
    )


def downgrade() -> None:
    op.drop_column("doctors", "practice_mode")
