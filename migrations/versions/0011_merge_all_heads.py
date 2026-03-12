"""merge all heads

Revision ID: 0011_merge_all_heads
Revises: 0003_add_invoicing_tables, 0007_split_payments, 0010_payouts_disputes
Create Date: 2026-03-12

"""
from typing import Sequence, Union


revision: str = "0011_merge_all_heads"
down_revision: Union[str, None] = (
    "0003_add_invoicing_tables",
    "0007_split_payments",
    "0010_payouts_disputes",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
