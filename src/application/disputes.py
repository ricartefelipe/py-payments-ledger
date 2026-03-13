from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.application.security import _audit
from src.application.webhooks import enqueue_webhook_deliveries
from src.infrastructure.db.models import (
    AccountConfig,
    Dispute,
    LedgerEntry,
    LedgerLine,
    OutboxEvent,
    PaymentIntent,
)
from src.infrastructure.db.session import safe_begin
from src.shared.correlation import get_correlation_id, get_subject
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)

VALID_REASONS = ("FRAUDULENT", "DUPLICATE", "PRODUCT_NOT_RECEIVED", "OTHER")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DisputeDTO(BaseModel):
    id: str
    tenant_id: str
    payment_intent_id: str
    reason: str
    status: str
    amount: str
    currency: str
    gateway_dispute_ref: str | None = None
    evidence: dict[str, Any] | None = None
    resolved_at: str | None = None
    created_at: str
    updated_at: str


def _to_dto(d: Dispute) -> DisputeDTO:
    return DisputeDTO(
        id=str(d.id),
        tenant_id=d.tenant_id,
        payment_intent_id=str(d.payment_intent_id),
        reason=d.reason,
        status=d.status,
        amount=str(d.amount),
        currency=d.currency,
        gateway_dispute_ref=d.gateway_dispute_ref,
        evidence=d.evidence,
        resolved_at=d.resolved_at.isoformat() if d.resolved_at else None,
        created_at=d.created_at.isoformat(),
        updated_at=d.updated_at.isoformat(),
    )


def _resolve_account(session: Session, tenant_id: str, code: str) -> str:
    cfg = session.execute(
        select(AccountConfig).where(
            AccountConfig.tenant_id == tenant_id,
            AccountConfig.code == code,
        )
    ).scalar_one_or_none()
    return cfg.code if cfg else code


def _record_loss_ledger(
    session: Session, tenant_id: str, dispute: Dispute, pi: PaymentIntent
) -> None:
    debit_account = _resolve_account(session, tenant_id, "DISPUTE_LOSS")
    credit_account = _resolve_account(session, tenant_id, "CASH")

    entry = LedgerEntry(
        tenant_id=tenant_id,
        payment_intent_id=dispute.payment_intent_id,
        posted_at=_utcnow(),
    )
    entry.lines = [
        LedgerLine(
            tenant_id=tenant_id,
            side="DEBIT",
            account=debit_account,
            amount=dispute.amount,
            currency=dispute.currency,
        ),
        LedgerLine(
            tenant_id=tenant_id,
            side="CREDIT",
            account=credit_account,
            amount=dispute.amount,
            currency=dispute.currency,
        ),
    ]
    session.add(entry)


def open_dispute(
    session: Session,
    tenant_id: str,
    payment_intent_id: uuid.UUID,
    reason: str,
    amount: Decimal | None = None,
    gateway_dispute_ref: str | None = None,
) -> DisputeDTO:
    if reason not in VALID_REASONS:
        raise http_problem(
            400,
            "Bad Request",
            f"invalid reason, must be one of {VALID_REASONS}",
            instance="/v1/disputes",
        )

    with safe_begin(session):
        pi = session.execute(
            select(PaymentIntent).where(
                PaymentIntent.tenant_id == tenant_id,
                PaymentIntent.id == payment_intent_id,
            )
        ).scalar_one_or_none()

        if not pi:
            raise http_problem(
                404, "Not Found", "payment intent not found", instance="/v1/disputes"
            )

        dispute_amount = amount if amount is not None else Decimal(str(pi.amount))

        dispute = Dispute(
            tenant_id=tenant_id,
            payment_intent_id=payment_intent_id,
            reason=reason,
            status="OPEN",
            amount=dispute_amount,
            currency=pi.currency,
            gateway_dispute_ref=gateway_dispute_ref,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(dispute)
        session.flush()

        event_payload = {
            "dispute_id": str(dispute.id),
            "payment_intent_id": str(payment_intent_id),
            "reason": reason,
            "amount": str(dispute_amount),
            "currency": pi.currency,
            "correlation_id": get_correlation_id(),
        }
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="dispute.opened",
                aggregate_type="Dispute",
                aggregate_id=str(dispute.id),
                payload=event_payload,
            )
        )
        enqueue_webhook_deliveries(session, tenant_id, "dispute.opened", event_payload)

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "dispute.opened",
        f"dispute:{dispute.id}",
        {
            "payment_intent_id": str(payment_intent_id),
            "reason": reason,
            "amount": str(dispute_amount),
        },
    )

    return _to_dto(dispute)


def list_disputes(
    session: Session,
    tenant_id: str,
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[DisputeDTO], int]:
    q = select(Dispute).where(Dispute.tenant_id == tenant_id)
    if status:
        q = q.where(Dispute.status == status)

    count_q = select(func.count()).select_from(q.subquery())
    total = session.execute(count_q).scalar() or 0

    q = q.order_by(Dispute.created_at.desc())
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = session.execute(q).scalars().all()
    return [_to_dto(r) for r in rows], total


