"""Webhook do provedor: lancamento unico, deduplicacao e nao-regressao.

Cobre os cenarios de aceite 2, 4 e parte do 7:

* ``SETTLED`` produz o lancamento exigido **uma unica vez**, inclusive quando
  concorre com o endpoint de captura;
* reentrega do mesmo ``event_id`` nao reaplica o efeito;
* evento atrasado nao regride a liquidacao;
* estorno gera compensacao vinculada e nao ultrapassa o valor permitido;
* o tenant do pagamento e respeitado, e referencia ambigua e recusada.

Tudo com ``ENCRYPTION_KEY`` ligada: sem o indice cego, nenhuma destas buscas
encontraria o pagamento e os testes passariam por acidente, "sem efeito".
"""

from __future__ import annotations

import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application import settlement, webhook_inbox
from src.application.journal import entry_total
from src.application.payments import (
    resolve_payment_by_gateway_ref,
    update_payment_from_stripe_event,
)
from src.infrastructure.db.models import (
    LedgerEntry,
    OutboxEvent,
    PaymentIntent,
    WebhookInbox,
)
from tests.integration.conftest import new_session, random_ref


def _intent(
    session: Session,
    tenant_id: str,
    *,
    amount: str = "250.00",
    currency: str = "BRL",
    status: str = "AUTHORIZED",
    gateway_ref: str | None = None,
) -> PaymentIntent:
    pi = PaymentIntent(
        tenant_id=tenant_id,
        amount=Decimal(amount),
        currency=currency,
        status=status,
        customer_ref=f"order:{uuid.uuid4().hex[:8]}",
        gateway_ref=gateway_ref or random_ref(),
        gateway_provider="stripe",
    )
    session.add(pi)
    session.commit()
    return pi


def _entries(session: Session, tenant_id: str, pid) -> list[LedgerEntry]:
    session.expire_all()
    return list(
        session.execute(
            select(LedgerEntry).where(
                LedgerEntry.tenant_id == tenant_id,
                LedgerEntry.payment_intent_id == pid,
            )
        )
        .scalars()
        .all()
    )


