"""Criar tabelas core do seed se faltarem (BD legada com 0001 incompleto).

Quando o primeiro upgrade falha com DuplicateTable em tenants, o stamp+upgrade
pode aplicar 0017 sem ter executado o restante do 0001 — faltam roles, etc.
Esta revisão é idempotente (IF NOT EXISTS) e alinha com models atuais.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0018_schema_seed_if_missing"
down_revision: Union[str, None] = "0017_tenant_pk_varchar"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            name VARCHAR(64) NOT NULL PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS permissions (
            code VARCHAR(128) NOT NULL PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS users (
            id UUID NOT NULL PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) REFERENCES tenants(id),
            email VARCHAR(320) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            is_global_admin BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS role_permissions (
            id UUID NOT NULL PRIMARY KEY DEFAULT gen_random_uuid(),
            role_name VARCHAR(64) NOT NULL REFERENCES roles(name),
            permission_code VARCHAR(128) NOT NULL REFERENCES permissions(code),
            CONSTRAINT uq_role_perm UNIQUE (role_name, permission_code)
        );

        CREATE TABLE IF NOT EXISTS user_roles (
            id UUID NOT NULL PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id),
            role_name VARCHAR(64) NOT NULL REFERENCES roles(name),
            CONSTRAINT uq_user_role UNIQUE (user_id, role_name)
        );

        CREATE TABLE IF NOT EXISTS policies (
            id UUID NOT NULL PRIMARY KEY DEFAULT gen_random_uuid(),
            permission_code VARCHAR(128) NOT NULL REFERENCES permissions(code),
            effect VARCHAR(16) NOT NULL DEFAULT 'allow',
            allowed_plans TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
            allowed_regions TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
            enabled BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_policy_perm UNIQUE (permission_code)
        );

        CREATE TABLE IF NOT EXISTS feature_flags (
            id UUID NOT NULL PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id),
            name VARCHAR(128) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT false,
            rollout_percent INTEGER NOT NULL DEFAULT 100,
            allowed_roles TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_flag_tenant_name UNIQUE (tenant_id, name)
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id UUID NOT NULL PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64),
            actor_sub VARCHAR(320) NOT NULL,
            action VARCHAR(128) NOT NULL,
            target VARCHAR(256) NOT NULL,
            detail JSONB NOT NULL DEFAULT '{}'::jsonb,
            correlation_id VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS account_configs (
            id UUID NOT NULL PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id),
            code VARCHAR(64) NOT NULL,
            label VARCHAR(200) NOT NULL,
            account_type VARCHAR(32) NOT NULL,
            is_default BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_account_config_tenant_code UNIQUE (tenant_id, code)
        );

        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'policies' AND column_name = 'permission_code'
          ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'policies' AND column_name = 'enabled'
          ) THEN
            ALTER TABLE public.policies
              ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT true;
            ALTER TABLE public.policies ALTER COLUMN enabled DROP DEFAULT;
          END IF;
        END $$;
        """)


def downgrade() -> None:
    pass
