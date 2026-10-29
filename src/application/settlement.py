"""Maquina de estados do pagamento e finalizacao unica.

Antes deste modulo, cada caminho que mudava o estado de um pagamento tinha a sua
propria regra: o endpoint de captura lancava no journal e publicava no outbox; o
webhook do Stripe apenas trocava o ``status`` e commitava, sem journal e sem
evento; a reconciliacao mexia no status por outro caminho ainda. O resultado era
um pagamento ``SETTLED`` sem nenhum lancamento contabil — estado financeiro sem
fato economico.

Aqui existe **uma** operacao de dominio por fato (``settle``, ``void``,
``refund``) e todos os caminhos — comando HTTP, consumer, webhook e
reconciliacao — convergem nela. Cada operacao, dentro de uma unica transacao:

* valida a transicao de estado contra ``ALLOWED_TRANSITIONS``;
* confere que valor, moeda e referencia do provedor batem com o registro local;
* lanca no journal com ``fact_key`` deduplicado pelo banco;
* atualiza ``status`` e incrementa ``version``;
* grava o evento no outbox e as entregas de webhook.

Nenhuma funcao daqui faz chamada de rede. A conversa com o gateway acontece em
``src/application/gateway_ops.py``, fora de qualquer lock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application.event_broadcaster import broadcaster
from src.application.journal import (
    CREDIT,
    DEBIT,
    LineSpec,
    entry_total,
    post_entry,
    resolve_account,
    reverse_entry,
    reversed_total,
)
from src.application.security import _audit
from src.application.webhooks import enqueue_webhook_deliveries
from src.infrastructure.db.models import LedgerEntry, OutboxEvent, PaymentIntent
from src.shared.correlation import get_correlation_id, get_subject
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Estados
# ---------------------------------------------------------------------------

CREATED = "CREATED"
AUTHORIZING = "AUTHORIZING"
AUTHORIZED = "AUTHORIZED"
CAPTURING = "CAPTURING"
SETTLED = "SETTLED"
PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
REFUNDED = "REFUNDED"
VOIDED = "VOIDED"
FAILED = "FAILED"
#: Resultado incerto no provedor (timeout apos possivel aceite). Nao e recusa:
#: a reconciliacao resolve consultando o provedor com a mesma chave.
REVIEW = "REVIEW"

#: Transicoes permitidas. O que **nao** esta aqui e o ponto principal:
#: nada sai de ``SETTLED``, ``REFUNDED``, ``VOIDED`` ou ``FAILED`` de volta para
#: um estado anterior, portanto um evento atrasado do provedor nao regride a
#: liquidacao.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    CREATED: frozenset({AUTHORIZING, AUTHORIZED, SETTLED, FAILED, VOIDED, REVIEW}),
    AUTHORIZING: frozenset({AUTHORIZED, FAILED, REVIEW}),
    AUTHORIZED: frozenset({CAPTURING, SETTLED, VOIDED, FAILED, REVIEW}),
    CAPTURING: frozenset({SETTLED, AUTHORIZED, FAILED, REVIEW}),
    SETTLED: frozenset({PARTIALLY_REFUNDED, REFUNDED}),
    PARTIALLY_REFUNDED: frozenset({PARTIALLY_REFUNDED, REFUNDED}),
    REVIEW: frozenset({AUTHORIZED, SETTLED, FAILED, VOIDED}),
    REFUNDED: frozenset(),
    VOIDED: frozenset(),
    FAILED: frozenset(),
}

TERMINAL_STATES = frozenset({REFUNDED, VOIDED, FAILED})

#: Estados em que o dinheiro ja foi reconhecido no journal.
SETTLED_STATES = frozenset({SETTLED, PARTIALLY_REFUNDED, REFUNDED})


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class SettlementOutcome:
    """O que a operacao fez, para o chamador poder responder com precisao."""

    status: str
    changed: bool
    entry_id: str | None = None
    #: ``True`` quando o fato ja estava registrado e nada novo foi gravado.
    already_applied: bool = False


# ---------------------------------------------------------------------------
# Carregamento com lock curto
# ---------------------------------------------------------------------------


def load_for_update(session: Session, tenant_id: str, pid: Any, *, instance: str) -> PaymentIntent:
    """Carrega o pagamento com ``FOR UPDATE``, sempre no escopo do tenant."""
    pi = session.execute(
        select(PaymentIntent)
        .where(PaymentIntent.tenant_id == tenant_id, PaymentIntent.id == pid)
        .with_for_update()
    ).scalar_one_or_none()
    if not pi:
        raise http_problem(404, "Not Found", "payment intent not found", instance=instance)
    return pi


def find_by_gateway_ref(
    session: Session, tenant_id: str, gateway_ref: str, *, for_update: bool = False
) -> PaymentIntent | None:
    """Busca pelo identificador do provedor usando o indice cego.

    ``gateway_ref`` e cifrado com nonce aleatorio, portanto a comparacao direta
    nunca casa; ver ``src/shared/blind_index.py``. O escopo de tenant e
    obrigatorio: dois tenants podem receber a mesma referencia do provedor.
    """
    from src.shared.blind_index import blind_index_for

    q = select(PaymentIntent).where(
        PaymentIntent.tenant_id == tenant_id,
        PaymentIntent.gateway_ref_bidx == blind_index_for(gateway_ref),
    )
    if for_update:
        q = q.with_for_update()
    rows = session.execute(q).scalars().all()
    if not rows:
        return None
    if len(rows) > 1:
        # Nao e esperado, mas e melhor falhar do que escolher arbitrariamente
        # qual pagamento liquidar.
        log.error(
            "multiple payment intents share one gateway reference",
            extra={"tenant_id": tenant_id, "count": len(rows)},
        )
        raise http_problem(
            409,
            "Conflict",
            "multiple payment intents share this gateway reference",
            instance="settlement",
        )
    return rows[0]


def find_by_customer_ref(
    session: Session, tenant_id: str, customer_ref: str
) -> PaymentIntent | None:
    """Busca exata por ``customer_ref`` (cifrado) via indice cego."""
    from src.shared.blind_index import blind_index_for

    return session.execute(
        select(PaymentIntent)
        .where(
            PaymentIntent.tenant_id == tenant_id,
            PaymentIntent.customer_ref_bidx == blind_index_for(customer_ref),
        )
        .order_by(PaymentIntent.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Verificacao do fato antes de aplicar
# ---------------------------------------------------------------------------


def verify_matches(
    pi: PaymentIntent,
    *,
    amount: Decimal | None,
    currency: str | None,
    source: str,
    instance: str,
) -> None:
    """Confere que o fato recebido corresponde ao pagamento local.

    Um webhook assinado prova que o provedor enviou o evento, nao que o evento
    se refere a este valor e esta moeda. Liquidar sem conferir aceitaria um
    evento legitimo associado ao objeto errado.
    """
    if amount is not None:
        local = Decimal(str(pi.amount))
        if local != amount:
            log.error(
                "settlement amount does not match the local record",
                extra={
                    "payment_intent_id": str(pi.id),
                    "source": source,
                    "local_amount": str(local),
                    "event_amount": str(amount),
                },
            )
            raise http_problem(
                409,
                "Conflict",
                f"amount mismatch: local {local}, event {amount}",
                instance=instance,
            )
    if currency is not None and pi.currency.upper() != currency.upper():
        log.error(
            "settlement currency does not match the local record",
            extra={
                "payment_intent_id": str(pi.id),
                "source": source,
                "local_currency": pi.currency,
                "event_currency": currency,
            },
        )
        raise http_problem(
            409,
            "Conflict",
            f"currency mismatch: local {pi.currency}, event {currency}",
            instance=instance,
        )


def _apply_status(pi: PaymentIntent, target: str, *, source: str, instance: str) -> None:
    if pi.status == target:
        return
    if not can_transition(pi.status, target):
        log.info(
            "refusing state transition",
            extra={
                "payment_intent_id": str(pi.id),
                "from": pi.status,
                "to": target,
                "source": source,
            },
        )
        raise http_problem(
            409,
            "Conflict",
            f"cannot transition from {pi.status} to {target}",
            instance=instance,
        )
    pi.status = target
    pi.version = (pi.version or 0) + 1
    pi.updated_at = _utcnow()


def _emit(
    session: Session,
    pi: PaymentIntent,
    event_type: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    order_id = ""
    if pi.customer_ref and pi.customer_ref.startswith("order:"):
        order_id = pi.customer_ref.removeprefix("order:").strip()
    payload: dict[str, Any] = {
        "payment_intent_id": str(pi.id),
        "tenant_id": pi.tenant_id,
        "order_id": order_id,
        "status": pi.status,
        "amount": str(pi.amount),
        "currency": pi.currency,
        "gateway_ref": pi.gateway_ref or "",
        "correlation_id": get_correlation_id(),
    }
    if extra:
        payload.update(extra)
    session.add(
        OutboxEvent(
            tenant_id=pi.tenant_id,
            event_type=event_type,
            aggregate_type="PaymentIntent",
            aggregate_id=str(pi.id),
            payload=payload,
        )
    )
    enqueue_webhook_deliveries(session, pi.tenant_id, event_type, payload)
    return payload


def settle_fact_key(pid: Any) -> str:
    return f"settle:{pid}"


def find_settlement_entry(session: Session, tenant_id: str, pid: Any) -> LedgerEntry | None:
    return session.execute(
        select(LedgerEntry).where(
            LedgerEntry.tenant_id == tenant_id,
            LedgerEntry.fact_key == settle_fact_key(pid),
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Operacoes de dominio
# ---------------------------------------------------------------------------


def settle_payment(
    session: Session,
    tenant_id: str,
    pi: PaymentIntent,
    *,
    source: str,
    amount: Decimal | None = None,
    currency: str | None = None,
    instance: str = "settlement",
) -> SettlementOutcome:
    """Liquida o pagamento: estado, journal e outbox em uma transacao.

    Chamada pelo endpoint de captura, pelo webhook do provedor e pela
    reconciliacao. Reentrante: se o fato ja estava lancado, nao lanca de novo e
    devolve ``already_applied``.

    Pressupoe que o chamador ja carregou ``pi`` com ``FOR UPDATE`` e que nenhuma
    chamada de rede esta pendente.
    """
    verify_matches(pi, amount=amount, currency=currency, source=source, instance=instance)

    if pi.status in SETTLED_STATES:
        existing = find_settlement_entry(session, tenant_id, pi.id)
        if existing is not None:
            log.info(
                "payment already settled; nothing to do",
                extra={"payment_intent_id": str(pi.id), "source": source},
            )
            return SettlementOutcome(
                status=pi.status, changed=False, entry_id=str(existing.id), already_applied=True
            )
        # Estado diz liquidado mas nao existe lancamento: e o rastro do defeito
        # antigo (webhook mudava status sem journal). Lanca o fato que falta em
        # vez de deixar o pagamento incoerente.
        log.warning(
            "payment is in a settled state without a journal entry; posting the missing fact",
            extra={"payment_intent_id": str(pi.id), "status": pi.status, "source": source},
        )
    elif not can_transition(pi.status, SETTLED):
        raise http_problem(
            409,
            "Conflict",
            f"cannot settle payment with status {pi.status}",
            instance=instance,
        )

    money = Decimal(str(pi.amount))
    entry = post_entry(
        session,
        tenant_id,
        fact_key=settle_fact_key(pi.id),
        lines=[
            LineSpec(DEBIT, resolve_account(session, tenant_id, "CASH"), money, pi.currency),
            LineSpec(CREDIT, resolve_account(session, tenant_id, "REVENUE"), money, pi.currency),
        ],
        payment_intent_id=pi.id,
    )

    if entry is None:
        # Outra transacao lancou o mesmo fato primeiro. O estado dela tambem foi
        # atualizado, entao aqui nao ha nada a fazer.
        existing = find_settlement_entry(session, tenant_id, pi.id)
        return SettlementOutcome(
            status=pi.status,
            changed=False,
            entry_id=str(existing.id) if existing else None,
            already_applied=True,
        )

    if pi.status not in SETTLED_STATES:
        _apply_status(pi, SETTLED, source=source, instance=instance)

    _emit(session, pi, "payment.settled", {"source": source})
    _audit(
        session,
        tenant_id,
        get_subject() or f"system:{source}",
        "payment_intent.settled",
        f"payment_intent:{pi.id}",
        {"amount": str(pi.amount), "currency": pi.currency, "source": source},
    )
    broadcaster.broadcast_sync(
        tenant_id,
        "payment.status",
        {
            "paymentIntentId": str(pi.id),
            "status": pi.status,
            "action": "settled",
            "amount": str(pi.amount),
            "currency": pi.currency,
        },
    )
    return SettlementOutcome(status=pi.status, changed=True, entry_id=str(entry.id))


def void_payment(
    session: Session,
    tenant_id: str,
    pi: PaymentIntent,
    *,
    source: str,
    instance: str = "settlement",
) -> SettlementOutcome:
    """Cancela uma autorizacao e, se houver lancamento, o compensa."""
    if pi.status == VOIDED:
        return SettlementOutcome(status=VOIDED, changed=False, already_applied=True)
    if not can_transition(pi.status, VOIDED):
        raise http_problem(
            409,
            "Conflict",
            f"cannot void payment with status {pi.status}",
            instance=instance,
        )

    entry_id: str | None = None
    settlement = find_settlement_entry(session, tenant_id, pi.id)
    if settlement is not None:
        reversal = reverse_entry(session, tenant_id, settlement, fact_key=f"void:{pi.id}")
        if reversal is not None:
            entry_id = str(reversal.id)

    _apply_status(pi, VOIDED, source=source, instance=instance)
    _emit(session, pi, "payment.voided", {"source": source})
    _audit(
        session,
        tenant_id,
        get_subject() or f"system:{source}",
        "payment_intent.voided",
        f"payment_intent:{pi.id}",
        {"amount": str(pi.amount), "currency": pi.currency, "source": source},
    )
    broadcaster.broadcast_sync(
        tenant_id,
        "payment.status",
        {
            "paymentIntentId": str(pi.id),
            "status": VOIDED,
            "action": "voided",
            "amount": str(pi.amount),
            "currency": pi.currency,
        },
    )
    return SettlementOutcome(status=VOIDED, changed=True, entry_id=entry_id)


def refund_payment(
    session: Session,
    tenant_id: str,
    pi: PaymentIntent,
    *,
    source: str,
    amount: Decimal | None = None,
    refund_ref: str | None = None,
    instance: str = "settlement",
) -> SettlementOutcome:
    """Estorna total ou parcialmente, por lancamento compensatorio vinculado.

    ``refund_ref`` identifica o estorno (id do Refund local ou referencia do
    provedor) e entra no ``fact_key``, de modo que dois estornos distintos do
    mesmo pagamento sao fatos distintos, e a reentrega do mesmo estorno nao e.
    """
    if pi.status not in SETTLED_STATES:
        raise http_problem(
            409,
            "Conflict",
            f"cannot refund payment with status {pi.status}",
            instance=instance,
        )

    settlement = find_settlement_entry(session, tenant_id, pi.id)
    if settlement is None:
        raise http_problem(
            422,
            "Unprocessable Entity",
            f"payment {pi.id} has no settlement entry to refund",
            instance=instance,
        )

    settled_total = entry_total(settlement)
    already = reversed_total(session, tenant_id, settlement)
    refund_amount = amount if amount is not None else settled_total - already
    if refund_amount <= 0:
        raise http_problem(
            422, "Unprocessable Entity", "refund amount must be > 0", instance=instance
        )
    if already + refund_amount > settled_total:
        raise http_problem(
            422,
            "Unprocessable Entity",
            f"refund {refund_amount} exceeds refundable {settled_total - already}",
            instance=instance,
        )

    fact = f"refund:{pi.id}:{refund_ref}" if refund_ref else f"refund:{pi.id}"
    reversal = reverse_entry(session, tenant_id, settlement, fact_key=fact, amount=refund_amount)
    if reversal is None:
        return SettlementOutcome(status=pi.status, changed=False, already_applied=True)

    total_refunded = already + refund_amount
    target = REFUNDED if total_refunded >= settled_total else PARTIALLY_REFUNDED
    _apply_status(pi, target, source=source, instance=instance)

    _emit(
        session,
        pi,
        "payment.refunded" if target == REFUNDED else "payment.partially_refunded",
        {
            "source": source,
            "refund_amount": str(refund_amount),
            "refunded_total": str(total_refunded),
            "refund_ref": refund_ref or "",
        },
    )
    _audit(
        session,
        tenant_id,
        get_subject() or f"system:{source}",
        "payment_intent.refunded",
        f"payment_intent:{pi.id}",
        {
            "refund_amount": str(refund_amount),
            "refunded_total": str(total_refunded),
            "currency": pi.currency,
            "source": source,
        },
    )
    broadcaster.broadcast_sync(
        tenant_id,
        "payment.status",
        {
            "paymentIntentId": str(pi.id),
            "status": target,
            "action": "refunded",
            "amount": str(refund_amount),
            "currency": pi.currency,
        },
    )
    return SettlementOutcome(status=target, changed=True, entry_id=str(reversal.id))


def fail_payment(
    session: Session,
    tenant_id: str,
    pi: PaymentIntent,
    *,
    source: str,
    error_code: str = "",
    error_message: str = "",
    instance: str = "settlement",
) -> SettlementOutcome:
    """Marca recusa definitiva. Nao e o destino de um timeout — ver ``REVIEW``."""
    if pi.status == FAILED:
        return SettlementOutcome(status=FAILED, changed=False, already_applied=True)
    if not can_transition(pi.status, FAILED):
        raise http_problem(
            409,
            "Conflict",
            f"cannot fail payment with status {pi.status}",
            instance=instance,
        )
    _apply_status(pi, FAILED, source=source, instance=instance)
    _emit(
        session,
        pi,
        "payment.failed",
        {"source": source, "error_code": error_code, "error_message": error_message},
    )
    return SettlementOutcome(status=FAILED, changed=True)


def mark_for_review(
    session: Session,
    tenant_id: str,
    pi: PaymentIntent,
    *,
    source: str,
    reason: str,
    instance: str = "settlement",
) -> SettlementOutcome:
    """Registra resultado incerto: pode ter sido aceito no provedor.

    Preserva o historico incerto em vez de tratar como recusa. Nenhuma operacao
    nova e emitida; a saida e consulta/reconciliacao com a mesma chave.
    """
    if pi.status in SETTLED_STATES or pi.status in TERMINAL_STATES:
        # Ja ha um desfecho conhecido; incerteza posterior nao o apaga.
        return SettlementOutcome(status=pi.status, changed=False, already_applied=True)
    if pi.status == REVIEW:
        return SettlementOutcome(status=REVIEW, changed=False, already_applied=True)
    _apply_status(pi, REVIEW, source=source, instance=instance)
    log.warning(
        "payment moved to REVIEW: remote outcome is unknown",
        extra={"payment_intent_id": str(pi.id), "source": source, "reason": reason},
    )
    _emit(session, pi, "payment.review", {"source": source, "reason": reason})
    _audit(
        session,
        tenant_id,
        get_subject() or f"system:{source}",
        "payment_intent.review",
        f"payment_intent:{pi.id}",
        {"reason": reason, "source": source},
    )
    return SettlementOutcome(status=REVIEW, changed=True)
