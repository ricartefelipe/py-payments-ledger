"""init

Revision ID: 0001_init
Revises:
Create Date: 2026-02-15 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("plan", sa.String(length=32), nullable=False, server_default="pro"),
        sa.Column("region", sa.String(length=32), nullable=False, server_default="region-a"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_global_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "roles",
        sa.Column("name", sa.String(length=64), primary_key=True),
    )

    op.create_table(
        "permissions",
        sa.Column("code", sa.String(length=128), primary_key=True),
    )

    op.create_table(
        "user_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role_name", sa.String(length=64), sa.ForeignKey("roles.name"), nullable=False),
        sa.UniqueConstraint("user_id", "role_name", name="uq_user_role"),
    )

    op.create_table(
        "role_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("role_name", sa.String(length=64), sa.ForeignKey("roles.name"), nullable=False),
        sa.Column("permission_code", sa.String(length=128), sa.ForeignKey("permissions.code"), nullable=False),
        sa.UniqueConstraint("role_name", "permission_code", name="uq_role_perm"),
    )

    op.create_table(
        "policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("permission_code", sa.String(length=128), sa.ForeignKey("permissions.code"), nullable=False),
        sa.Column("effect", sa.String(length=16), nullable=False, server_default="allow"),
        sa.Column("allowed_plans", postgresql.ARRAY(sa.String(length=32)), nullable=False, server_default=sa.text("ARRAY[]::text[]")),
        sa.Column("allowed_regions", postgresql.ARRAY(sa.String(length=32)), nullable=False, server_default=sa.text("ARRAY[]::text[]")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("permission_code", name="uq_policy_perm"),
    )

    op.create_table(
        "feature_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("rollout_percent", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("allowed_roles", postgresql.ARRAY(sa.String(length=64)), nullable=False, server_default=sa.text("ARRAY[]::text[]")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_flag_tenant_name"),
    )

    op.create_table(
        "payment_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CREATED", index=True),
        sa.Column("customer_ref", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("payment_intent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("payment_intents.id"), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "ledger_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ledger_entries.id"), nullable=False, index=True),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("event_type", sa.String(length=128), nullable=False, index=True),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING", index=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_outbox_tenant_id"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), nullable=True, index=True),
        sa.Column("actor_sub", sa.String(length=320), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("target", sa.String(length=256), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("outbox_events")
    op.drop_table("ledger_lines")
    op.drop_table("ledger_entries")
    op.drop_table("payment_intents")
    op.drop_table("feature_flags")
    op.drop_table("policies")
    op.drop_table("role_permissions")
    op.drop_table("user_roles")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("users")
    op.drop_table("tenants")
