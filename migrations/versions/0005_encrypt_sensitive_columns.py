"""encrypt sensitive columns: increase size for encrypted data (AES-256-GCM at rest)

Revision ID: 0005_encrypt_sensitive_columns
Revises: 0004_gateway_configs
Create Date: 2026-03-12 00:00:00.000000

Encrypted values use prefix + base64(nonce+ciphertext+tag), requiring larger storage.
Existing plaintext data remains readable (backward compatible).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_encrypt_sensitive_columns"
down_revision = "0004_gateway_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "payment_intents",
        "customer_ref",
        existing_type=sa.String(length=128),
        type_=sa.String(length=512),
        existing_nullable=False,
    )
    op.alter_column(
        "payment_intents",
        "gateway_ref",
        existing_type=sa.String(length=255),
        type_=sa.String(length=512),
        existing_nullable=True,
    )
    op.alter_column(
        "webhook_endpoints",
        "secret",
        existing_type=sa.String(length=255),
        type_=sa.String(length=512),
        existing_nullable=False,
    )
    op.alter_column(
        "refunds",
        "gateway_ref",
        existing_type=sa.String(length=255),
        type_=sa.String(length=512),
        existing_nullable=True,
    )
    op.alter_column(
        "reconciliation_discrepancies",
        "gateway_ref",
        existing_type=sa.String(length=255),
        type_=sa.String(length=512),
        existing_nullable=True,
    )
    op.alter_column(
        "recurring_charges",
        "gateway_customer_ref",
        existing_type=sa.String(length=255),
        type_=sa.String(length=512),
        existing_nullable=True,
    )
    op.alter_column(
        "gateway_configs",
        "api_key_ref",
        existing_type=sa.String(length=128),
        type_=sa.String(length=512),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "gateway_configs",
        "api_key_ref",
        existing_type=sa.String(length=512),
        type_=sa.String(length=128),
        existing_nullable=True,
    )
    op.alter_column(
        "recurring_charges",
        "gateway_customer_ref",
        existing_type=sa.String(length=512),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "reconciliation_discrepancies",
        "gateway_ref",
        existing_type=sa.String(length=512),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "refunds",
        "gateway_ref",
        existing_type=sa.String(length=512),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "webhook_endpoints",
        "secret",
        existing_type=sa.String(length=512),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "payment_intents",
        "gateway_ref",
        existing_type=sa.String(length=512),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "payment_intents",
        "customer_ref",
        existing_type=sa.String(length=512),
        type_=sa.String(length=128),
        existing_nullable=False,
    )
