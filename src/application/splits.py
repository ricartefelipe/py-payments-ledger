from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.application.security import _audit
from src.application.webhooks import enqueue_webhook_deliveries
from src.infrastructure.db.models import (
    AccountConfig,
    LedgerEntry,
    LedgerLine,
    OutboxEvent,
    PaymentIntent,
    PaymentSplit,
)
from src.infrastructure.db.session import safe_begin
from src.shared.correlation import get_correlation_id, get_subject
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SplitInput(BaseModel):
    recipient_id: str
    amount: float
    currency: str = "BRL"


class PaymentSplitDTO(BaseModel):
    id: str
    payment_intent_id: str
    recipient_id: str
    amount: str
    currency: str
    status: str
    transferred_at: str | None = None
    created_at: str


def _to_split_dto(s: PaymentSplit) -> PaymentSplitDTO:
    return PaymentSplitDTO(
        id=str(s.id),
        payment_intent_id=str(s.payment_intent_id),
        recipient_id=s.recipient_id,
        amount=str(s.amount),
        currency=s.currency,
        status=s.status,
        transferred_at=s.transferred_at.isoformat() if s.transferred_at else None,
        created_at=s.created_at.isoformat(),
    )


def _resolve_account(session: Session, tenant_id: str, code: str) -> str:
    cfg = session.execute(
        select(AccountConfig).where(
            AccountConfig.tenant_id == tenant_id,
            AccountConfig.code == code,
        )
    ).scalar_one_or_none()
    return cfg.code if cfg else code


def create_split(
    session: Session,
    tenant_id: str,
    payment_intent_id: uuid.UUID,
    splits: list[SplitInput],
) -> list[PaymentSplitDTO]:
    if not splits:
        raise http_problem(
            400,
            "Bad Request",
            "at least one split is required",
            instance=f"/v1/payment-intents/{payment_intent_id}/splits",
        )

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
                instance=f"/v1/payment-intents/{payment_intent_id}/splits",
            )

        if pi.status not in ("SETTLED", "AUTHORIZED"):
            raise http_problem(
                409,
                "Conflict",
                f"cannot create splits for payment with status {pi.status}",
                instance=f"/v1/payment-intents/{payment_intent_id}/splits",
            )

        existing_total = Decimal(
            str(
                session.execute(
                    select(func.coalesce(func.sum(PaymentSplit.amount), Decimal(0))).where(
                        PaymentSplit.payment_intent_id == payment_intent_id,
                        PaymentSplit.tenant_id == tenant_id,
                        PaymentSplit.status.in_(("PENDING", "TRANSFERRED")),
                    )
                ).scalar()
                or 0
            )
        )

        new_total = sum((Decimal(str(s.amount)) for s in splits), Decimal(0))

        if existing_total + new_total > Decimal(str(pi.amount)):
            raise http_problem(
                422,
                "Unprocessable Entity",
                f"total splits ({existing_total + new_total}) would exceed payment amount ({pi.amount})",
                instance=f"/v1/payment-intents/{payment_intent_id}/splits",
            )

        created: list[PaymentSplit] = []
        for s in splits:
            if Decimal(str(s.amount)) <= 0:
                raise http_problem(
                    400,
                    "Bad Request",
                    "split amount must be > 0",
                    instance=f"/v1/payment-intents/{payment_intent_id}/splits",
                )
            split = PaymentSplit(
                tenant_id=tenant_id,
                payment_intent_id=payment_intent_id,
                recipient_id=s.recipient_id,
                amount=Decimal(str(s.amount)),
                currency=s.currency,
                status="PENDING",
                created_at=_utcnow(),
            )
            session.add(split)
            created.append(split)

        session.flush()

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "split.created",
        f"payment_intent:{payment_intent_id}",
        {
            "split_count": len(created),
            "total_amount": str(new_total),
        },
    )

    return [_to_split_dto(s) for s in created]


def list_splits(
    session: Session, tenant_id: str, payment_intent_id: uuid.UUID
) -> list[PaymentSplitDTO]:
    rows = (
        session.execute(
            select(PaymentSplit)
            .where(
                PaymentSplit.tenant_id == tenant_id,
                PaymentSplit.payment_intent_id == payment_intent_id,
            )
            .order_by(PaymentSplit.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [_to_split_dto(r) for r in rows]


def process_splits(
    session: Session, tenant_id: str, payment_intent_id: uuid.UUID
) -> list[PaymentSplitDTO]:
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
                instance=f"/v1/payment-intents/{payment_intent_id}/splits/process",
            )

        if pi.status != "SETTLED":
            raise http_problem(
                409,
                "Conflict",
                f"cannot process splits for payment with status {pi.status}",
                instance=f"/v1/payment-intents/{payment_intent_id}/splits/process",
            )

        pending_splits = (
            session.execute(
                select(PaymentSplit)
                .where(
                    PaymentSplit.tenant_id == tenant_id,
                    PaymentSplit.payment_intent_id == payment_intent_id,
                    PaymentSplit.status == "PENDING",
                )
                .with_for_update()
            )
            .scalars()
            .all()
        )

        if not pending_splits:
            raise http_problem(
                409,
                "Conflict",
                "no pending splits to process",
                instance=f"/v1/payment-intents/{payment_intent_id}/splits/process",
            )

        revenue_account = _resolve_account(session, tenant_id, "REVENUE")
        now = _utcnow()
        processed: list[PaymentSplit] = []

        for split in pending_splits:
            payable_account = f"SPLIT_PAYABLE:{split.recipient_id}"

            entry = LedgerEntry(
                tenant_id=tenant_id,
                payment_intent_id=payment_intent_id,
                posted_at=now,
            )
            entry.lines = [
                LedgerLine(
                    tenant_id=tenant_id,
                    side="DEBIT",
                    account=revenue_account,
                    amount=split.amount,
                    currency=split.currency,
                ),
                LedgerLine(
                    tenant_id=tenant_id,
                    side="CREDIT",
                    account=payable_account,
                    amount=split.amount,
                    currency=split.currency,
                ),
            ]
            session.add(entry)

            split.status = "TRANSFERRED"
            split.transferred_at = now
            processed.append(split)

        session.flush()

        event_payload = {
            "payment_intent_id": str(payment_intent_id),
            "tenant_id": tenant_id,
            "splits_processed": len(processed),
            "total_amount": str(sum(Decimal(str(s.amount)) for s in processed)),
            "correlation_id": get_correlation_id(),
        }
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="payment.splits.processed",
                aggregate_type="PaymentIntent",
                aggregate_id=str(payment_intent_id),
                payload=event_payload,
            )
        )
        enqueue_webhook_deliveries(session, tenant_id, "payment.splits.processed", event_payload)

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "split.processed",
        f"payment_intent:{payment_intent_id}",
        {
            "splits_processed": len(processed),
            "total_amount": str(sum(Decimal(str(s.amount)) for s in processed)),
        },
    )

    return [_to_split_dto(s) for s in processed]