def _events(session: Session, tenant_id: str, event_type: str) -> list[OutboxEvent]:
    session.expire_all()
    return list(
        session.execute(
            select(OutboxEvent).where(
                OutboxEvent.tenant_id == tenant_id, OutboxEvent.event_type == event_type
            )
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Cenario 2: liquidacao unica
# ---------------------------------------------------------------------------


def test_settled_event_posts_state_journal_and_outbox_together(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    """O defeito original: status mudava sem journal e sem evento."""
    pi = _intent(pg_session, tenant_id)

    changed = update_payment_from_stripe_event(
        pg_session,
        pi.gateway_ref,
        settlement.SETTLED,
        provider="stripe",
        amount=Decimal("250.00"),
        currency="BRL",
    )

    assert changed is True
    pg_session.expire_all()
    refreshed = pg_session.get(PaymentIntent, pi.id)
    assert refreshed is not None
    assert refreshed.status == settlement.SETTLED

    entries = _entries(pg_session, tenant_id, pi.id)
    assert len(entries) == 1, "liquidacao sem lancamento contabil"
    assert entry_total(entries[0]) == Decimal("250.00")
    assert entries[0].fact_key == f"settle:{pi.id}"

    assert len(_events(pg_session, tenant_id, "payment.settled")) == 1


def test_repeated_settled_event_does_not_post_twice(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    pi = _intent(pg_session, tenant_id)

    first = update_payment_from_stripe_event(
        pg_session, pi.gateway_ref, settlement.SETTLED, provider="stripe"
    )
    second = update_payment_from_stripe_event(
        pg_session, pi.gateway_ref, settlement.SETTLED, provider="stripe"
    )

    assert first is True
    assert second is False, "a segunda aplicacao deveria ser reconhecida como ja feita"
    assert len(_entries(pg_session, tenant_id, pi.id)) == 1


def test_webhook_concurrent_with_capture_posts_one_entry(
    pg_engine, pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    """Cenario de aceite 2: webhook e captura simultaneos, um lancamento.

    Duas sessoes independentes tentam liquidar o mesmo pagamento ao mesmo
    tempo, sincronizadas por barreira. Uma grava; a outra encontra o fato ja
    lancado. O desempate e da restricao de unicidade ``(tenant_id, fact_key)``,
    nao de uma verificacao previa.
    """
    pi = _intent(pg_session, tenant_id)
    gateway_ref = pi.gateway_ref
    pi_id = pi.id
    pg_session.commit()

    barrier = threading.Barrier(2, timeout=30)
    outcome: dict[str, object] = {}

    def worker(name: str, source: str) -> None:
        session = new_session(pg_engine)
        try:
            session.execute(select(PaymentIntent).limit(0))  # abre a transacao
            barrier.wait()
            if source == "webhook":
                changed = update_payment_from_stripe_event(
                    session, gateway_ref, settlement.SETTLED, provider="stripe"
                )
            else:
                target = settlement.load_for_update(session, tenant_id, pi_id, instance="capture")
                result = settlement.settle_payment(session, tenant_id, target, source="capture")
                session.commit()
                changed = result.changed
            outcome[name] = "changed" if changed else "noop"
        except Exception as exc:  # noqa: BLE001 - o teste classifica
            session.rollback()
            outcome[name] = exc
        finally:
            session.close()

    threads = [
        threading.Thread(target=worker, args=("webhook", "webhook"), daemon=True),
        threading.Thread(target=worker, args=("capture", "capture"), daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive(), "thread travada ao liquidar em concorrencia"

    errors = {k: v for k, v in outcome.items() if isinstance(v, Exception)}
    assert not errors, f"nenhuma das duas deveria falhar: {errors}"

    entries = _entries(pg_session, tenant_id, pi_id)
    assert len(entries) == 1, f"lancamento duplicado: {outcome}"
    assert entry_total(entries[0]) == Decimal("250.00")

    refreshed = pg_session.get(PaymentIntent, pi_id)
    assert refreshed is not None
    assert refreshed.status == settlement.SETTLED


# ---------------------------------------------------------------------------
# Verificacao do fato
# ---------------------------------------------------------------------------


def test_amount_mismatch_is_refused(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    """Assinatura valida nao prova que o evento se refere a este valor."""
    pi = _intent(pg_session, tenant_id, amount="250.00")

    with pytest.raises(Exception, match="amount mismatch"):
        update_payment_from_stripe_event(
            pg_session,
            pi.gateway_ref,
            settlement.SETTLED,
            provider="stripe",
            amount=Decimal("2500.00"),
        )
    pg_session.rollback()

    assert _entries(pg_session, tenant_id, pi.id) == []
    refreshed = pg_session.get(PaymentIntent, pi.id)
    assert refreshed is not None
    assert refreshed.status == settlement.AUTHORIZED


def test_currency_mismatch_is_refused(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    pi = _intent(pg_session, tenant_id, currency="BRL")

    with pytest.raises(Exception, match="currency mismatch"):
        update_payment_from_stripe_event(
            pg_session,
            pi.gateway_ref,
            settlement.SETTLED,
            provider="stripe",
            amount=Decimal("250.00"),
            currency="USD",
        )
    pg_session.rollback()
    assert _entries(pg_session, tenant_id, pi.id) == []


# ---------------------------------------------------------------------------
# Eventos fora de ordem
# ---------------------------------------------------------------------------


def test_late_authorized_event_does_not_regress_settlement(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    """Cenario de aceite: evento atrasado nao desfaz a liquidacao."""
    pi = _intent(pg_session, tenant_id)
    update_payment_from_stripe_event(
        pg_session, pi.gateway_ref, settlement.SETTLED, provider="stripe"
    )
    pg_session.expire_all()

    changed = update_payment_from_stripe_event(
        pg_session, pi.gateway_ref, settlement.AUTHORIZED, provider="stripe"
    )

    assert changed is False
    pg_session.expire_all()
    refreshed = pg_session.get(PaymentIntent, pi.id)
    assert refreshed is not None
    assert refreshed.status == settlement.SETTLED
    assert len(_entries(pg_session, tenant_id, pi.id)) == 1


def test_voided_after_settled_is_refused(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    """``SETTLED`` nao volta para ``VOIDED``: o dinheiro ja foi reconhecido.

    O cancelamento de um pagamento liquidado e um estorno, nao um void.
    """
    pi = _intent(pg_session, tenant_id)
    update_payment_from_stripe_event(
        pg_session, pi.gateway_ref, settlement.SETTLED, provider="stripe"
    )
    pg_session.expire_all()

    with pytest.raises(Exception, match="cannot void"):
        update_payment_from_stripe_event(
            pg_session, pi.gateway_ref, settlement.VOIDED, provider="stripe"
        )
    pg_session.rollback()
    pg_session.expire_all()
    refreshed = pg_session.get(PaymentIntent, pi.id)
    assert refreshed is not None
    assert refreshed.status == settlement.SETTLED


# ---------------------------------------------------------------------------
# Estorno (cenario de aceite 4)
# ---------------------------------------------------------------------------


def test_refund_creates_a_linked_reversal_and_preserves_the_original(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    pi = _intent(pg_session, tenant_id, amount="250.00")
    update_payment_from_stripe_event(
        pg_session, pi.gateway_ref, settlement.SETTLED, provider="stripe"
    )
    pg_session.expire_all()
    original = _entries(pg_session, tenant_id, pi.id)[0]

    update_payment_from_stripe_event(
        pg_session,
        pi.gateway_ref,
        settlement.REFUNDED,
        provider="stripe",
        amount=Decimal("100.00"),
        event_ref="re_1",
    )
    pg_session.expire_all()

    entries = _entries(pg_session, tenant_id, pi.id)
    assert len(entries) == 2
    reversal = next(e for e in entries if e.entry_type == "REVERSAL")
    assert reversal.reverses_entry_id == original.id
    assert entry_total(reversal) == Decimal("100.00")
    # O lancamento original permanece integro.
    kept = next(e for e in entries if e.id == original.id)
    assert entry_total(kept) == Decimal("250.00")

    refreshed = pg_session.get(PaymentIntent, pi.id)
    assert refreshed is not None
    assert refreshed.status == settlement.PARTIALLY_REFUNDED


def test_refunds_cannot_exceed_the_settled_amount(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    pi = _intent(pg_session, tenant_id, amount="100.00")
    update_payment_from_stripe_event(
        pg_session, pi.gateway_ref, settlement.SETTLED, provider="stripe"
    )
    pg_session.expire_all()

    update_payment_from_stripe_event(
        pg_session,
        pi.gateway_ref,
        settlement.REFUNDED,
        provider="stripe",
        amount=Decimal("70.00"),
        event_ref="re_a",
    )
    pg_session.expire_all()

    with pytest.raises(Exception, match="exceeds refundable"):
        update_payment_from_stripe_event(
            pg_session,
            pi.gateway_ref,
            settlement.REFUNDED,
            provider="stripe",
            amount=Decimal("50.00"),
            event_ref="re_b",
        )
    pg_session.rollback()
    pg_session.expire_all()

    entries = _entries(pg_session, tenant_id, pi.id)
    reversals = [e for e in entries if e.entry_type == "REVERSAL"]
    assert len(reversals) == 1
    assert entry_total(reversals[0]) == Decimal("70.00")


def test_repeated_refund_event_with_the_same_reference_is_idempotent(
    pg_session: Session, tenant_id: str, encryption_on: None
) -> None:
    """A mesma reentrega de ``charge.refunded`` nao estorna duas vezes."""
    pi = _intent(pg_session, tenant_id, amount="100.00")
    update_payment_from_stripe_event(
        pg_session, pi.gateway_ref, settlement.SETTLED, provider="stripe"
    )
    pg_session.expire_all()

    for _ in range(3):
        update_payment_from_stripe_event(
            pg_session,
            pi.gateway_ref,
            settlement.REFUNDED,
            provider="stripe",
            amount=Decimal("30.00"),
            event_ref="re_same",
        )
        pg_session.expire_all()

    reversals = [e for e in _entries(pg_session, tenant_id, pi.id) if e.entry_type == "REVERSAL"]
    assert len(reversals) == 1
    assert entry_total(reversals[0]) == Decimal("30.00")


# ---------------------------------------------------------------------------
# Tenant (cenario de aceite 7)
# ---------------------------------------------------------------------------


def test_ambiguous_gateway_reference_is_refused(
    pg_session: Session, tenant_id: str, other_tenant_id: str, encryption_on: None
) -> None:
    """Referencia compartilhada entre tenants: recusar e melhor que adivinhar.

    O caminho antigo usava ``scalar_one_or_none`` sem tenant: levantaria
    ``MultipleResultsFound`` ou liquidaria o pagamento do tenant errado.
    """
    shared = random_ref()
    _intent(pg_session, tenant_id, gateway_ref=shared)
    _intent(pg_session, other_tenant_id, gateway_ref=shared)

    with pytest.raises(Exception, match="more than one payment intent"):
        resolve_payment_by_gateway_ref(pg_session, shared, provider="stripe")


def test_settlement_touches_only_the_owning_tenant(
    pg_session: Session, tenant_id: str, other_tenant_id: str, encryption_on: None
) -> None:
    mine = _intent(pg_session, tenant_id)
    theirs = _intent(pg_session, other_tenant_id)

    update_payment_from_stripe_event(
        pg_session, mine.gateway_ref, settlement.SETTLED, provider="stripe"
    )
    pg_session.expire_all()

    assert len(_entries(pg_session, tenant_id, mine.id)) == 1
    assert _entries(pg_session, other_tenant_id, theirs.id) == []
    other = pg_session.get(PaymentIntent, theirs.id)
    assert other is not None
    assert other.status == settlement.AUTHORIZED


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------


def test_inbox_registers_once_and_reports_duplicates(pg_session: Session) -> None:
    event_id = f"evt_{uuid.uuid4().hex[:12]}"

    first = webhook_inbox.register(
        pg_session,
        provider="stripe",
        event_id=event_id,
        event_type="payment_intent.succeeded",
        payload={"id": event_id},
    )
    pg_session.commit()
    assert first.is_new is True

    second = webhook_inbox.register(
        pg_session,
        provider="stripe",
        event_id=event_id,
        event_type="payment_intent.succeeded",
        payload={"id": event_id},
    )
    pg_session.commit()
    assert second.is_new is False
    assert second.inbox_id == first.inbox_id

    pg_session.expire_all()
    rows = (
        pg_session.execute(select(WebhookInbox).where(WebhookInbox.event_id == event_id))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].attempts == 2
    pg_session.execute(WebhookInbox.__table__.delete().where(WebhookInbox.event_id == event_id))
    pg_session.commit()


def test_same_event_id_from_different_providers_is_independent(
    pg_session: Session,
) -> None:
    """A unicidade e ``(provider, event_id)``: provedores nao colidem."""
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    a = webhook_inbox.register(
        pg_session, provider="stripe", event_id=event_id, event_type="t", payload={}
    )
    b = webhook_inbox.register(
        pg_session, provider="pagseguro", event_id=event_id, event_type="t", payload={}
    )
    pg_session.commit()
    assert a.is_new and b.is_new
    assert a.inbox_id != b.inbox_id

    pg_session.execute(WebhookInbox.__table__.delete().where(WebhookInbox.event_id == event_id))
    pg_session.commit()
