"""Align policies allowed_plans with application seed (ABAC tier slugs).

Revision ID: 0014_align_policy_plans_seed
Revises: 0013_add_policy_enabled
Create Date: 2026-04-01

Corrige deriva em staging/produção onde allowed_plans só tinha slugs não mapeados
em _PLAN_TIER (tier comparison falhava para enterprise).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0014_align_policy_plans_seed"
down_revision: Union[str, None] = "0013_add_policy_enabled"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Valores alinhados a src/infrastructure/db/seed.py — _upsert_policies
    updates = [
        (
            "payments:write",
            "ARRAY['pro','enterprise']::varchar(32)[]",
            "ARRAY['region-a','region-b']::varchar(32)[]",
        ),
        (
            "payments:read",
            "ARRAY['free','pro','enterprise']::varchar(32)[]",
            "ARRAY['region-a','region-b']::varchar(32)[]",
        ),
        (
            "ledger:read",
            "ARRAY['pro','enterprise']::varchar(32)[]",
            "ARRAY['region-a','region-b']::varchar(32)[]",
        ),
        (
            "admin:write",
            "ARRAY['enterprise']::varchar(32)[]",
            "ARRAY['region-a','region-b']::varchar(32)[]",
        ),
        (
            "profile:read",
            "ARRAY['free','pro','enterprise']::varchar(32)[]",
            "ARRAY['region-a','region-b']::varchar(32)[]",
        ),
        (
            "analytics:read",
            "ARRAY['pro','enterprise']::varchar(32)[]",
            "ARRAY['region-a','region-b']::varchar(32)[]",
        ),
    ]
    for perm, plans_sql, regions_sql in updates:
        op.execute(f"""
            UPDATE policies SET
                effect = 'allow',
                allowed_plans = {plans_sql},
                allowed_regions = {regions_sql}
            WHERE permission_code = '{perm}' AND enabled IS TRUE;
            """)


def downgrade() -> None:
    pass
