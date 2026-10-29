"""Webhook do Stripe.

Ordem das verificacoes, e por que essa ordem:

1. **Assinatura sobre os bytes originais.** ``construct_event`` valida o HMAC e a
   janela de tempo do cabecalho ``Stripe-Signature``. E feito antes de qualquer
   parsing para que o corpo nunca seja interpretado sem prova de origem.
2. **Registro no inbox.** ``(provider, event_id)`` com unicidade no banco. A
   segunda entrega do mesmo evento e reconhecida sem reaplicar o efeito, e duas
   entregas simultaneas sao serializadas pelo indice.
3. **Resolucao do pagamento.** Pelo indice cego, com o provedor como filtro, e
   recusando referencia ambigua em vez de escolher um tenant.
4. **Conferencia de valor e moeda.** A assinatura prova que o Stripe enviou o
   evento; nao prova que ele se refere a este valor nesta moeda.
5. **Aplicacao pelo caminho unico de dominio** (``settlement``), que grava
   estado, journal e outbox na mesma transacao do registro do inbox.

Codigo de resposta: ``200`` quando o evento foi aplicado, ja havia sido aplicado
ou nao se aplica; ``500`` quando falhou de forma reentregavel. Devolver ``200``
para uma falha faria o Stripe considerar a entrega concluida e nunca reenviar.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import stripe
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from src.api.deps.db import get_db
from src.application import webhook_inbox
from src.application.disputes import open_dispute, resolve_dispute
from src.application.payments import (
    resolve_payment_by_gateway_ref,
    update_payment_from_stripe_event,
)
from src.shared.config import get_settings
from src.shared.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["stripe-webhooks"])

PROVIDER = "stripe"

#: Eventos que movem estado financeiro, e o estado local correspondente.
_STATUS_EVENTS: dict[str, str] = {
    "payment_intent.succeeded": "SETTLED",
    "payment_intent.payment_failed": "FAILED",
    "payment_intent.canceled": "VOIDED",
    "payment_intent.amount_capturable_updated": "AUTHORIZED",
    "charge.refunded": "REFUNDED",
}


def _minor_to_decimal(value: Any) -> Decimal | None:
    """Converte a unidade minima do Stripe (centavos) para ``Decimal``.

    O Stripe trabalha em unidade minima inteira. A divisao e feita em ``Decimal``
    para nao passar por ``float`` em nenhum ponto.
    """
    if value is None:
        return None
    try:
        return (Decimal(str(int(value))) / Decimal(100)).quantize(Decimal("0.01"))
    except (TypeError, ValueError, ArithmeticError):
        logger.warning("stripe webhook: unreadable amount", extra={"raw": str(value)[:64]})
        return None


def _event_reference(event_type: str, data_object: dict[str, Any]) -> str | None:
    """Referencia do PaymentIntent no Stripe para este tipo de evento."""
    if event_type == "charge.refunded":
        return data_object.get("payment_intent")
    if event_type.startswith("payment_intent."):
        return data_object.get("id")
    return data_object.get("payment_intent") or data_object.get("id")


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
    stripe_signature: str = Header(alias="Stripe-Signature"),
):
    settings = get_settings()
    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, settings.stripe_webhook_secret
        )
    except stripe.error.SignatureVerificationError:
        logger.warning("stripe webhook: invalid signature")
        return JSONResponse(status_code=400, content={"status": "invalid_signature"})
    except ValueError:
        logger.warning("stripe webhook: invalid payload")
        return JSONResponse(status_code=400, content={"status": "invalid_payload"})

    event_id = event.get("id") or ""
    event_type = event["type"]
    data_object = event["data"]["object"]
    if not event_id:
        # Sem identidade do evento nao ha como deduplicar; recusar e mais seguro
        # que aplicar um efeito financeiro que pode repetir.
        logger.error("stripe webhook: event without id", extra={"event_type": event_type})
        return JSONResponse(status_code=400, content={"status": "missing_event_id"})

    logger.info("stripe webhook received", extra={"event_type": event_type, "event_id": event_id})

    try:
        applied = _handle_event(db, event_id, event_type, data_object, dict(event))
    except Exception:
        # Falha reentregavel: desfaz tudo e pede reenvio ao Stripe. Responder 200
        # aqui faria o evento ser perdido.
        db.rollback()
        logger.exception(
            "stripe webhook: processing failed; asking for redelivery",
            extra={"event_type": event_type, "event_id": event_id},
        )
        try:
            _record_failure(db, event_id, event_type)
        except Exception:
            logger.warning("could not record webhook failure", exc_info=True)
        return JSONResponse(status_code=500, content={"status": "retry"})

    return {"status": applied}


def _handle_event(
    db: Session,
    event_id: str,
    event_type: str,
    data_object: dict[str, Any],
    raw_event: dict[str, Any],
) -> str:
    """Registra e aplica o evento na mesma transacao."""
    reception = webhook_inbox.register(
        db,
        provider=PROVIDER,
        event_id=event_id,
        event_type=event_type,
        payload=raw_event,
    )
    if not reception.is_new:
        db.commit()
        return "duplicate"

    inbox_id = reception.inbox_id

    if event_type == "charge.dispute.created":
        outcome = _handle_dispute_created(db, data_object)
    elif event_type == "charge.dispute.closed":
        outcome = _handle_dispute_closed(db, data_object)
    elif event_type in _STATUS_EVENTS:
        outcome = _handle_status_event(db, event_type, data_object)
    else:
        webhook_inbox.mark_ignored(db, inbox_id, f"unhandled event type {event_type}")
        db.commit()
        logger.debug("stripe webhook: unhandled type", extra={"event_type": event_type})
        return "ignored"

    if outcome.ignored_reason:
        webhook_inbox.mark_ignored(db, inbox_id, outcome.ignored_reason)
    else:
        webhook_inbox.mark_processed(db, inbox_id, tenant_id=outcome.tenant_id)
    db.commit()
    return outcome.result


def _record_failure(db: Session, event_id: str, event_type: str) -> None:
    """Marca a entrega como falha, em transacao propria apos o rollback."""
    reception = webhook_inbox.register(
        db,
        provider=PROVIDER,
        event_id=event_id,
        event_type=event_type,
        payload={"note": "processing failed"},
    )
    if reception.inbox_id is not None:
        webhook_inbox.mark_failed(db, reception.inbox_id, "processing failed; awaiting redelivery")
    db.commit()


class _Outcome:
    __slots__ = ("ignored_reason", "result", "tenant_id")

    def __init__(
        self, result: str, *, tenant_id: str | None = None, ignored_reason: str = ""
    ) -> None:
        self.result = result
        self.tenant_id = tenant_id
        self.ignored_reason = ignored_reason


def _handle_status_event(db: Session, event_type: str, data_object: dict[str, Any]) -> _Outcome:
    gateway_ref = _event_reference(event_type, data_object)
    if not gateway_ref:
        return _Outcome("ignored", ignored_reason="event carries no payment_intent reference")

    target = _STATUS_EVENTS[event_type]
    amount = _minor_to_decimal(
        data_object.get("amount_received")
        if event_type == "payment_intent.succeeded"
        else (
            data_object.get("amount_refunded")
            if event_type == "charge.refunded"
            else data_object.get("amount")
        )
    )
    currency = data_object.get("currency")
    currency = currency.upper() if isinstance(currency, str) else None

    # Para estorno parcial o valor do evento e o total estornado; a conferencia
    # contra o valor do pagamento nao se aplica.
    verify_amount = amount if event_type == "payment_intent.succeeded" else None

    changed = update_payment_from_stripe_event(
        db,
        gateway_ref,
        target,
        provider=PROVIDER,
        amount=verify_amount,
        currency=currency if event_type == "payment_intent.succeeded" else None,
        event_ref=data_object.get("id"),
    )
    pi = resolve_payment_by_gateway_ref(db, gateway_ref, provider=PROVIDER)
    if pi is None:
        return _Outcome(
            "ignored", ignored_reason=f"no local payment for gateway reference ({event_type})"
        )
    return _Outcome("applied" if changed else "already_applied", tenant_id=pi.tenant_id)


def _handle_dispute_created(db: Session, data_object: dict[str, Any]) -> _Outcome:
    dispute_ref = data_object.get("id", "")
    reason_map = {
        "fraudulent": "FRAUDULENT",
        "duplicate": "DUPLICATE",
        "product_not_received": "PRODUCT_NOT_RECEIVED",
    }
    reason = reason_map.get(data_object.get("reason", "other"), "OTHER")

    pi_ref = data_object.get("payment_intent")
    if not pi_ref:
        return _Outcome("ignored", ignored_reason="dispute without payment_intent")

    pi = resolve_payment_by_gateway_ref(db, pi_ref, provider=PROVIDER)
    if not pi:
        return _Outcome("ignored", ignored_reason="no local payment for disputed reference")

    amount = _minor_to_decimal(data_object.get("amount")) or Decimal(str(pi.amount))
    # Sem try/except: uma falha aqui deve desfazer a transacao e pedir reenvio,
    # em vez de registrar o evento como processado sem ter criado a contestacao.
    open_dispute(
        db,
        str(pi.tenant_id),
        pi.id,
        reason,
        amount=amount,
        gateway_dispute_ref=dispute_ref,
    )
    logger.info(
        "dispute opened from stripe webhook",
        extra={"dispute_ref": dispute_ref, "payment_intent_id": str(pi.id)},
    )
    return _Outcome("applied", tenant_id=pi.tenant_id)


def _handle_dispute_closed(db: Session, data_object: dict[str, Any]) -> _Outcome:
    from sqlalchemy import select

    from src.infrastructure.db.models import Dispute

    dispute_ref = data_object.get("id", "")
    status = data_object.get("status", "")

    d = db.execute(
        select(Dispute).where(Dispute.gateway_dispute_ref == dispute_ref)
    ).scalar_one_or_none()
    if not d:
        return _Outcome("ignored", ignored_reason="no local dispute for this reference")
    if d.status not in ("OPEN", "UNDER_REVIEW"):
        return _Outcome(
            "already_applied",
            tenant_id=str(d.tenant_id),
            ignored_reason=f"dispute already {d.status}",
        )

    resolve_dispute(db, str(d.tenant_id), d.id, won=status == "won")
    logger.info(
        "dispute resolved from stripe webhook",
        extra={"dispute_ref": dispute_ref, "outcome": "WON" if status == "won" else "LOST"},
    )
    return _Outcome("applied", tenant_id=str(d.tenant_id))
