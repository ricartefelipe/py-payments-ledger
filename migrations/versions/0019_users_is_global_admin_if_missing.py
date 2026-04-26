"""Adiciona users.is_global_admin se a tabela users for legada sem a coluna."""

from typing import Sequence, Union

from alembic import op

revision: str = "0019_users_global_admin_col"
down_revision: Union[str, None] = "0018_schema_seed_if_missing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'users'
          ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users'
              AND column_name = 'is_global_admin'
          ) THEN
            ALTER TABLE public.users
              ADD COLUMN is_global_admin BOOLEAN NOT NULL DEFAULT false;
            ALTER TABLE public.users ALTER COLUMN is_global_admin DROP DEFAULT;
          END IF;
        END $$;
        """)


def downgrade() -> None:
    pass
