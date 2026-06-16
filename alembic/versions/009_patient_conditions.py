"""patient conditions and condition suggestions

Revision ID: 009_patient_conditions
Revises: 008_add_vitals
Create Date: 2026-06-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "009_patient_conditions"
down_revision = "008_add_vitals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "patient_conditions" not in tables:
        op.create_table(
            "patient_conditions",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=False),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column(
                "patient_id",
                postgresql.UUID(as_uuid=False),
                sa.ForeignKey("patients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("condition_name", sa.String(200), nullable=False),
            sa.Column("condition_code", sa.String(10), nullable=True),
            sa.Column("diagnosed_at", sa.Date(), nullable=True),
            sa.Column("status", sa.String(20), server_default="active", nullable=False),
            sa.Column(
                "added_by_doctor_id",
                postgresql.UUID(as_uuid=False),
                sa.ForeignKey("doctors.id"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("NOW()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint(
                "status IN ('active', 'resolved', 'monitoring')",
                name="ck_patient_conditions_status",
            ),
        )
        op.create_index(
            "idx_patient_conditions_patient",
            "patient_conditions",
            ["patient_id"],
        )

    if "patient_condition_suggestions" not in tables:
        op.create_table(
            "patient_condition_suggestions",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=False),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column(
                "patient_id",
                postgresql.UUID(as_uuid=False),
                sa.ForeignKey("patients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("condition_name", sa.String(200), nullable=False),
            sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(20), server_default="pending", nullable=False),
            sa.Column(
                "suggested_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("NOW()"),
                nullable=False,
            ),
            sa.Column(
                "reviewed_by_doctor_id",
                postgresql.UUID(as_uuid=False),
                sa.ForeignKey("doctors.id"),
                nullable=True,
            ),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint(
                "status IN ('pending', 'confirmed', 'dismissed')",
                name="ck_patient_condition_suggestions_status",
            ),
        )
        op.create_index(
            "idx_patient_condition_suggestions_patient",
            "patient_condition_suggestions",
            ["patient_id"],
        )


def downgrade() -> None:
    op.drop_index("idx_patient_condition_suggestions_patient", table_name="patient_condition_suggestions")
    op.drop_table("patient_condition_suggestions")
    op.drop_index("idx_patient_conditions_patient", table_name="patient_conditions")
    op.drop_table("patient_conditions")
