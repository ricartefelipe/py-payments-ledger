from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.application.payments import (
    PaymentIntentDTO,
    confirm_payment_intent,
    create_payment_intent,
)
from src.application.security import _audit
from src.infrastructure.db.models import PaymentLink
from src.infrastructure.db.session import safe_begin
from src.shared.correlation import get_correlation_id, get_subject
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaymentLinkDTO(BaseModel):
    id: str
    tenant_id: str
    amount: str
    currency: str
    description: str | None = None
    customer_email: str | None = None
    status: str
    payment_intent_id: str | None = None
    expires_at: str
    used_at: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str


def _to_dto(link: PaymentLink) -> PaymentLinkDTO:
    return PaymentLinkDTO(
        id=link.id,
        tenant_id=link.tenant_id,
        amount=str(link.amount),
        currency=link.currency,
        description=link.description,
        customer_email=link.customer_email,
        status=link.status,
        payment_intent_id=str(link.payment_intent_id) if link.payment_intent_id else None,
        expires_at=link.expires_at.isoformat(),
        used_at=link.used_at.isoformat() if link.used_at else None,
        metadata=link.metadata_json,
        created_at=link.created_at.isoformat(),
    )


def create_payment_link(
    session: Session,
    tenant_id: str,
    amount: float,
    currency: str,
    description: str | None = None,
    customer_email: str | None = None,
    expires_hours: int = 24,
    metadata: dict[str, Any] | None = None,
) -> PaymentLinkDTO:
    if amount <= 0:
        raise http_problem(400, "Bad Request", "amount must be > 0", instance="/v1/payment-links")
    if currency not in ("BRL", "USD", "EUR"):
        raise http_problem(
            400, "Bad Request", "unsupported currency", instance="/v1/payment-links"
        )

    now = _utcnow()
    link = PaymentLink(
        tenant_id=tenant_id,
        amount=amount,
        currency=currency,
        description=description,
        customer_email=customer_email,
        status="ACTIVE",
        expires_at=now + timedelta(hours=expires_hours),
        metadata_json=metadata,
        created_at=now,
    )

    with safe_begin(session):
        session.add(link)
        session.flush()

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "payment_link.created",
        f"payment_link:{link.id}",
        {"amount": str(amount), "currency": currency},
    )

    return _to_dto(link)


def list_payment_links(
    session: Session,
    tenant_id: str,
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[PaymentLinkDTO], int]:
    q = select(PaymentLink).where(PaymentLink.tenant_id == tenant_id)
    if status:
        q = q.where(PaymentLink.status == status)

    count_q = select(func.count()).select_from(q.subquery())
    total = session.execute(count_q).scalar() or 0

    q = q.order_by(PaymentLink.created_at.desc())
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = session.execute(q).scalars().all()
    return [_to_dto(r) for r in rows], total


def get_payment_link(session: Session, link_id: str) -> PaymentLinkDTO:
    link = session.execute(
        select(PaymentLink).where(PaymentLink.id == link_id)
    ).scalar_one_or_none()
    if not link:
        raise http_problem(
            404, "Not Found", "payment link not found", instance=f"/v1/payment-links/{link_id}"
        )
    return _to_dto(link)


def use_payment_link(
    session: Session,
    link_id: str,
    customer_ref: str,
) -> PaymentLinkDTO:
    with safe_begin(session):
        link = session.execute(
            select(PaymentLink).where(PaymentLink.id == link_id).with_for_update()
        ).scalar_one_or_none()
        if not link:
            raise http_problem(
                404,
                "Not Found",
                "payment link not found",
                instance=f"/v1/payment-links/{link_id}/pay",
            )
        if link.status != "ACTIVE":
            raise http_problem(
                409,
                "Conflict",
                f"payment link status is {link.status}",
                instance=f"/v1/payment-links/{link_id}/pay",
            )
        if link.expires_at <= _utcnow():
            link.status = "EXPIRED"
            session.flush()
            raise http_problem(
                410,
                "Gone",
                "payment link has expired",
                instance=f"/v1/payment-links/{link_id}/pay",
            )

        pi_dto = create_payment_intent(
            session,
            link.tenant_id,
            float(link.amount),
            link.currency,
            customer_ref,
        )

        pi_dto = confirm_payment_intent(
            session,
            link.tenant_id,
            uuid.UUID(pi_dto.id),
        )

        link.payment_intent_id = uuid.UUID(pi_dto.id)
        link.status = "USED"
        link.used_at = _utcnow()
        session.flush()

    _audit(
        session,
        link.tenant_id,
        customer_ref,
        "payment_link.used",
        f"payment_link:{link.id}",
        {"payment_intent_id": pi_dto.id},
    )

    return _to_dto(link)


def cancel_payment_link(
    session: Session,
    tenant_id: str,
    link_id: str,
) -> PaymentLinkDTO:
    with safe_begin(session):
        link = session.execute(
            select(PaymentLink).where(
                PaymentLink.id == link_id,
                PaymentLink.tenant_id == tenant_id,
            ).with_for_update()
        ).scalar_one_or_none()
        if not link:
            raise http_problem(
                404,
                "Not Found",
                "payment link not found",
                instance=f"/v1/payment-links/{link_id}/cancel",
            )
        if link.status != "ACTIVE":
            raise http_problem(
                409,
                "Conflict",
                f"cannot cancel link with status {link.status}",
                instance=f"/v1/payment-links/{link_id}/cancel",
            )
        link.status = "CANCELLED"
        session.flush()

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "payment_link.cancelled",
        f"payment_link:{link.id}",
        {},
    )

    return _to_dto(link)


def expire_stale_links(session: Session) -> int:
    now = _utcnow()
    with safe_begin(session):
        result = session.execute(
            update(PaymentLink)
            .where(PaymentLink.status == "ACTIVE", PaymentLink.expires_at < now)
            .values(status="EXPIRED")
        )
        count = result.rowcount or 0

    if count > 0:
        log.info("expired %d stale payment links", count)
    return count
