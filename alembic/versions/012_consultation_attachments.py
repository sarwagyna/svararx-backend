"""Add consultation_attachments for patient record file uploads."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "012_consultation_attachments"
down_revision = "011_doctor_referral"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "consultation_attachments" not in inspector.get_table_names():
        op.create_table(
            "consultation_attachments",
            sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
            sa.Column("consultation_id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("patient_id", postgresql.UUID(as_uuid=False), nullable=True),
            sa.Column("doctor_id", postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column("filename", sa.String(255), nullable=False),
            sa.Column("mime_type", sa.String(100), nullable=True),
            sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("storage_key", sa.String(500), nullable=False),
            sa.Column("category", sa.String(30), nullable=False, server_default="other"),
            sa.Column("ocr_text", sa.Text(), nullable=True),
            sa.Column("ocr_status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["consultation_id"], ["consultations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
            sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    indexes = {idx["name"] for idx in inspector.get_indexes("consultation_attachments")}
    if "ix_consultation_attachments_consultation_id" not in indexes:
        op.create_index(
            "ix_consultation_attachments_consultation_id",
            "consultation_attachments",
            ["consultation_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "consultation_attachments" not in inspector.get_table_names():
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("consultation_attachments")}
    if "ix_consultation_attachments_consultation_id" in indexes:
        op.drop_index("ix_consultation_attachments_consultation_id", table_name="consultation_attachments")
    op.drop_table("consultation_attachments")
