"""saved payment methods (tokenization)

Revision ID: 0012_saved_payment_methods
Revises: 0011_merge_all_heads
Create Date: 2026-03-12

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0012_saved_payment_methods"
down_revision: Union[str, None] = "0011_merge_all_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "saved_payment_methods",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False, index=True
        ),
        sa.Column("customer_ref", sa.String(256), nullable=False, index=True),
        sa.Column("gateway_provider", sa.String(32), nullable=False),
        sa.Column("gateway_token", sa.String(512), nullable=False),
        sa.Column("card_last4", sa.String(4), nullable=True),
        sa.Column("card_brand", sa.String(32), nullable=True),
        sa.Column("card_exp_month", sa.Integer, nullable=True),
        sa.Column("card_exp_year", sa.Integer, nullable=True),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_unique_constraint(
        "uq_spm_tenant_gw_token",
        "saved_payment_methods",
        ["tenant_id", "gateway_provider", "gateway_token"],
    )


def downgrade() -> None:
    op.drop_table("saved_payment_methods")
