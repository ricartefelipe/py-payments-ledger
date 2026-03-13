"""split payments: create payment_splits table

Revision ID: 0007_split_payments
Revises: 0006_multi_currency
Create Date: 2026-03-12 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_split_payments"
down_revision = "0006_multi_currency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_splits",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "payment_intent_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payment_intents.id"),
            nullable=False,
        ),
        sa.Column("recipient_id", sa.String(128), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("transferred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_payment_splits_tenant_id", "payment_splits", ["tenant_id"])
    op.create_index("ix_payment_splits_payment_intent_id", "payment_splits", ["payment_intent_id"])


def downgrade() -> None:
    op.drop_index("ix_payment_splits_payment_intent_id", table_name="payment_splits")
    op.drop_index("ix_payment_splits_tenant_id", table_name="payment_splits")
    op.drop_table("payment_splits")
