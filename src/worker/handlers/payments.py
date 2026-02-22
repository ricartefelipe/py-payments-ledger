from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application.payments import post_ledger_for_authorized_payment
from src.infrastructure.db.models import OutboxEvent, PaymentIntent
from src.shared.correlation import get_correlation_id
from src.shared.logging import get_logger

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def handle_event(session: Session, routing_key: str, payload: dict[str, Any]) -> None:
    if routing_key == "payment.authorized":
        pid = uuid.UUID(payload["payment_intent_id"])
        tenant_id = payload["tenant_id"]
        post_ledger_for_authorized_payment(session, tenant_id, pid)
        log.info("ledger posted", extra={"payment_intent_id": str(pid), "tenant_id": tenant_id})

    elif routing_key == "order.confirmed":
        handle_order_confirmed(session, payload)


def handle_order_confirmed(session: Session, payload: dict[str, Any]) -> None:
    order_id = str(payload["order_id"])
    tenant_id = str(payload["tenant_id"])
    amount = Decimal(str(payload["total_amount"]))
    currency = str(payload.get("currency", "BRL"))
    customer_ref = str(payload.get("customer_ref", f"order:{order_id}"))
    correlation_id = str(payload.get("correlation_id", get_correlation_id()))

    with session.begin():
        existing = session.execute(
            select(PaymentIntent).where(
                PaymentIntent.tenant_id == tenant_id,
                PaymentIntent.customer_ref == f"order:{order_id}",
            )
        ).scalar_one_or_none()
        if existing:
            log.info(
                "order already processed",
                extra={"order_id": order_id, "payment_intent_id": str(existing.id)},
            )
            return

        now = _utcnow()
        pi = PaymentIntent(
            tenant_id=tenant_id,
            amount=amount,
            currency=currency,
            status="AUTHORIZED",
            customer_ref=f"order:{order_id}",
            created_at=now,
            updated_at=now,
        )
        session.add(pi)
        session.flush()

        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="payment.authorized",
                aggregate_type="PaymentIntent",
                aggregate_id=str(pi.id),
                payload={
                    "payment_intent_id": str(pi.id),
                    "amount": str(amount),
                    "currency": currency,
                    "order_id": order_id,
                    "customer_ref": pi.customer_ref,
                    "correlation_id": correlation_id,
                },
            )
        )

    log.info(
        "payment intent created from order",
        extra={
            "order_id": order_id,
            "payment_intent_id": str(pi.id),
            "tenant_id": tenant_id,
        },
    )
