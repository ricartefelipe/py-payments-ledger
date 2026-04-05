"""Alinhar tenants.id a varchar(64) em BD legada com tipo uuid.

Bases antigas (ex.: staging) podem ter tenants.id como uuid enquanto o ORM
espera String(64). Isso quebra o seed staging com:
operator does not exist: uuid = character varying

A migração remove FKs para tenants(id), converte colunas tenant_id uuid
e tenants.id para varchar(64), e recria as FKs.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0017_tenant_pk_string_align_legacy"
down_revision: Union[str, None] = "0016_audit_log_target_detail"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # View legada em staging (não está no repositório) bloqueia ALTER em tenant_id.
    op.execute("DROP VIEW IF EXISTS public.v_active_flags_by_tenant CASCADE;")
    op.execute("""
        DO $$
        DECLARE
          r RECORD;
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'tenants'
              AND column_name = 'id' AND udt_name = 'uuid'
          ) THEN
            RETURN;
          END IF;

          CREATE TEMP TABLE _fk_tenant_backup AS
          SELECT c.conname, c.conrelid::regclass::text AS tbl_name,
            pg_get_constraintdef(c.oid) AS def
          FROM pg_constraint c
          WHERE c.contype = 'f' AND c.confrelid = 'public.tenants'::regclass;

          FOR r IN SELECT * FROM _fk_tenant_backup LOOP
            EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tbl_name, r.conname);
          END LOOP;

          FOR r IN
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name <> 'tenants'
              AND column_name = 'tenant_id' AND udt_name = 'uuid'
          LOOP
            EXECUTE format(
              'ALTER TABLE public.%I ALTER COLUMN %I TYPE varchar(64) USING %I::text',
              r.table_name, r.column_name, r.column_name
            );
          END LOOP;

          ALTER TABLE public.tenants
            ALTER COLUMN id TYPE varchar(64) USING id::text;

          FOR r IN SELECT * FROM _fk_tenant_backup LOOP
            EXECUTE format(
              'ALTER TABLE %s ADD CONSTRAINT %I %s',
              r.tbl_name, r.conname, r.def
            );
          END LOOP;
        END $$;
        """)


def downgrade() -> None:
    pass
