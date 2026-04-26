from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.application.ports.payment_gateway import PaymentGatewayPort
from src.application.security import _audit
from src.application.webhooks import enqueue_webhook_deliveries
from src.infrastructure.db.models import (
    AccountConfig,
    LedgerEntry,
    LedgerLine,
    OutboxEvent,
    PaymentIntent,
    Refund,
)
from src.infrastructure.db.session import safe_begin
from src.shared.correlation import get_correlation_id, get_subject
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RefundDTO(BaseModel):
    id: str
    payment_intent_id: str
    amount: str
    reason: str | None
    status: str
    gateway_ref: str | None
    created_at: str


def _resolve_account(session: Session, tenant_id: str, code: str, fallback: str) -> str:
    cfg = session.execute(
        select(AccountConfig).where(
            AccountConfig.tenant_id == tenant_id,
            AccountConfig.code == code,
        )
    ).scalar_one_or_none()
    return cfg.code if cfg else fallback


def create_refund(
    session: Session,
    tenant_id: str,
    payment_intent_id: uuid.UUID,
    amount: Decimal,
    reason: str | None = None,
    gateway: PaymentGatewayPort | None = None,
    idempotency_key: str | None = None,
) -> RefundDTO:
    with safe_begin(session):
        pi = session.execute(
            select(PaymentIntent)
            .where(
                PaymentIntent.tenant_id == tenant_id,
                PaymentIntent.id == payment_intent_id,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if not pi:
            raise http_problem(
                404,
                "Not Found",
                "payment intent not found",
                instance=f"/v1/payment-intents/{payment_intent_id}/refund",
            )

        if pi.status not in ("SETTLED", "PARTIALLY_REFUNDED"):
            raise http_problem(
                409,
                "Conflict",
                f"cannot refund payment with status {pi.status}",
                instance=f"/v1/payment-intents/{payment_intent_id}/refund",
            )

        if amount <= 0:
            raise http_problem(
                400,
                "Bad Request",
                "refund amount must be > 0",
                instance=f"/v1/payment-intents/{payment_intent_id}/refund",
            )

        _total_raw = session.execute(
            select(func.coalesce(func.sum(Refund.amount), Decimal(0))).where(
                Refund.payment_intent_id == payment_intent_id,
                Refund.tenant_id == tenant_id,
                Refund.status.in_(("COMPLETED", "PENDING", "PROCESSING")),
            )
        ).scalar()
        total_refunded = Decimal(str(_total_raw)) if _total_raw is not None else Decimal(0)

        if total_refunded + amount > pi.amount:
            raise http_problem(
                422,
                "Unprocessable Entity",
                f"total refunds ({total_refunded + amount}) would exceed payment amount ({pi.amount})",
                instance=f"/v1/payment-intents/{payment_intent_id}/refund",
            )

        refund = Refund(
            tenant_id=tenant_id,
            payment_intent_id=payment_intent_id,
            amount=amount,
            reason=reason,
            status="PENDING",
            created_at=_utcnow(),
        )
        session.add(refund)
        session.flush()

        if gateway and pi.gateway_ref:
            idem = idempotency_key or str(refund.id)
            gw_result = asyncio.run(
                gateway.refund(
                    pi.gateway_ref,
                    amount,
                    pi.currency,
                    idem,
                )
            )
            if gw_result.success:
                refund.gateway_ref = gw_result.gateway_ref
            else:
                refund.status = "FAILED"
                log.error(
                    "gateway refund failed",
                    extra={"gateway_ref": pi.gateway_ref, "amount": str(amount)},
                )

        if refund.status != "FAILED":
            debit_account = _resolve_account(session, tenant_id, "REFUND_EXPENSE", "REFUND_EXPENSE")
            credit_account = _resolve_account(session, tenant_id, "CASH", "CASH")

            entry = LedgerEntry(
                tenant_id=tenant_id,
                payment_intent_id=payment_intent_id,
                posted_at=_utcnow(),
            )
            entry.lines = [
                LedgerLine(
                    tenant_id=tenant_id,
                    side="DEBIT",
                    account=debit_account,
                    amount=amount,
                    currency=pi.currency,
                ),
                LedgerLine(
                    tenant_id=tenant_id,
                    side="CREDIT",
                    account=credit_account,
                    amount=amount,
                    currency=pi.currency,
                ),
            ]
            session.add(entry)

            if total_refunded + amount >= pi.amount:
                pi.status = "REFUNDED"
            else:
                pi.status = "PARTIALLY_REFUNDED"
            pi.updated_at = _utcnow()

            refund.status = "COMPLETED"

            event_payload = {
                "payment_intent_id": str(payment_intent_id),
                "refund_id": str(refund.id),
                "amount": str(amount),
                "currency": pi.currency,
                "reason": reason or "",
                "payment_status": pi.status,
                "correlation_id": get_correlation_id(),
            }
            session.add(
                OutboxEvent(
                    tenant_id=tenant_id,
                    event_type="payment.refunded",
                    aggregate_type="PaymentIntent",
                    aggregate_id=str(payment_intent_id),
                    payload=event_payload,
                )
            )
            enqueue_webhook_deliveries(session, tenant_id, "payment.refunded", event_payload)

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "refund.created",
        f"refund:{refund.id}",
        {
            "payment_intent_id": str(payment_intent_id),
            "amount": str(amount),
            "status": refund.status,
        },
    )

    return RefundDTO(
        id=str(refund.id),
        payment_intent_id=str(refund.payment_intent_id),
        amount=str(refund.amount),
        reason=refund.reason,
        status=refund.status,
        gateway_ref=refund.gateway_ref,
        created_at=refund.created_at.isoformat(),
    )


def list_refunds(session: Session, tenant_id: str, payment_intent_id: uuid.UUID) -> list[RefundDTO]:
    rows = (
        session.execute(
            select(Refund)
            .where(
                Refund.tenant_id == tenant_id,
                Refund.payment_intent_id == payment_intent_id,
            )
            .order_by(Refund.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        RefundDTO(
            id=str(r.id),
            payment_intent_id=str(r.payment_intent_id),
            amount=str(r.amount),
            reason=r.reason,
            status=r.status,
            gateway_ref=r.gateway_ref,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
