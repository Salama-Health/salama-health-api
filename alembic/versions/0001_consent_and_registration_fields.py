"""Add consent, registration provenance and injection site.

The app has been sending these since the first build. Pydantic ignores unknown
fields by default, so every registration returned 200 and silently discarded
them - including consentGiven/consentAt, which is the consent record.

This is the repository's first migration. The tables themselves were created by
Base.metadata.create_all() at startup, so this revision only adds the missing
columns; it does not attempt to define the existing schema. alembic_version on
the deployed database exists but holds no row, so `alembic upgrade head` applies
this cleanly.

Every column is nullable. The 1,230 rows already in the database were imported
from seed CSVs that carry none of this, and backfilling a consent record that
was never given would be worse than leaving it null.

Revision ID: 0001_consent_reg
Revises:
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_consent_reg"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("children", sa.Column("consent_given", sa.Boolean(), nullable=True))
    op.add_column("children", sa.Column("consent_at", sa.DateTime(), nullable=True))
    op.add_column(
        "children",
        sa.Column("born_date_estimated", sa.Boolean(), nullable=True,
                  server_default=sa.false()),
    )
    op.add_column("children", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("children", sa.Column("registered_by", sa.String(), nullable=True))
    op.add_column("children", sa.Column("registered_at", sa.DateTime(), nullable=True))
    op.add_column("vaccinations", sa.Column("site", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("vaccinations", "site")
    op.drop_column("children", "registered_at")
    op.drop_column("children", "registered_by")
    op.drop_column("children", "notes")
    op.drop_column("children", "born_date_estimated")
    op.drop_column("children", "consent_at")
    op.drop_column("children", "consent_given")
