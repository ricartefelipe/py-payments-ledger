"""add policies.enabled

Revision ID: 0013_add_policy_enabled
Revises: 0012_saved_payment_methods
Create Date: 2026-03-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_add_policy_enabled"
down_revision: str | None = "0012_saved_payment_methods"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Seed/auth expects `policies.enabled` to exist.
    op.add_column(
        "policies",
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    # Remove default to keep behaviour explicit.
    op.alter_column("policies", "enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("policies", "enabled")
