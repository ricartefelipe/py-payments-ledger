from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application.payments import post_ledger_for_authorized_payment
from src.infrastructure.db.models import OutboxEvent, PaymentIntent
from src.shared.correlation import get_correlation_id, set_correlation_id
from src.shared.logging import get_logger
from src.worker.handlers.charge_request import parse_charge_payload

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def handle_event(session: Session, routing_key: str, payload: dict[str, Any]) -> None:
    if routing_key == "payment.authorized":
        pid_raw = payload.get("payment_intent_id") or payload.get("paymentIntentId")
        pid = uuid.UUID(str(pid_raw))
        tenant_id = str(payload.get("tenant_id") or payload.get("tenantId") or "")
        post_ledger_for_authorized_payment(session, tenant_id, pid)
        log.info(
            "ledger posted",
            extra={
                "payment_intent_id": str(pid),
                "tenant_id": tenant_id,
                "correlation_id": get_correlation_id(),
            },
        )

    elif routing_key in ("payment.charge_requested", "order.confirmed"):
        handle_charge_request(session, payload)


def handle_charge_request(session: Session, payload: dict[str, Any]) -> None:
    parsed = parse_charge_payload(payload)
    order_id = parsed["order_id"]
    tenant_id = parsed["tenant_id"]

    if not order_id or not tenant_id:
        log.warning(
            "charge request missing order_id or tenant_id",
            extra={"payload_keys": list(payload.keys()), "parsed": parsed},
        )
        return

    set_correlation_id(parsed["correlation_id"] or get_correlation_id())
    amount = Decimal(parsed["total_amount"])
    currency = parsed["currency"]
    customer_ref = parsed["customer_ref"] or f"order:{order_id}"

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
                extra={
                    "order_id": order_id,
                    "payment_intent_id": str(existing.id),
                    "correlation_id": parsed["correlation_id"],
                },
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
                    "correlation_id": parsed["correlation_id"] or get_correlation_id(),
                },
            )
        )

    log.info(
        "payment intent created from charge request",
        extra={
            "order_id": order_id,
            "payment_intent_id": str(pi.id),
            "tenant_id": tenant_id,
            "correlation_id": parsed["correlation_id"],
        },
    )
