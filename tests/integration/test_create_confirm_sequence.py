"""Sequência create → confirm → capture com banco real.

Este arquivo existe por causa de um defeito que passou por toda a suíte: a
autorização feita **dentro** de `create_payment_intent` adotava o estado
transitório `AUTHORIZING`, e o `confirm` seguinte recusava com 409. Cada etapa
tinha teste próprio; a **sequência** não tinha, e foi o smoke contra a imagem
que revelou.

Os testes abaixo exercitam o encadeamento real, que é o que o cliente faz.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application import settlement
from src.application.journal import entry_total
from src.application.payments import (
    confirm_payment_intent,
    create_payment_intent,
    post_ledger_for_authorized_payment,
)
from src.application.ports.payment_gateway import GatewayResult, GatewayStatus
from src.infrastructure.db.models import GatewayOperation, LedgerEntry, PaymentIntent


class SequenceGateway:
    """Gateway de teste que registra authorize e capture."""

    def __init__(self) -> None:
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


def _status(session: Session, pid) -> str:
    session.expire_all()
    pi = session.get(PaymentIntent, pid)
    assert pi is not None
    return pi.status


def test_create_leaves_the_payment_in_created(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    """Após criar, o estado público é CREATED — mesmo tendo autorizado."""
    gw = SequenceGateway()
    dto = create_payment_intent(
        pg_session,
        tenant_id,
        Decimal("120.00"),
        "BRL",
        "CUST-SEQ-1",
        gateway=gw,
        idempotency_key=f"seq-{uuid.uuid4().hex[:8]}",
    )

    assert dto.status == settlement.CREATED
    assert _status(pg_session, uuid.UUID(dto.id)) == settlement.CREATED
    assert len(gw.authorizes) == 1
    # A referência do provedor já está gravada.
    pi = pg_session.get(PaymentIntent, uuid.UUID(dto.id))
    assert pi is not None and pi.gateway_ref


def test_create_then_confirm_then_capture(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    """A jornada completa: CREATED → AUTHORIZED → SETTLED com lançamento."""
    gw = SequenceGateway()
    dto = create_payment_intent(
        pg_session,
        tenant_id,
        Decimal("120.00"),
        "BRL",
        "CUST-SEQ-2",
        gateway=gw,
        idempotency_key=f"seq-{uuid.uuid4().hex[:8]}",
    )
    pid = uuid.UUID(dto.id)

    confirmed = confirm_payment_intent(pg_session, tenant_id, pid, gateway=gw)
    assert (
        confirmed.status == settlement.AUTHORIZED
    ), "confirm recusou o pagamento que acabou de ser criado"

    # A autorização não é emitida de novo: já existe referência do provedor.
    assert len(gw.authorizes) == 1

    post_ledger_for_authorized_payment(pg_session, tenant_id, pid, gateway=gw)
    assert _status(pg_session, pid) == settlement.SETTLED
    assert len(gw.captures) == 1

    entries = (
        pg_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.tenant_id == tenant_id, LedgerEntry.payment_intent_id == pid
            )
        )
        .scalars()
        .all()
    )
    assert len(entries) == 1
    assert entry_total(entries[0]) == Decimal("120.00")


def test_confirm_is_idempotent(pg_session: Session, tenant_id: str, encryption_on: None) -> None:
    gw = SequenceGateway()
    dto = create_payment_intent(
        pg_session,
        tenant_id,
        Decimal("50.00"),
        "BRL",
        "CUST-SEQ-3",
        gateway=gw,
        idempotency_key=f"seq-{uuid.uuid4().hex[:8]}",
    )
    pid = uuid.UUID(dto.id)

    first = confirm_payment_intent(pg_session, tenant_id, pid, gateway=gw)
    second = confirm_payment_intent(pg_session, tenant_id, pid, gateway=gw)

    assert first.status == settlement.AUTHORIZED
    assert second.status == settlement.AUTHORIZED
    assert len(gw.authorizes) == 1


def test_create_records_one_authorize_operation(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    """A autorização da criação fica registrada, com a chave do cliente."""
    gw = SequenceGateway()
    client_key = f"seq-{uuid.uuid4().hex[:8]}"
    dto = create_payment_intent(
        pg_session,
        tenant_id,
        Decimal("75.00"),
        "BRL",
        "CUST-SEQ-4",
        gateway=gw,
        idempotency_key=client_key,
    )

    pg_session.expire_all()
    ops = (
        pg_session.execute(
            select(GatewayOperation).where(
                GatewayOperation.tenant_id == tenant_id,
                GatewayOperation.payment_intent_id == uuid.UUID(dto.id),
            )
        )
        .scalars()
        .all()
    )
    assert len(ops) == 1
    assert ops[0].op_type == "AUTHORIZE"
    assert ops[0].status == GatewayOperation.SUCCEEDED
    assert ops[0].idempotency_key == f"authorize:{tenant_id}:{client_key}"
    assert gw.authorizes == [ops[0].idempotency_key]