def get_dispute(session: Session, tenant_id: str, dispute_id: uuid.UUID) -> DisputeDTO:
    d = session.execute(
        select(Dispute).where(Dispute.tenant_id == tenant_id, Dispute.id == dispute_id)
    ).scalar_one_or_none()
    if not d:
        raise http_problem(
            404, "Not Found", "dispute not found", instance=f"/v1/disputes/{dispute_id}"
        )
    return _to_dto(d)


def submit_evidence(
    session: Session, tenant_id: str, dispute_id: uuid.UUID, evidence: dict[str, Any]
) -> DisputeDTO:
    with safe_begin(session):
        d = session.execute(
            select(Dispute)
            .where(Dispute.tenant_id == tenant_id, Dispute.id == dispute_id)
            .with_for_update()
        ).scalar_one_or_none()

        if not d:
            raise http_problem(
                404,
                "Not Found",
                "dispute not found",
                instance=f"/v1/disputes/{dispute_id}/evidence",
            )
        if d.status not in ("OPEN", "UNDER_REVIEW"):
            raise http_problem(
                409,
                "Conflict",
                f"cannot submit evidence for dispute with status {d.status}",
                instance=f"/v1/disputes/{dispute_id}/evidence",
            )

        d.evidence = evidence
        d.status = "UNDER_REVIEW"
        d.updated_at = _utcnow()

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "dispute.evidence_submitted",
        f"dispute:{d.id}",
        {"evidence_keys": list(evidence.keys())},
    )

    return _to_dto(d)


def accept_dispute(session: Session, tenant_id: str, dispute_id: uuid.UUID) -> DisputeDTO:
    with safe_begin(session):
        d = session.execute(
            select(Dispute)
            .where(Dispute.tenant_id == tenant_id, Dispute.id == dispute_id)
            .with_for_update()
        ).scalar_one_or_none()

        if not d:
            raise http_problem(
                404,
                "Not Found",
                "dispute not found",
                instance=f"/v1/disputes/{dispute_id}/accept",
            )
        if d.status not in ("OPEN", "UNDER_REVIEW"):
            raise http_problem(
                409,
                "Conflict",
                f"cannot accept dispute with status {d.status}",
                instance=f"/v1/disputes/{dispute_id}/accept",
            )

        pi = session.execute(
            select(PaymentIntent).where(PaymentIntent.id == d.payment_intent_id)
        ).scalar_one_or_none()

        d.status = "ACCEPTED"
        d.resolved_at = _utcnow()
        d.updated_at = _utcnow()

        if pi:
            _record_loss_ledger(session, tenant_id, d, pi)

        event_payload = {
            "dispute_id": str(d.id),
            "payment_intent_id": str(d.payment_intent_id),
            "amount": str(d.amount),
            "currency": d.currency,
            "correlation_id": get_correlation_id(),
        }
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="dispute.accepted",
                aggregate_type="Dispute",
                aggregate_id=str(d.id),
                payload=event_payload,
            )
        )
        enqueue_webhook_deliveries(session, tenant_id, "dispute.accepted", event_payload)

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "dispute.accepted",
        f"dispute:{d.id}",
        {"amount": str(d.amount)},
    )

    return _to_dto(d)


def resolve_dispute(
    session: Session, tenant_id: str, dispute_id: uuid.UUID, won: bool
) -> DisputeDTO:
    with safe_begin(session):
        d = session.execute(
            select(Dispute)
            .where(Dispute.tenant_id == tenant_id, Dispute.id == dispute_id)
            .with_for_update()
        ).scalar_one_or_none()

        if not d:
            raise http_problem(
                404,
                "Not Found",
                "dispute not found",
                instance=f"/v1/disputes/{dispute_id}/resolve",
            )
        if d.status not in ("OPEN", "UNDER_REVIEW"):
            raise http_problem(
                409,
                "Conflict",
                f"cannot resolve dispute with status {d.status}",
                instance=f"/v1/disputes/{dispute_id}/resolve",
            )

        d.status = "WON" if won else "LOST"
        d.resolved_at = _utcnow()
        d.updated_at = _utcnow()

        if not won:
            pi = session.execute(
                select(PaymentIntent).where(PaymentIntent.id == d.payment_intent_id)
            ).scalar_one_or_none()
            if pi:
                _record_loss_ledger(session, tenant_id, d, pi)

        event_payload = {
            "dispute_id": str(d.id),
            "payment_intent_id": str(d.payment_intent_id),
            "outcome": d.status,
            "amount": str(d.amount),
            "currency": d.currency,
            "correlation_id": get_correlation_id(),
        }
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="dispute.resolved",
                aggregate_type="Dispute",
                aggregate_id=str(d.id),
                payload=event_payload,
            )
        )
        enqueue_webhook_deliveries(session, tenant_id, "dispute.resolved", event_payload)

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "dispute.resolved",
        f"dispute:{d.id}",
        {"outcome": d.status, "amount": str(d.amount)},
    )

    return _to_dto(d)
