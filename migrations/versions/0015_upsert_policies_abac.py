"""Upsert ABAC policies (idempotent) — alinha staging/produção ao seed.

Revision ID: 0015_upsert_policies_abac
Revises: 0014_align_policy_plans_seed
Create Date: 2026-04-03

A migração 0014 só faz UPDATE em linhas existentes com enabled=true.
Esta migração garante INSERT ou UPDATE por permission_code com allowed_plans
mapeados em _PLAN_TIER (evita 403 "Plan enterprise not allowed" por slugs legados).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0015_upsert_policies_abac"
down_revision: Union[str, None] = "0014_align_policy_plans_seed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Alinhado a src/infrastructure/db/seed.py — _upsert_policies
_POLICY_ROWS: list[tuple[str, str, str, str]] = [
    (
        "payments:write",
        "allow",
        "ARRAY['pro','enterprise']::varchar(32)[]",
        "ARRAY['region-a','region-b']::varchar(32)[]",
    ),
    (
        "payments:read",
        "allow",
        "ARRAY['free','pro','enterprise']::varchar(32)[]",
        "ARRAY['region-a','region-b']::varchar(32)[]",
    ),
    (
        "ledger:read",
        "allow",
        "ARRAY['pro','enterprise']::varchar(32)[]",
        "ARRAY['region-a','region-b']::varchar(32)[]",
    ),
    (
        "admin:write",
        "allow",
        "ARRAY['enterprise']::varchar(32)[]",
        "ARRAY['region-a','region-b']::varchar(32)[]",
    ),
    (
        "profile:read",
        "allow",
        "ARRAY['free','pro','enterprise']::varchar(32)[]",
        "ARRAY['region-a','region-b']::varchar(32)[]",
    ),
    (
        "analytics:read",
        "allow",
        "ARRAY['pro','enterprise']::varchar(32)[]",
        "ARRAY['region-a','region-b']::varchar(32)[]",
    ),
]


def upgrade() -> None:
    for perm_code, effect, plans_sql, regions_sql in _POLICY_ROWS:
        op.execute(f"""
            INSERT INTO permissions (code) VALUES ('{perm_code}')
            ON CONFLICT (code) DO NOTHING;
            """)
        op.execute(f"""
            INSERT INTO policies (permission_code, effect, allowed_plans, allowed_regions, enabled)
            VALUES (
                '{perm_code}',
                '{effect}',
                {plans_sql},
                {regions_sql},
                true
            )
            ON CONFLICT (permission_code) DO UPDATE SET
                effect = EXCLUDED.effect,
                allowed_plans = EXCLUDED.allowed_plans,
                allowed_regions = EXCLUDED.allowed_regions,
                enabled = EXCLUDED.enabled;
            """)


def downgrade() -> None:
    pass
