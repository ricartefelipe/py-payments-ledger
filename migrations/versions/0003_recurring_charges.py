"""add recurring_charges table

Revision ID: 0003_recurring_charges
Revises: 0002_improvements
Create Date: 2026-03-12 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_recurring_charges"
down_revision = "0002_improvements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recurring_charges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("interval", sa.String(length=32), nullable=False),
        sa.Column("next_charge_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="ACTIVE"
        ),
        sa.Column("gateway_customer_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_recurring_charges_status_next",
        "recurring_charges",
        ["status", "next_charge_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_recurring_charges_status_next", table_name="recurring_charges")
    op.drop_table("recurring_charges")
