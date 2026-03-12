"""add gateway_configs table for multi-gateway support per tenant

Revision ID: 0004_gateway_configs
Revises: 0003_recurring_charges
Create Date: 2026-03-12 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_gateway_configs"
down_revision = "0003_recurring_charges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateway_configs",
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
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("api_key_ref", sa.String(length=128), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "supported_currencies",
            postgresql.ARRAY(sa.String(length=8)),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column(
            "payment_types",
            postgresql.ARRAY(sa.String(length=32)),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
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
        sa.UniqueConstraint("tenant_id", "provider", name="uq_gateway_config_tenant_provider"),
    )

    op.add_column(
        "payment_intents",
        sa.Column("gateway_provider", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_intents", "gateway_provider")
    op.drop_table("gateway_configs")
