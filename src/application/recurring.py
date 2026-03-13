from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from dateutil.relativedelta import relativedelta
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application.payments import create_payment_intent
from src.infrastructure.db.models import RecurringCharge
from src.infrastructure.db.session import safe_begin
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)

VALID_INTERVALS = ("monthly", "yearly")
VALID_CURRENCIES = ("BRL", "USD", "EUR")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _advance_next_charge(current: datetime, interval: str) -> datetime:
    if interval == "monthly":
        return current + relativedelta(months=1)
    if interval == "yearly":
        return current + relativedelta(years=1)
    raise ValueError(f"unknown interval: {interval}")


class RecurringChargeDTO(BaseModel):
    id: str
    tenant_id: str
    description: str
    amount_cents: int
    currency: str
    interval: str
    next_charge_at: str
    status: str
    gateway_customer_ref: str | None = None
    created_at: str
    updated_at: str


def _to_dto(rc: RecurringCharge) -> RecurringChargeDTO:
    return RecurringChargeDTO(
        id=str(rc.id),
        tenant_id=rc.tenant_id,
        description=rc.description,
        amount_cents=rc.amount_cents,
        currency=rc.currency,
        interval=rc.interval,
        next_charge_at=rc.next_charge_at.isoformat(),
        status=rc.status,
        gateway_customer_ref=rc.gateway_customer_ref,
        created_at=rc.created_at.isoformat(),
        updated_at=rc.updated_at.isoformat(),
    )


def create_recurring_charge(
    session: Session,
    tenant_id: str,
    description: str,
    amount_cents: int,
    currency: str,
    interval: str,
    gateway_customer_ref: str | None = None,
) -> RecurringChargeDTO:
    if amount_cents <= 0:
        raise http_problem(
            400, "Bad Request", "amount_cents must be > 0", instance="/v1/recurring-charges"
        )
    if currency not in VALID_CURRENCIES:
        raise http_problem(
            400, "Bad Request", "unsupported currency", instance="/v1/recurring-charges"
        )
    if interval not in VALID_INTERVALS:
        raise http_problem(
            400,
            "Bad Request",
            f"interval must be one of {VALID_INTERVALS}",
            instance="/v1/recurring-charges",
        )

    now = _utcnow()
    next_charge = _advance_next_charge(now, interval)

    with safe_begin(session):
        rc = RecurringCharge(
            tenant_id=tenant_id,
            description=description,
            amount_cents=amount_cents,
            currency=currency,
            interval=interval,
            next_charge_at=next_charge,
            status="ACTIVE",
            gateway_customer_ref=gateway_customer_ref,
            created_at=now,
            updated_at=now,
        )
        session.add(rc)
        session.flush()

    log.info("recurring charge created", extra={"charge_id": str(rc.id), "tenant_id": tenant_id})
    return _to_dto(rc)


def _get_charge_or_404(session: Session, charge_id: uuid.UUID, tenant_id: str) -> RecurringCharge:
    rc = session.execute(
        select(RecurringCharge).where(
            RecurringCharge.id == charge_id,
            RecurringCharge.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if not rc:
        raise http_problem(
            404,
            "Not Found",
            "recurring charge not found",
            instance=f"/v1/recurring-charges/{charge_id}",
        )
    return rc


def pause_recurring_charge(
    session: Session, charge_id: uuid.UUID, tenant_id: str
) -> RecurringChargeDTO:
    with safe_begin(session):
        rc = _get_charge_or_404(session, charge_id, tenant_id)
        if rc.status != "ACTIVE":
            raise http_problem(
                409,
                "Conflict",
                f"cannot pause charge with status {rc.status}",
                instance=f"/v1/recurring-charges/{charge_id}/pause",
            )
        rc.status = "PAUSED"
        rc.updated_at = _utcnow()

    return _to_dto(rc)


def resume_recurring_charge(
    session: Session, charge_id: uuid.UUID, tenant_id: str
) -> RecurringChargeDTO:
    with safe_begin(session):
        rc = _get_charge_or_404(session, charge_id, tenant_id)
        if rc.status != "PAUSED":
            raise http_problem(
                409,
                "Conflict",
                f"cannot resume charge with status {rc.status}",
                instance=f"/v1/recurring-charges/{charge_id}/resume",
            )
        rc.status = "ACTIVE"
        rc.updated_at = _utcnow()

    return _to_dto(rc)


def cancel_recurring_charge(
    session: Session, charge_id: uuid.UUID, tenant_id: str
) -> RecurringChargeDTO:
    with safe_begin(session):
        rc = _get_charge_or_404(session, charge_id, tenant_id)
        if rc.status == "CANCELLED":
            return _to_dto(rc)
        rc.status = "CANCELLED"
        rc.updated_at = _utcnow()

    return _to_dto(rc)


def list_recurring_charges(session: Session, tenant_id: str) -> list[RecurringChargeDTO]:
    rows = (
        session.execute(
            select(RecurringCharge)
            .where(RecurringCharge.tenant_id == tenant_id)
            .order_by(RecurringCharge.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [_to_dto(r) for r in rows]


def process_due_charges(session: Session, gateway: Any = None) -> int:
    now = _utcnow()
    processed = 0

    due = (
        session.execute(
            select(RecurringCharge)
            .where(
                RecurringCharge.status == "ACTIVE",
                RecurringCharge.next_charge_at <= now,
            )
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )

    for rc in due:
        try:
            amount = Decimal(rc.amount_cents) / Decimal(100)
            customer_ref = rc.gateway_customer_ref or f"recurring:{rc.id}"

            create_payment_intent(
                session,
                rc.tenant_id,
                float(amount),
                rc.currency,
                customer_ref,
                gateway=gateway,
                idempotency_key=f"recurring-{rc.id}-{rc.next_charge_at.isoformat()}",
            )

            with safe_begin(session):
                rc.next_charge_at = _advance_next_charge(rc.next_charge_at, rc.interval)
                rc.updated_at = _utcnow()

            processed += 1
            log.info(
                "recurring charge processed",
                extra={"charge_id": str(rc.id), "tenant_id": rc.tenant_id},
            )
        except Exception:
            log.exception(
                "failed to process recurring charge",
                extra={"charge_id": str(rc.id), "tenant_id": rc.tenant_id},
            )

    return processed
