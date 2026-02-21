from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.infrastructure.db.models import LedgerEntry, LedgerLine, OutboxEvent, PaymentIntent
from src.shared.metrics import PAYMENT_INTENTS_CONFIRMED_TOTAL, PAYMENT_INTENTS_CREATED_TOTAL
from src.shared.problem import http_problem
from src.shared.correlation import get_correlation_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaymentIntentDTO(BaseModel):
    id: str
    amount: str
    currency: str
    status: str
    customer_ref: str
    created_at: str
    updated_at: str


def create_payment_intent(
    session: Session, tenant_id: str, amount: float, currency: str, customer_ref: str
) -> PaymentIntentDTO:
    if amount <= 0:
        raise http_problem(400, "Bad Request", "amount must be > 0", instance="/v1/payment-intents")
    if currency not in ("BRL", "USD", "EUR"):
        raise http_problem(
            400, "Bad Request", "unsupported currency", instance="/v1/payment-intents"
        )

    with session.begin():
        pi = PaymentIntent(
            tenant_id=tenant_id,
            amount=amount,
            currency=currency,
            status="CREATED",
            customer_ref=customer_ref,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(pi)
        session.flush()

        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="payment.intent.created",
                aggregate_type="PaymentIntent",
                aggregate_id=str(pi.id),
                payload={
                    "payment_intent_id": str(pi.id),
                    "amount": str(amount),
                    "currency": currency,
                    "customer_ref": customer_ref,
                    "correlation_id": get_correlation_id(),
                },
            )
        )

    PAYMENT_INTENTS_CREATED_TOTAL.labels(tenant_id).inc()

    return PaymentIntentDTO(
        id=str(pi.id),
        amount=str(pi.amount),
        currency=pi.currency,
        status=pi.status,
        customer_ref=pi.customer_ref,
        created_at=pi.created_at.isoformat(),
        updated_at=pi.updated_at.isoformat(),
    )


def get_payment_intent(session: Session, tenant_id: str, pid: uuid.UUID) -> PaymentIntentDTO:
    pi = session.execute(
        select(PaymentIntent).where(PaymentIntent.tenant_id == tenant_id, PaymentIntent.id == pid)
    ).scalar_one_or_none()
    if not pi:
        raise http_problem(
            404, "Not Found", "payment intent not found", instance=f"/v1/payment-intents/{pid}"
        )
    return PaymentIntentDTO(
        id=str(pi.id),
        amount=str(pi.amount),
        currency=pi.currency,
        status=pi.status,
        customer_ref=pi.customer_ref,
        created_at=pi.created_at.isoformat(),
        updated_at=pi.updated_at.isoformat(),
    )


def confirm_payment_intent(session: Session, tenant_id: str, pid: uuid.UUID) -> PaymentIntentDTO:
    with session.begin():
        pi = session.execute(
            select(PaymentIntent)
            .where(PaymentIntent.tenant_id == tenant_id, PaymentIntent.id == pid)
            .with_for_update()
        ).scalar_one_or_none()
        if not pi:
            raise http_problem(
                404,
                "Not Found",
                "payment intent not found",
                instance=f"/v1/payment-intents/{pid}/confirm",
            )
        if pi.status in ("SETTLED", "FAILED"):
            return PaymentIntentDTO(
                id=str(pi.id),
                amount=str(pi.amount),
                currency=pi.currency,
                status=pi.status,
                customer_ref=pi.customer_ref,
                created_at=pi.created_at.isoformat(),
                updated_at=pi.updated_at.isoformat(),
            )
        if pi.status != "CREATED":
            raise http_problem(
                409,
                "Conflict",
                f"cannot confirm status {pi.status}",
                instance=f"/v1/payment-intents/{pid}/confirm",
            )

        pi.status = "AUTHORIZED"
        pi.updated_at = _utcnow()

        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="payment.authorized",
                aggregate_type="PaymentIntent",
                aggregate_id=str(pi.id),
                payload={
                    "payment_intent_id": str(pi.id),
                    "amount": str(pi.amount),
                    "currency": pi.currency,
                    "correlation_id": get_correlation_id(),
                },
            )
        )

    PAYMENT_INTENTS_CONFIRMED_TOTAL.labels(tenant_id).inc()

    return PaymentIntentDTO(
        id=str(pi.id),
        amount=str(pi.amount),
        currency=pi.currency,
        status=pi.status,
        customer_ref=pi.customer_ref,
        created_at=pi.created_at.isoformat(),
        updated_at=pi.updated_at.isoformat(),
    )


def post_ledger_for_authorized_payment(session: Session, tenant_id: str, pid: uuid.UUID) -> None:
    with session.begin():
        pi = session.execute(
            select(PaymentIntent)
            .where(PaymentIntent.tenant_id == tenant_id, PaymentIntent.id == pid)
            .with_for_update()
        ).scalar_one_or_none()
        if not pi:
            raise http_problem(404, "Not Found", "payment intent not found", instance="worker")
        if pi.status != "AUTHORIZED":
            return

        entry = LedgerEntry(tenant_id=tenant_id, payment_intent_id=pi.id, posted_at=_utcnow())
        entry.lines = [
            LedgerLine(tenant_id=tenant_id, side="DEBIT", account="CASH", amount=pi.amount),
            LedgerLine(tenant_id=tenant_id, side="CREDIT", account="REVENUE", amount=pi.amount),
        ]
        session.add(entry)

        pi.status = "SETTLED"
        pi.updated_at = _utcnow()

        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="payment.settled",
                aggregate_type="PaymentIntent",
                aggregate_id=str(pi.id),
                payload={
                    "payment_intent_id": str(pi.id),
                    "status": "SETTLED",
                    "correlation_id": get_correlation_id(),
                },
            )
        )
