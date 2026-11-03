"""Cobrança recorrente de ponta a ponta, com banco real.

A cobrança ficava em ``CREATED``: o worker criava o pagamento e nunca confirmava. Os testes
exercitam a sequência inteira e, principalmente, as retomadas: uma tentativa interrompida não
pode autorizar de novo nem deixar pagamentos soltos.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.application.journal import entry_total
from src.application.payments import create_payment_intent, post_ledger_for_authorized_payment
from src.application.ports.payment_gateway import GatewayResult, GatewayStatus
from src.application.recurring import _cycle_key, process_due_charges
from src.infrastructure.db.models import (
    GatewayOperation,
    LedgerEntry,
    OutboxEvent,
    PaymentIntent,
    RecurringCharge,
)


class RecordingGateway:
    """Registra as chamadas: a prova de não cobrar duas vezes está na contagem."""

    def __init__(self, authorize_result: GatewayResult | None = None) -> None:
        self.authorize_result = authorize_result
        self.authorizes: list[str] = []
        self.captures: list[str] = []

    async def authorize(
        self,
        tenant_id: str,
        amount: Decimal,
        currency: str,
        customer_ref: str,
        idempotency_key: str,
        payment_method_token: str = "",
    ) -> GatewayResult:
        self.authorizes.append(idempotency_key)
        if self.authorize_result is not None:
            return self.authorize_result
        return GatewayResult(
            success=True,
            gateway_ref=f"gw_{uuid.uuid4().hex[:12]}",
            status=GatewayStatus.AUTHORIZED,
        )

    async def capture(
        self, gateway_ref: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> GatewayResult:
        self.captures.append(idempotency_key)
        return GatewayResult(success=True, gateway_ref=gateway_ref, status=GatewayStatus.CAPTURED)


DECLINED = GatewayResult(
    success=False,
    gateway_ref="",
    status=GatewayStatus.FAILED,
    error_code="card_declined",
    error_message="recusado",
)
UNCERTAIN = GatewayResult(
    success=False,
    gateway_ref="",
    status=GatewayStatus.UNCERTAIN,
    error_code="timeout",
    is_uncertain=True,
    error_message="sem resposta",
)


@pytest.fixture
def charge(pg_session: Session, tenant_id: str) -> Generator[RecurringCharge, None, None]:
    now = datetime.now(UTC)
    rc = RecurringCharge(
        tenant_id=tenant_id,
        description="Plano mensal",
        amount_cents=29900,
        currency="BRL",
        interval="monthly",
        next_charge_at=now - timedelta(minutes=5),
        status="ACTIVE",
        created_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=30),
    )
    pg_session.add(rc)
    pg_session.commit()
    yield rc
    pg_session.rollback()
    pg_session.execute(text("DELETE FROM recurring_charges WHERE tenant_id = :t"), {"t": tenant_id})
    pg_session.commit()


def _payments(session: Session, tenant_id: str) -> list[PaymentIntent]:
    session.expire_all()
    return list(
        session.execute(select(PaymentIntent).where(PaymentIntent.tenant_id == tenant_id))
        .scalars()
        .all()
    )


def _reset_due(session: Session, rc: RecurringCharge) -> datetime:
    session.refresh(rc)
    return rc.next_charge_at


def test_cobranca_vencida_chega_a_liquidada_com_lancamento(
    pg_session: Session, tenant_id: str, charge: RecurringCharge, encryption_on: None
) -> None:
    due = charge.next_charge_at
    gateway = RecordingGateway()

    assert process_due_charges(pg_session, gateway=gateway) == 1

    [pi] = _payments(pg_session, tenant_id)
    assert pi.status == "AUTHORIZED"
    assert pi.amount == Decimal("299.00")
    assert _reset_due(pg_session, charge) > datetime.now(UTC) > due

    # O evento é o que aciona a captura no consumidor do worker.
    events = pg_session.execute(
        select(OutboxEvent.event_type).where(OutboxEvent.aggregate_id == str(pi.id))
    ).scalars()
    assert "payment.authorized" in set(events)

    post_ledger_for_authorized_payment(pg_session, tenant_id, pi.id, gateway=gateway)
    pg_session.commit()

    [pi] = _payments(pg_session, tenant_id)
    assert pi.status == "SETTLED"
    [entry] = (
        pg_session.execute(select(LedgerEntry).where(LedgerEntry.payment_intent_id == pi.id))
        .scalars()
        .all()
    )
    assert entry_total(entry) == Decimal("299.00")
    assert len(gateway.authorizes) == 1
    assert len(gateway.captures) == 1


def test_segunda_passada_nao_cobra_de_novo(
    pg_session: Session, tenant_id: str, charge: RecurringCharge
) -> None:
    gateway = RecordingGateway()

    assert process_due_charges(pg_session, gateway=gateway) == 1
    assert process_due_charges(pg_session, gateway=gateway) == 0

    assert len(_payments(pg_session, tenant_id)) == 1
    assert len(gateway.authorizes) == 1


def test_queda_antes_de_confirmar_retoma_o_mesmo_pagamento(
    pg_session: Session, tenant_id: str, charge: RecurringCharge
) -> None:
    gateway = RecordingGateway()
    create_payment_intent(
        pg_session,
        tenant_id,
        Decimal("299.00"),
        "BRL",
        f"recurring:{charge.id}",
        gateway=gateway,
        idempotency_key=_cycle_key(charge),
    )
    pg_session.commit()

    assert process_due_charges(pg_session, gateway=gateway) == 1

    [pi] = _payments(pg_session, tenant_id)
    assert pi.status == "AUTHORIZED"
    assert len(gateway.authorizes) == 1


def test_queda_antes_de_gravar_a_referencia_usa_a_da_operacao(
    pg_session: Session, tenant_id: str, charge: RecurringCharge
) -> None:
    gateway = RecordingGateway()
    dto = create_payment_intent(
        pg_session,
        tenant_id,
        Decimal("299.00"),
        "BRL",
        f"recurring:{charge.id}",
        gateway=gateway,
        idempotency_key=_cycle_key(charge),
    )
    pi = pg_session.get(PaymentIntent, uuid.UUID(dto.id))
    ref = pi.gateway_ref
    pi.gateway_ref = None
    pg_session.commit()

    assert process_due_charges(pg_session, gateway=gateway) == 1

    [pi] = _payments(pg_session, tenant_id)
    assert pi.status == "AUTHORIZED"
    assert pi.gateway_ref == ref
    assert len(gateway.authorizes) == 1


def test_recusa_encerra_o_ciclo_sem_repetir(
    pg_session: Session, tenant_id: str, charge: RecurringCharge
) -> None:
    gateway = RecordingGateway(DECLINED)

    assert process_due_charges(pg_session, gateway=gateway) == 1
    assert process_due_charges(pg_session, gateway=gateway) == 0

    [pi] = _payments(pg_session, tenant_id)
    assert pi.status == "FAILED"
    assert _reset_due(pg_session, charge) > datetime.now(UTC)
    assert len(gateway.authorizes) == 1


def test_desfecho_incerto_nao_avanca_nem_autoriza_de_novo(
    pg_session: Session, tenant_id: str, charge: RecurringCharge
) -> None:
    due = charge.next_charge_at
    gateway = RecordingGateway(UNCERTAIN)

    assert process_due_charges(pg_session, gateway=gateway) == 0
    assert process_due_charges(pg_session, gateway=gateway) == 0

    [pi] = _payments(pg_session, tenant_id)
    assert pi.status == "REVIEW"
    assert _reset_due(pg_session, charge) == due
    assert len(gateway.authorizes) == 1


@pytest.mark.parametrize(
    ("age", "expected"),
    [(timedelta(seconds=1), "CREATED"), (timedelta(minutes=5), "REVIEW")],
)
def test_operacao_pendente_espera_e_depois_vai_para_revisao(
    pg_session: Session,
    tenant_id: str,
    charge: RecurringCharge,
    age: timedelta,
    expected: str,
) -> None:
    """Tentativa que morreu com a autorização em voo: reautorizar poderia cobrar duas vezes."""
    due = charge.next_charge_at
    gateway = RecordingGateway()
    create_payment_intent(
        pg_session,
        tenant_id,
        Decimal("299.00"),
        "BRL",
        f"recurring:{charge.id}",
        gateway=gateway,
        idempotency_key=_cycle_key(charge),
    )
    [pi] = _payments(pg_session, tenant_id)
    pi.gateway_ref = None
    op = pg_session.execute(
        select(GatewayOperation).where(GatewayOperation.payment_intent_id == pi.id)
    ).scalar_one()
    op.status = GatewayOperation.PENDING
    op.provider_ref = None
    op.updated_at = datetime.now(UTC) - age
    pg_session.commit()

    assert process_due_charges(pg_session, gateway=gateway) == 0

    [pi] = _payments(pg_session, tenant_id)
    assert pi.status == expected
    assert _reset_due(pg_session, charge) == due
    assert len(gateway.authorizes) == 1
    assert (
        pg_session.scalar(
            select(func.count())
            .select_from(GatewayOperation)
            .where(GatewayOperation.tenant_id == tenant_id)
        )
        == 1
    )


def test_cobranca_pausada_nao_e_processada(
    pg_session: Session, tenant_id: str, charge: RecurringCharge
) -> None:
    charge.status = "PAUSED"
    pg_session.commit()
    gateway = RecordingGateway()

    assert process_due_charges(pg_session, gateway=gateway) == 0

    assert _payments(pg_session, tenant_id) == []
    assert gateway.authorizes == []
