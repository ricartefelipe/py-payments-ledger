"""multi-currency support: index on exchange_rates and seed default rates

Revision ID: 0006_multi_currency
Revises: 0005_encrypt_sensitive_columns
Create Date: 2026-03-12 00:00:00.000000
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0006_multi_currency"
down_revision = "0005_encrypt_sensitive_columns"
branch_labels = None
depends_on = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def upgrade() -> None:
    op.create_index(
        "ix_exchange_rates_pair_effective",
        "exchange_rates",
        ["from_currency", "to_currency", sa.text("effective_at DESC")],
        unique=False,
    )

    exchange_rates = sa.table(
        "exchange_rates",
        sa.column("id", sa.String),
        sa.column("from_currency", sa.String),
        sa.column("to_currency", sa.String),
        sa.column("rate", sa.Numeric),
        sa.column("effective_at", sa.DateTime),
    )

    now = _utcnow()
    op.bulk_insert(
        exchange_rates,
        [
            {
                "id": str(uuid.uuid4()),
                "from_currency": "USD",
                "to_currency": "BRL",
                "rate": 5.0,
                "effective_at": now,
            },
            {
                "id": str(uuid.uuid4()),
                "from_currency": "EUR",
                "to_currency": "BRL",
                "rate": 5.5,
                "effective_at": now,
            },
            {
                "id": str(uuid.uuid4()),
                "from_currency": "GBP",
                "to_currency": "BRL",
                "rate": 6.3,
                "effective_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM exchange_rates WHERE (from_currency, to_currency) IN "
        "(('USD','BRL'), ('EUR','BRL'), ('GBP','BRL'))"
    )
    op.drop_index("ix_exchange_rates_pair_effective", table_name="exchange_rates")
