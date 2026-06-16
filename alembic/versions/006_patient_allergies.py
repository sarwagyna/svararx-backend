"""patient allergies schema

Revision ID: 006
Revises: 005
Create Date: 2026-06-13
"""
from alembic import op
import sqlalchemy as sa

revision = "006_patient_allergies"
down_revision = "006_consultations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "patient_allergies" not in tables:
        op.create_table(
            "patient_allergies",
            sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
            sa.Column("patient_id", sa.UUID(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
            sa.Column("drug_name", sa.String(200), nullable=False),
            sa.Column("drug_generic", sa.String(200), nullable=True),
            sa.Column("reaction", sa.String(500), nullable=True),
            sa.Column("severity", sa.String(20), server_default="unknown", nullable=False),
            sa.Column("reported_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
            sa.Column("reported_by_doctor_id", sa.UUID(), sa.ForeignKey("doctors.id"), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_patient_allergies_patient", "patient_allergies", ["patient_id"])
        return

    columns = {c["name"] for c in inspector.get_columns("patient_allergies")}

    if "allergen" in columns and "drug_name" not in columns:
        op.alter_column("patient_allergies", "allergen", new_column_name="drug_name")
        columns.discard("allergen")
        columns.add("drug_name")

    if "notes" in columns and "reaction" not in columns:
        op.alter_column("patient_allergies", "notes", new_column_name="reaction")
        columns.discard("notes")
        columns.add("reaction")

    if "created_at" in columns and "reported_at" not in columns:
        op.alter_column("patient_allergies", "created_at", new_column_name="reported_at")
        columns.discard("created_at")
        columns.add("reported_at")

    if "drug_generic" not in columns:
        op.add_column("patient_allergies", sa.Column("drug_generic", sa.String(200), nullable=True))

    if "reported_by_doctor_id" not in columns:
        op.add_column(
            "patient_allergies",
            sa.Column("reported_by_doctor_id", sa.UUID(), sa.ForeignKey("doctors.id"), nullable=True),
        )

    if "deleted_at" not in columns:
        op.add_column("patient_allergies", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    if "severity" not in columns:
        op.add_column(
            "patient_allergies",
            sa.Column("severity", sa.String(20), server_default="unknown", nullable=False),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "patient_allergies" not in inspector.get_table_names():
        return

    columns = {c["name"] for c in inspector.get_columns("patient_allergies")}

    if "deleted_at" in columns:
        op.drop_column("patient_allergies", "deleted_at")
    if "reported_by_doctor_id" in columns:
        op.drop_column("patient_allergies", "reported_by_doctor_id")
    if "drug_generic" in columns:
        op.drop_column("patient_allergies", "drug_generic")

    if "drug_name" in columns and "allergen" not in columns:
        op.alter_column("patient_allergies", "drug_name", new_column_name="allergen")
    if "reaction" in columns and "notes" not in columns:
        op.alter_column("patient_allergies", "reaction", new_column_name="notes")
    if "reported_at" in columns and "created_at" not in columns:
        op.alter_column("patient_allergies", "reported_at", new_column_name="created_at")
