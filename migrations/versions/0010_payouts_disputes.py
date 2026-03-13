"""add payouts and disputes tables, make ledger_entries.payment_intent_id nullable

Revision ID: 0010_payouts_disputes
Revises: 0009_payment_links
Create Date: 2026-03-12 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_payouts_disputes"
down_revision = "0009_payment_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payouts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("recipient_id", sa.String(128), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("gateway_ref", sa.String(255), nullable=True),
        sa.Column("bank_account", sa.String(64), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_reason", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_payouts_tenant_id", "payouts", ["tenant_id"])
    op.create_index("ix_payouts_status", "payouts", ["status"])

    op.create_table(
        "disputes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "payment_intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payment_intents.id"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="OPEN"),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("gateway_dispute_ref", sa.String(255), nullable=True),
        sa.Column("evidence", postgresql.JSONB, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_disputes_tenant_id", "disputes", ["tenant_id"])
    op.create_index("ix_disputes_payment_intent_id", "disputes", ["payment_intent_id"])
    op.create_index("ix_disputes_status", "disputes", ["status"])

    op.alter_column(
        "ledger_entries",
        "payment_intent_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "ledger_entries",
        "payment_intent_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.drop_index("ix_disputes_status", table_name="disputes")
    op.drop_index("ix_disputes_payment_intent_id", table_name="disputes")
    op.drop_index("ix_disputes_tenant_id", table_name="disputes")
    op.drop_table("disputes")

    op.drop_index("ix_payouts_status", table_name="payouts")
    op.drop_index("ix_payouts_tenant_id", table_name="payouts")
    op.drop_table("payouts")
