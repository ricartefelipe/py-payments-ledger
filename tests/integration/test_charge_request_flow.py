"""Consumer de pedidos: deduplicacao, cobranca unica e resultado incerto.

Estes testes substituem os equivalentes com sessao mockada em
``tests/unit/test_worker_handlers.py``. O fluxo passou a persistir a intencao,
commitar, falar com o provedor fora da transacao e finalizar com
compare-and-set — comportamento que uma ``MagicMock`` de sessao nao exercita.
Aqui, alem das assercoes originais (cria o intent, publica no outbox, reemite em
falha transitoria, emite ``retry_exhausted`` em recusa), provamos o que importa
de verdade: **reentrega da mensagem nao cobra duas vezes**.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application.ports.payment_gateway import GatewayResult, GatewayStatus
from src.infrastructure.db.models import GatewayOperation, OutboxEvent, PaymentIntent
from src.worker.handlers import payments as payment_handlers
from src.worker.handlers.payments import handle_charge_request


class RecordingGateway:
    """Gateway de teste que registra cada chamada e obedece a um roteiro."""

    def __init__(self, results: list[GatewayResult] | None = None) -> None:
        self.calls: list[dict] = []
        self._results = results or []

    async def authorize(
        self,
        tenant_id: str,
        amount: Decimal,
        currency: str,
        customer_ref: str,
        idempotency_key: str,
        payment_method_token: str = "",
    ) -> GatewayResult:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "amount": amount,
                "currency": currency,
                "idempotency_key": idempotency_key,
            }
        )
        if self._results:
            return self._results.pop(0)
        return GatewayResult(
            success=True,
            gateway_ref=f"gw_{uuid.uuid4().hex[:12]}",
            status=GatewayStatus.AUTHORIZED,
        )


@pytest.fixture
def wired_worker(monkeypatch: pytest.MonkeyPatch):
    """Instala um gateway de teste no modulo do worker e restaura depois."""

    def _install(gateway: RecordingGateway):
        monkeypatch.setattr(payment_handlers, "_gateway", gateway, raising=False)
        monkeypatch.setattr(payment_handlers, "_settings", None, raising=False)
        return gateway

    return _install


def _payload(tenant_id: str, order_id: str, amount: str = "250.00", currency: str = "BRL") -> dict:
    return {
        "order_id": order_id,
        "tenant_id": tenant_id,
        "total_amount": amount,
        "currency": currency,
        "correlation_id": f"corr-{order_id}",
    }


def _intents(session: Session, tenant_id: str) -> list[PaymentIntent]:
    # A sessao usa expire_on_commit=False; sem expirar, a consulta devolveria a
    # instancia do identity map com o estado anterior ao commit.
    session.expire_all()
    return list(
        session.execute(select(PaymentIntent).where(PaymentIntent.tenant_id == tenant_id))
        .scalars()
        .all()
    )


def _events(session: Session, tenant_id: str, event_type: str) -> list[OutboxEvent]:
    session.expire_all()
    return list(
        session.execute(
            select(OutboxEvent).where(
                OutboxEvent.tenant_id == tenant_id,
                OutboxEvent.event_type == event_type,
            )
        )
        .scalars()
        .all()
    )


def _operations(session: Session, tenant_id: str) -> list[GatewayOperation]:
    session.expire_all()
    return list(
        session.execute(select(GatewayOperation).where(GatewayOperation.tenant_id == tenant_id))
        .scalars()
        .all()
    )


def test_creates_payment_intent_and_outbox_event(
    pg_session: Session, tenant_id: str, encryption_on: None, wired_worker
) -> None:
    gw = wired_worker(RecordingGateway())
    order_id = f"o-{uuid.uuid4().hex[:8]}"

    handle_charge_request(pg_session, _payload(tenant_id, order_id))

    intents = _intents(pg_session, tenant_id)
    assert len(intents) == 1
    assert intents[0].status == "AUTHORIZED"
    assert Decimal(str(intents[0].amount)) == Decimal("250.00")
    assert intents[0].customer_ref == f"order:{order_id}"
    assert intents[0].gateway_ref

    authorized = _events(pg_session, tenant_id, "payment.authorized")
    assert len(authorized) == 1
    assert authorized[0].payload["order_id"] == order_id
    assert len(gw.calls) == 1


def test_redelivery_does_not_charge_twice(
    pg_session: Session, tenant_id: str, encryption_on: None, wired_worker
) -> None:
    """RabbitMQ entrega pelo menos uma vez: a segunda entrega nao pode cobrar.

    Antes do indice cego, a consulta de deduplicacao comparava igualdade sobre
    ``customer_ref`` cifrado e nunca encontrava o pagamento anterior — cada
    reentrega autorizava de novo.
    """
    gw = wired_worker(RecordingGateway())
    order_id = f"o-{uuid.uuid4().hex[:8]}"
    payload = _payload(tenant_id, order_id)

    handle_charge_request(pg_session, payload)
    handle_charge_request(pg_session, payload)
    handle_charge_request(pg_session, payload)

    assert len(_intents(pg_session, tenant_id)) == 1, "a reentrega criou outro pagamento"
    assert len(gw.calls) == 1, "a reentrega autorizou de novo no provedor"
    assert len(_events(pg_session, tenant_id, "payment.authorized")) == 1


def test_authorization_uses_a_key_anchored_on_the_order(
    pg_session: Session, tenant_id: str, encryption_on: None, wired_worker
) -> None:
    """A chave de idempotencia e estavel por pedido, nao por tentativa."""
    gw = wired_worker(RecordingGateway())
    order_id = f"o-{uuid.uuid4().hex[:8]}"

    handle_charge_request(pg_session, _payload(tenant_id, order_id))

    assert gw.calls[0]["idempotency_key"] == f"charge:{tenant_id}:{order_id}"
    ops = _operations(pg_session, tenant_id)
    assert len(ops) == 1
    assert ops[0].idempotency_key == f"charge:{tenant_id}:{order_id}"
    assert ops[0].status == GatewayOperation.SUCCEEDED
    assert ops[0].op_type == "AUTHORIZE"


def test_transient_failure_is_retried_then_succeeds(
    pg_session: Session, tenant_id: str, encryption_on: None, wired_worker
) -> None:
    """Falha que nao chegou ao provedor pode ser reenviada com a mesma chave."""
    transient = GatewayResult(
        success=False,
        gateway_ref="",
        status=GatewayStatus.FAILED,
        error_code="gateway_unreachable",
        error_message="could not reach the gateway",
        is_retryable=True,
    )
    ok = GatewayResult(success=True, gateway_ref="gw_after_retry", status=GatewayStatus.AUTHORIZED)
    gw = wired_worker(RecordingGateway([transient, ok]))
    order_id = f"o-{uuid.uuid4().hex[:8]}"

    handle_charge_request(pg_session, _payload(tenant_id, order_id))

    assert len(gw.calls) == 2
    # A mesma chave nas duas tentativas: e isso que impede cobranca dupla.
    assert gw.calls[0]["idempotency_key"] == gw.calls[1]["idempotency_key"]
    intents = _intents(pg_session, tenant_id)
    assert len(intents) == 1
    assert intents[0].status == "AUTHORIZED"
    assert len(_events(pg_session, tenant_id, "payment.authorized")) == 1


def test_definitive_decline_emits_retry_exhausted_and_fails_the_payment(
    pg_session: Session, tenant_id: str, encryption_on: None, wired_worker
) -> None:
    declined = GatewayResult(
        success=False,
        gateway_ref="",
        status=GatewayStatus.FAILED,
        error_code="card_declined",
        error_message="insufficient funds",
        is_retryable=False,
    )
    gw = wired_worker(RecordingGateway([declined]))
    order_id = f"o-{uuid.uuid4().hex[:8]}"

    handle_charge_request(pg_session, _payload(tenant_id, order_id))

    assert len(gw.calls) == 1, "recusa definitiva nao deve ser reenviada"
    intents = _intents(pg_session, tenant_id)
    assert len(intents) == 1
    assert intents[0].status == "FAILED"

    exhausted = _events(pg_session, tenant_id, "payment.retry_exhausted")
    assert len(exhausted) == 1
    assert exhausted[0].payload["error_code"] == "card_declined"
    assert exhausted[0].aggregate_id == order_id

    ops = _operations(pg_session, tenant_id)
    assert len(ops) == 1
    assert ops[0].status == GatewayOperation.FAILED


def test_uncertain_result_moves_to_review_and_never_resends(
    pg_session: Session, tenant_id: str, encryption_on: None, wired_worker
) -> None:
    """Timeout apos o envio: o provedor pode ter autorizado.

    O pagamento vai para ``REVIEW`` e a operacao fica ``UNCERTAIN``. Nenhuma
    tentativa nova e emitida — essa e a diferenca entre reconciliar e cobrar
    duas vezes.
    """
    uncertain = GatewayResult(
        success=False,
        gateway_ref="",
        status=GatewayStatus.UNCERTAIN,
        error_code="gateway_timeout_after_send",
        error_message="request was sent but no answer arrived",
        is_retryable=False,
        is_uncertain=True,
    )
    gw = wired_worker(RecordingGateway([uncertain]))
    order_id = f"o-{uuid.uuid4().hex[:8]}"

    handle_charge_request(pg_session, _payload(tenant_id, order_id))

    assert len(gw.calls) == 1, "resultado incerto nao pode ser reenviado"
    intents = _intents(pg_session, tenant_id)
    assert len(intents) == 1
    assert intents[0].status == "REVIEW"

    ops = _operations(pg_session, tenant_id)
    assert len(ops) == 1
    assert ops[0].status == GatewayOperation.UNCERTAIN

    # Nao houve recusa: nada de retry_exhausted nem de FAILED.
    assert _events(pg_session, tenant_id, "payment.retry_exhausted") == []
    assert _events(pg_session, tenant_id, "payment.review") != []


def test_redelivery_after_uncertain_result_still_does_not_resend(
    pg_session: Session, tenant_id: str, encryption_on: None, wired_worker
) -> None:
    """A reentrega de uma mensagem cuja autorizacao ficou incerta nao reenvia."""
    uncertain = GatewayResult(
        success=False,
        gateway_ref="",
        status=GatewayStatus.UNCERTAIN,
        error_code="gateway_timeout_after_send",
        error_message="no answer",
        is_retryable=False,
        is_uncertain=True,
    )
    gw = wired_worker(RecordingGateway([uncertain]))
    order_id = f"o-{uuid.uuid4().hex[:8]}"
    payload = _payload(tenant_id, order_id)

    handle_charge_request(pg_session, payload)
    assert len(gw.calls) == 1

    # Segunda entrega: encontra o pagamento em REVIEW e para.
    handle_charge_request(pg_session, payload)
    assert len(gw.calls) == 1, "a reentrega reenviou uma operacao de desfecho incerto"
    assert len(_intents(pg_session, tenant_id)) == 1


def test_missing_identifiers_are_rejected_without_touching_the_gateway(
    pg_session: Session, tenant_id: str, wired_worker
) -> None:
    gw = wired_worker(RecordingGateway())

    handle_charge_request(pg_session, {"tenant_id": tenant_id, "total_amount": "10"})
    handle_charge_request(pg_session, {"order_id": "o1", "total_amount": "10"})

    assert gw.calls == []
    assert _intents(pg_session, tenant_id) == []


def test_two_tenants_with_the_same_order_id_are_independent(
    pg_session: Session,
    tenant_id: str,
    other_tenant_id: str,
    encryption_on: None,
    wired_worker,
) -> None:
    """A deduplicacao e por tenant: o pedido A-1 de um nao bloqueia o do outro."""
    gw = wired_worker(RecordingGateway())
    order_id = "shared-order-1"

    handle_charge_request(pg_session, _payload(tenant_id, order_id))
    handle_charge_request(pg_session, _payload(other_tenant_id, order_id))

    assert len(_intents(pg_session, tenant_id)) == 1
    assert len(_intents(pg_session, other_tenant_id)) == 1
    assert len(gw.calls) == 2
    keys = {c["idempotency_key"] for c in gw.calls}
    assert keys == {
        f"charge:{tenant_id}:{order_id}",
        f"charge:{other_tenant_id}:{order_id}",
    }
