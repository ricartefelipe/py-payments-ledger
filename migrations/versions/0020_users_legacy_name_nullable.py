"""Relaxa users.name NOT NULL em tabela users legada (schema fora do 0001).

Algumas BDs tinham colunas extra (ex.: name obrigatório) enquanto o ORM atual
não preenche — o seed falhava no INSERT.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0020_users_name_nullable"
down_revision: Union[str, None] = "0019_users_global_admin_col"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'users'
              AND column_name = 'name'
          ) THEN
            ALTER TABLE public.users ALTER COLUMN name DROP NOT NULL;
          END IF;
        END $$;
        """)


def downgrade() -> None:
    pass
