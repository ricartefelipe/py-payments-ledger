"""A trilha de auditoria nao pode desfazer o trabalho do chamador.

``_audit`` chamava ``session.rollback()`` quando a gravacao falhava. Como ela e
invocada no fim das operacoes de dominio, uma falha de auditoria desfazia a
mudanca de estado do pagamento **e o lancamento contabil** junto. Alem disso
convertia ``tenant_id`` para ``UUID``, embora ``tenants.id`` seja
``varchar(64)`` desde a migration 0017: para qualquer tenant que nao fosse um
UUID a auditoria era descartada em silencio.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application.journal import CREDIT, DEBIT, LineSpec, post_entry
from src.application.security import _audit
from src.infrastructure.db.models import AuditLog, LedgerEntry, PaymentIntent


def _intent(session: Session, tenant_id: str) -> PaymentIntent:
    pi = PaymentIntent(
        tenant_id=tenant_id,
        amount=Decimal("100.00"),
        currency="BRL",
        status="AUTHORIZED",
        customer_ref="order:audit-1",
        gateway_ref="gw_audit_1",
        gateway_provider="stripe",
    )
    session.add(pi)
    session.commit()
    return pi


def test_audit_accepts_a_non_uuid_tenant_id(pg_session: Session, tenant_id: str) -> None:
    """Tenants sao varchar(64): a auditoria precisa gravar para qualquer um."""
    _audit(pg_session, tenant_id, "actor@example.com", "test.action", "target:1", {"k": "v"})
    pg_session.commit()
    pg_session.expire_all()

    rows = (
        pg_session.execute(select(AuditLog).where(AuditLog.tenant_id == tenant_id)).scalars().all()
    )
    assert len(rows) == 1
    assert rows[0].action == "test.action"
    assert rows[0].detail == {"k": "v"}


def test_failed_audit_preserves_the_ledger_entry(pg_session: Session, tenant_id: str) -> None:
    """Auditoria invalida nao pode levar o lancamento contabil com ela."""
    pi = _intent(pg_session, tenant_id)
    entry = post_entry(
        pg_session,
        tenant_id,
        fact_key=f"settle:{pi.id}",
        lines=[
            LineSpec(DEBIT, "CASH", Decimal("100.00"), "BRL"),
            LineSpec(CREDIT, "REVENUE", Decimal("100.00"), "BRL"),
        ],
        payment_intent_id=pi.id,
    )
    assert entry is not None

    # `target` excede String(256): o insert da auditoria falha no banco.
    _audit(pg_session, tenant_id, "actor", "test.action", "x" * 400, {})
    pg_session.commit()
    pg_session.expire_all()

    kept = (
        pg_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.tenant_id == tenant_id,
                LedgerEntry.fact_key == f"settle:{pi.id}",
            )
        )
        .scalars()
        .all()
    )
    assert len(kept) == 1, "a falha de auditoria desfez o lancamento contabil"
