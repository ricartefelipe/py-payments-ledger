"""Relaxa users.tenant_id NOT NULL em tabela users legada.

O seed do admin global usa tenant_id NULL; o ORM mapeia Optional[str].
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0021_users_tenant_null"
down_revision: Union[str, None] = "0020_users_name_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users'
              AND column_name = 'tenant_id'
          ) THEN
            ALTER TABLE public.users ALTER COLUMN tenant_id DROP NOT NULL;
          END IF;
        END $$;
        """)


def downgrade() -> None:
    pass
