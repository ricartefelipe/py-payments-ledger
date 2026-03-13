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
    Payout,
)
from src.infrastructure.db.session import safe_begin
from src.shared.correlation import get_correlation_id, get_subject
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PayoutDTO(BaseModel):
    id: str
    tenant_id: str
    recipient_id: str
    amount: str
    currency: str
    status: str
    gateway_ref: str | None = None
    bank_account: str | None = None
    description: str | None = None
    processed_at: str | None = None
    failed_reason: str | None = None
    created_at: str


def _to_dto(p: Payout) -> PayoutDTO:
    return PayoutDTO(
        id=str(p.id),
        tenant_id=p.tenant_id,
        recipient_id=p.recipient_id,
        amount=str(p.amount),
        currency=p.currency,
        status=p.status,
        gateway_ref=p.gateway_ref,
        bank_account=p.bank_account,
        description=p.description,
        processed_at=p.processed_at.isoformat() if p.processed_at else None,
        failed_reason=p.failed_reason,
        created_at=p.created_at.isoformat(),
    )


def _resolve_account(session: Session, tenant_id: str, code: str) -> str:
    cfg = session.execute(
        select(AccountConfig).where(
            AccountConfig.tenant_id == tenant_id,
            AccountConfig.code == code,
        )
    ).scalar_one_or_none()
    return cfg.code if cfg else code


def create_payout(
    session: Session,
    tenant_id: str,
    recipient_id: str,
    amount: Decimal,
    currency: str,
    bank_account: str | None = None,
    description: str | None = None,
) -> PayoutDTO:
    if amount <= 0:
        raise http_problem(400, "Bad Request", "amount must be > 0", instance="/v1/payouts")
    if currency not in ("BRL", "USD", "EUR"):
        raise http_problem(400, "Bad Request", "unsupported currency", instance="/v1/payouts")

    with safe_begin(session):
        payout = Payout(
            tenant_id=tenant_id,
            recipient_id=recipient_id,
            amount=amount,
            currency=currency,
            status="PENDING",
            bank_account=bank_account,
            description=description,
            created_at=_utcnow(),
        )
        session.add(payout)
        session.flush()

        debit_account = _resolve_account(session, tenant_id, f"SPLIT_PAYABLE:{recipient_id}")
        credit_account = _resolve_account(session, tenant_id, "CASH_OUT")

        entry = LedgerEntry(
            tenant_id=tenant_id,
            payment_intent_id=None,
            posted_at=_utcnow(),
        )
        entry.lines = [
            LedgerLine(
                tenant_id=tenant_id,
                side="DEBIT",
                account=debit_account,
                amount=amount,
                currency=currency,
            ),
            LedgerLine(
                tenant_id=tenant_id,
                side="CREDIT",
                account=credit_account,
                amount=amount,
                currency=currency,
            ),
        ]
        session.add(entry)

        event_payload = {
            "payout_id": str(payout.id),
            "recipient_id": recipient_id,
            "amount": str(amount),
            "currency": currency,
            "correlation_id": get_correlation_id(),
        }
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="payout.created",
                aggregate_type="Payout",
                aggregate_id=str(payout.id),
                payload=event_payload,
            )
        )
        enqueue_webhook_deliveries(session, tenant_id, "payout.created", event_payload)

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "payout.created",
        f"payout:{payout.id}",
        {"recipient_id": recipient_id, "amount": str(amount), "currency": currency},
    )

    return _to_dto(payout)


def list_payouts(
    session: Session,
    tenant_id: str,
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[PayoutDTO], int]:
    q = select(Payout).where(Payout.tenant_id == tenant_id)
    if status:
        q = q.where(Payout.status == status)

    count_q = select(func.count()).select_from(q.subquery())
    total = session.execute(count_q).scalar() or 0

    q = q.order_by(Payout.created_at.desc())
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = session.execute(q).scalars().all()
    return [_to_dto(r) for r in rows], total


def get_payout(session: Session, tenant_id: str, payout_id: uuid.UUID) -> PayoutDTO:
    p = session.execute(
        select(Payout).where(Payout.tenant_id == tenant_id, Payout.id == payout_id)
    ).scalar_one_or_none()
    if not p:
        raise http_problem(
            404, "Not Found", "payout not found", instance=f"/v1/payouts/{payout_id}"
        )
    return _to_dto(p)


def process_payout(session: Session, tenant_id: str, payout_id: uuid.UUID) -> PayoutDTO:
    with safe_begin(session):
        p = session.execute(
            select(Payout)
            .where(Payout.tenant_id == tenant_id, Payout.id == payout_id)
            .with_for_update()
        ).scalar_one_or_none()

        if not p:
            raise http_problem(
                404, "Not Found", "payout not found",
                instance=f"/v1/payouts/{payout_id}/process",
            )
        if p.status != "PENDING":
            raise http_problem(
                409, "Conflict", f"cannot process payout with status {p.status}",
                instance=f"/v1/payouts/{payout_id}/process",
            )

        p.status = "PROCESSING"
        session.flush()

        p.gateway_ref = f"sim_transfer_{uuid.uuid4().hex[:12]}"
        p.status = "COMPLETED"
        p.processed_at = _utcnow()

        event_payload = {
            "payout_id": str(p.id),
            "recipient_id": p.recipient_id,
            "amount": str(p.amount),
            "currency": p.currency,
            "gateway_ref": p.gateway_ref,
            "correlation_id": get_correlation_id(),
        }
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="payout.completed",
                aggregate_type="Payout",
                aggregate_id=str(p.id),
                payload=event_payload,
            )
        )
        enqueue_webhook_deliveries(session, tenant_id, "payout.completed", event_payload)

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "payout.completed",
        f"payout:{p.id}",
        {"gateway_ref": p.gateway_ref},
    )

    return _to_dto(p)


def cancel_payout(session: Session, tenant_id: str, payout_id: uuid.UUID) -> PayoutDTO:
    with safe_begin(session):
        p = session.execute(
            select(Payout)
            .where(Payout.tenant_id == tenant_id, Payout.id == payout_id)
            .with_for_update()
        ).scalar_one_or_none()

        if not p:
            raise http_problem(
                404, "Not Found", "payout not found",
                instance=f"/v1/payouts/{payout_id}/cancel",
            )
        if p.status not in ("PENDING",):
            raise http_problem(
                409, "Conflict", f"cannot cancel payout with status {p.status}",
                instance=f"/v1/payouts/{payout_id}/cancel",
            )

        p.status = "FAILED"
        p.failed_reason = "cancelled by user"

        event_payload = {
            "payout_id": str(p.id),
            "recipient_id": p.recipient_id,
            "amount": str(p.amount),
            "currency": p.currency,
            "reason": "cancelled",
            "correlation_id": get_correlation_id(),
        }
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="payout.failed",
                aggregate_type="Payout",
                aggregate_id=str(p.id),
                payload=event_payload,
            )
        )
        enqueue_webhook_deliveries(session, tenant_id, "payout.failed", event_payload)

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "payout.cancelled",
        f"payout:{p.id}",
        {},
    )

    return _to_dto(p)
