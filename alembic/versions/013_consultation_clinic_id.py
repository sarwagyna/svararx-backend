"""Add clinic_id to consultations for multi-tenant isolation."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "013_consultation_clinic_id"
down_revision = "012_consultation_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("consultations")}

    if "clinic_id" not in columns:
        op.add_column(
            "consultations",
            sa.Column("clinic_id", postgresql.UUID(as_uuid=False), nullable=True),
        )
        op.create_foreign_key(
            "fk_consultations_clinic_id",
            "consultations",
            "clinics",
            ["clinic_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index("ix_consultations_clinic_id", "consultations", ["clinic_id"])

    # Backfill any rows missing clinic_id
    op.execute(
        """
        UPDATE consultations c
        SET clinic_id = p.clinic_id
        FROM patients p
        WHERE c.patient_id = p.id AND c.clinic_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE consultations c
        SET clinic_id = dc.clinic_id
        FROM doctor_clinics dc
        WHERE c.doctor_id = dc.doctor_id
          AND dc.is_active = true
          AND c.clinic_id IS NULL
        """
    )

    # Enforce NOT NULL only when every row has a clinic_id
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM consultations WHERE clinic_id IS NULL) THEN
                ALTER TABLE consultations ALTER COLUMN clinic_id SET NOT NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("consultations")}
    if "clinic_id" not in columns:
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("consultations")}
    if "ix_consultations_clinic_id" in indexes:
        op.drop_index("ix_consultations_clinic_id", table_name="consultations")
    op.drop_constraint("fk_consultations_clinic_id", "consultations", type_="foreignkey")
    op.drop_column("consultations", "clinic_id")
