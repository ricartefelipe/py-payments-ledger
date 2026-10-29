"""Integridade financeira: indice cego, journal imutavel, registro de operacao e inbox.

Esta migration fecha quatro lacunas que se sustentam mutuamente:

1. **Indice cego.** ``payment_intents.gateway_ref`` e ``customer_ref`` sao
   cifrados com nonce aleatorio, logo ``WHERE gateway_ref = :v`` nunca casa.
   Webhook, reconciliacao e deduplicacao de pedido dependiam dessa busca e
   falhavam em silencio sempre que ``ENCRYPTION_KEY`` estava configurada.

2. **Journal imutavel e deduplicado por fato.** ``ledger_entries`` ganha
   ``fact_key`` com unicidade por tenant, vinculo de reversao e triggers que
   recusam UPDATE/DELETE. Correcao passa a ser lancamento compensatorio.

3. **Registro de operacao no gateway.** Identidade duravel por obrigacao e
   operacao, com estado proprio para resultado incerto (timeout apos aceite).

4. **Inbox de webhook.** Deduplicacao por ``(provider, event_id)``.

Compatibilidade com dados existentes
------------------------------------
Os indices cegos sao calculados no upgrade a partir do valor ja decifrado pelo
ORM, portanto a migration funciona tanto em banco vazio quanto em banco com
historico. ``ledger_entries`` legadas recebem ``fact_key = 'legacy:<id>'``: um
valor unico por linha, que preserva eventuais duplicatas ja gravadas em vez de
abortar o upgrade. Essas duplicatas aparecem no relatorio de reconciliacao.

**Rotacao de chave:** o indice cego e derivado de ``ENCRYPTION_KEY`` via HKDF.
Trocar essa chave invalida os indices; rode ``scripts/reindex_blind_index.py``
na janela de rotacao. Ver ``docs/operations.md``.

Revision ID: 0023_financial_integrity
Revises: 0022_audit_log_align
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_financial_integrity"
down_revision: str | None = "0022_audit_log_align"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_table(bind, table: str) -> bool:
    return table in set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Indice cego para as colunas cifradas usadas em busca por igualdade
    # ------------------------------------------------------------------
    if not _has_column(bind, "payment_intents", "gateway_ref_bidx"):
        op.add_column(
            "payment_intents", sa.Column("gateway_ref_bidx", sa.String(64), nullable=True)
        )
        op.create_index(
            "ix_payment_intents_gateway_ref_bidx",
            "payment_intents",
            ["tenant_id", "gateway_ref_bidx"],
        )
    if not _has_column(bind, "payment_intents", "customer_ref_bidx"):
        op.add_column(
            "payment_intents", sa.Column("customer_ref_bidx", sa.String(64), nullable=True)
        )
        op.create_index(
            "ix_payment_intents_customer_ref_bidx",
            "payment_intents",
            ["tenant_id", "customer_ref_bidx"],
        )

    # Versao para CAS: permite finalizar sem manter row lock durante a rede.
    if not _has_column(bind, "payment_intents", "version"):
        op.add_column(
            "payment_intents",
            sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        )

    if _has_table(bind, "saved_payment_methods") and not _has_column(
        bind, "saved_payment_methods", "gateway_token_bidx"
    ):
        op.add_column(
            "saved_payment_methods", sa.Column("gateway_token_bidx", sa.String(64), nullable=True)
        )

    _backfill_blind_indexes(bind)

    # A unicidade sobre o token cifrado nunca disparava (nonce aleatorio por
    # escrita); passa para o indice cego, onde e efetiva.
    if _has_table(bind, "saved_payment_methods"):
        existing = {
            c["name"] for c in sa.inspect(bind).get_unique_constraints("saved_payment_methods")
        }
        if "uq_spm_tenant_gw_token" in existing:
            op.drop_constraint("uq_spm_tenant_gw_token", "saved_payment_methods", type_="unique")
        op.create_unique_constraint(
            "uq_spm_tenant_gw_token_bidx",
            "saved_payment_methods",
            ["tenant_id", "gateway_provider", "gateway_token_bidx"],
        )

    # ------------------------------------------------------------------
    # 2. Journal: tipo, vinculo de reversao, dedupe por fato e imutabilidade
    # ------------------------------------------------------------------
    if not _has_column(bind, "ledger_entries", "entry_type"):
        op.add_column(
            "ledger_entries",
            sa.Column("entry_type", sa.String(16), nullable=False, server_default="ORIGINAL"),
        )
    if not _has_column(bind, "ledger_entries", "reverses_entry_id"):
        op.add_column(
            "ledger_entries",
            sa.Column("reverses_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_ledger_entries_reverses",
            "ledger_entries",
            "ledger_entries",
            ["reverses_entry_id"],
            ["id"],
        )
    if not _has_column(bind, "ledger_entries", "fact_key"):
        op.add_column("ledger_entries", sa.Column("fact_key", sa.String(160), nullable=True))
        # Lancamentos legados recebem chave unica propria: preserva o historico
        # existente (inclusive duplicatas ja gravadas) sem abortar o upgrade.
        op.execute(
            "UPDATE ledger_entries SET fact_key = 'legacy:' || id::text WHERE fact_key IS NULL"
        )
        # O default no banco e o que torna este upgrade compativel com a versao
        # anterior do codigo, que monta `LedgerEntry` sem `fact_key`: um INSERT
        # sem a coluna recebe uma chave unica em vez de violar NOT NULL. Isso
        # permite upgrade com instancias antigas ainda de pe e, principalmente,
        # permite **rollback de imagem** sem downgrade de schema. Lancamentos
        # assim nao participam da deduplicacao por fato, e e exatamente isso que
        # a versao antiga do codigo nao oferecia.
        op.alter_column(
            "ledger_entries",
            "fact_key",
            nullable=False,
            server_default=sa.text("'legacy:' || gen_random_uuid()::text"),
        )
        op.create_unique_constraint(
            "uq_ledger_entries_tenant_fact", "ledger_entries", ["tenant_id", "fact_key"]
        )

    # Append-only no nivel do banco: nenhuma camada de aplicacao, script ad-hoc
    # ou console pode reescrever um lancamento ja postado.
    op.execute("""
        CREATE OR REPLACE FUNCTION ledger_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'ledger is append-only: % on % is not allowed; post a compensating entry',
                TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """)
    for table in ("ledger_entries", "ledger_lines"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute(f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
            """)

    # ------------------------------------------------------------------
    # 3. Registro de operacao no gateway
    # ------------------------------------------------------------------
    if not _has_table(bind, "gateway_operations"):
        op.create_table(
            "gateway_operations",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "tenant_id",
                sa.String(64),
                sa.ForeignKey("tenants.id"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "payment_intent_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("payment_intents.id"),
                nullable=True,
                index=True,
            ),
            sa.Column("op_type", sa.String(16), nullable=False),
            sa.Column("idempotency_key", sa.String(160), nullable=False),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("provider_ref", sa.String(512), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
            sa.Column("amount", sa.Numeric(18, 2), nullable=True),
            sa.Column("currency", sa.String(8), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error_message", sa.String(1024), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_gwop_tenant_idem"),
        )
        op.create_index("ix_gwop_pending", "gateway_operations", ["status", "created_at"])

    # ------------------------------------------------------------------
    # 4. Inbox de webhook
    # ------------------------------------------------------------------
    if not _has_table(bind, "webhook_inbox"):
        op.create_table(
            "webhook_inbox",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("event_id", sa.String(255), nullable=False),
            sa.Column("event_type", sa.String(128), nullable=False),
            sa.Column("tenant_id", sa.String(64), nullable=True),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="RECEIVED"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.String(1024), nullable=True),
            sa.Column(
                "received_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("provider", "event_id", name="uq_webhook_inbox_provider_event"),
        )
        op.create_index("ix_webhook_inbox_status", "webhook_inbox", ["status", "received_at"])


def _backfill_blind_indexes(bind) -> None:
    """Calcula os indices cegos a partir dos valores ja decifrados pelo ORM."""
    from sqlalchemy.orm import Session

    from src.infrastructure.db.models import PaymentIntent, SavedPaymentMethod
    from src.shared.blind_index import blind_index_for

    session = Session(bind=bind)
    updated = 0
    for pi in session.execute(sa.select(PaymentIntent)).scalars():
        pi.gateway_ref_bidx = blind_index_for(pi.gateway_ref)
        pi.customer_ref_bidx = blind_index_for(pi.customer_ref)
        updated += 1
    if _has_table(bind, "saved_payment_methods"):
        for spm in session.execute(sa.select(SavedPaymentMethod)).scalars():
            spm.gateway_token_bidx = blind_index_for(spm.gateway_token)
    session.flush()
    print(f"[0023] blind index calculado para {updated} payment_intents")


def downgrade() -> None:
    for table in ("ledger_entries", "ledger_lines"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS ledger_append_only()")

    op.drop_table("webhook_inbox")
    op.drop_table("gateway_operations")

    op.drop_constraint("uq_ledger_entries_tenant_fact", "ledger_entries", type_="unique")
    op.drop_column("ledger_entries", "fact_key")
    op.drop_constraint("fk_ledger_entries_reverses", "ledger_entries", type_="foreignkey")
    op.drop_column("ledger_entries", "reverses_entry_id")
    op.drop_column("ledger_entries", "entry_type")

    op.drop_constraint("uq_spm_tenant_gw_token_bidx", "saved_payment_methods", type_="unique")
    op.create_unique_constraint(
        "uq_spm_tenant_gw_token",
        "saved_payment_methods",
        ["tenant_id", "gateway_provider", "gateway_token"],
    )
    op.drop_column("saved_payment_methods", "gateway_token_bidx")

    op.drop_column("payment_intents", "version")
    op.drop_index("ix_payment_intents_customer_ref_bidx", "payment_intents")
    op.drop_column("payment_intents", "customer_ref_bidx")
    op.drop_index("ix_payment_intents_gateway_ref_bidx", "payment_intents")
    op.drop_column("payment_intents", "gateway_ref_bidx")
