"""Garante colunas audit_log.target e audit_log.detail (BD legada sem 0016 aplicada).

Quando o baseline usou stamp em 0016 sem executar o corpo de 0016, audit_log pode
existir sem target/detail — o seed e o ORM quebram.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_audit_log_align"
down_revision: Union[str, None] = "0021_users_tenant_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill_audit_target_from_legacy_columns(cols: set[str]) -> None:
    p, m, r = "path" in cols, "method" in cols, "resource_type" in cols
    if p and m and r:
        op.execute(
            sa.text(
                "UPDATE audit_log SET target = LEFT(COALESCE("
                "NULLIF(TRIM(path), ''), NULLIF(TRIM(method), ''), "
                "NULLIF(TRIM(resource_type), ''), 'audit'), 256)"
            )
        )
    elif p and m:
        op.execute(
            sa.text(
                "UPDATE audit_log SET target = LEFT(COALESCE("
                "NULLIF(TRIM(path), ''), NULLIF(TRIM(method), ''), 'audit'), 256)"
            )
        )
    elif p and r:
        op.execute(
            sa.text(
                "UPDATE audit_log SET target = LEFT(COALESCE("
                "NULLIF(TRIM(path), ''), NULLIF(TRIM(resource_type), ''), 'audit'), 256)"
            )
        )
    elif m and r:
        op.execute(
            sa.text(
                "UPDATE audit_log SET target = LEFT(COALESCE("
                "NULLIF(TRIM(method), ''), NULLIF(TRIM(resource_type), ''), 'audit'), 256)"
            )
        )
    elif p:
        op.execute(
            sa.text(
                "UPDATE audit_log SET target = LEFT(COALESCE(NULLIF(TRIM(path), ''), 'audit'), 256)"
            )
        )
    elif m:
        op.execute(
            sa.text(
                "UPDATE audit_log SET target = LEFT(COALESCE(NULLIF(TRIM(method), ''), 'audit'), 256)"
            )
        )
    elif r:
        op.execute(
            sa.text(
                "UPDATE audit_log SET target = LEFT("
                "COALESCE(NULLIF(TRIM(resource_type), ''), 'audit'), 256)"
            )
        )


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if not insp.has_table("audit_log"):
        return
    cols = {c["name"] for c in insp.get_columns("audit_log")}

    if "target" not in cols:
        op.add_column(
            "audit_log",
            sa.Column(
                "target",
                sa.String(256),
                nullable=False,
                server_default=sa.text("'audit'::character varying"),
            ),
        )
        _backfill_audit_target_from_legacy_columns(cols)
        op.alter_column("audit_log", "target", server_default=None)

    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("audit_log")}
    if "detail" not in cols:
        if "details" in cols:
            op.add_column(
                "audit_log",
                sa.Column(
                    "detail",
                    postgresql.JSONB(),
                    nullable=False,
                    server_default=sa.text("'{}'::jsonb"),
                ),
            )
            # Não copiar details::jsonb — dados legados podem não ser JSON válido (ex.: texto "ABAC").
            op.alter_column("audit_log", "detail", server_default=None)
        else:
            op.add_column(
                "audit_log",
                sa.Column(
                    "detail",
                    postgresql.JSONB(),
                    nullable=False,
                    server_default=sa.text("'{}'::jsonb"),
                ),
            )
            op.alter_column("audit_log", "detail", server_default=None)


def downgrade() -> None:
    pass
