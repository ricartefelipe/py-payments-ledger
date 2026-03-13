from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application.security import _audit
from src.application.webhooks import enqueue_webhook_deliveries
from src.infrastructure.db.models import (
    OutboxEvent,
    PaymentIntent,
    ReconciliationDiscrepancy,
)
from src.infrastructure.db.session import safe_begin
from src.shared.correlation import get_correlation_id, get_subject
from src.shared.logging import get_logger

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DiscrepancyDTO(BaseModel):
    id: str
    tenant_id: str
    payment_intent_id: str | None
    discrepancy_type: str
    gateway_ref: str | None
    expected_amount: str | None
    actual_amount: str | None
    expected_status: str | None
    actual_status: str | None
    resolved: bool
    created_at: str


STRIPE_STATUS_TO_LOCAL: dict[str, str] = {
    "requires_capture": "AUTHORIZED",
    "requires_confirmation": "AUTHORIZED",
    "succeeded": "SETTLED",
    "canceled": "FAILED",
    "requires_payment_method": "FAILED",
}

LOCAL_STATUS_TO_STRIPE: dict[str, list[str]] = {
    "AUTHORIZED": ["requires_capture", "requires_confirmation"],
    "SETTLED": ["succeeded"],
    "FAILED": ["canceled", "requires_payment_method"],
}


def reconcile_transactions(
    session: Session,
    tenant_id: str,
    gateway_transactions: list[dict[str, Any]],
    *,
    auto_fix: bool = False,
) -> list[DiscrepancyDTO]:
    """Compare gateway transactions with local PaymentIntents.

    gateway_transactions: list of dicts with keys:
        gateway_ref, amount, currency, status
    auto_fix: when True, automatically update local status to match gateway
        for STATUS_MISMATCH discrepancies.
    """
    discrepancies: list[DiscrepancyDTO] = []
    auto_fixed: list[str] = []

    with safe_begin(session):
        for gtx in gateway_transactions:
            gw_ref = gtx["gateway_ref"]
            gw_amount = Decimal(str(gtx["amount"]))
            gw_status = gtx["status"]

            pi = session.execute(
                select(PaymentIntent).where(
                    PaymentIntent.tenant_id == tenant_id,
                    PaymentIntent.gateway_ref == gw_ref,
                )
            ).scalar_one_or_none()

            if not pi:
                disc = ReconciliationDiscrepancy(
                    tenant_id=tenant_id,
                    discrepancy_type="MISSING_LOCAL",
                    gateway_ref=gw_ref,
                    actual_amount=gw_amount,
                    actual_status=gw_status,
                    details={"gateway_transaction": gtx},
                )
                session.add(disc)
                session.flush()
                discrepancies.append(_to_dto(disc))
                continue

            if pi.amount != gw_amount:
                disc = ReconciliationDiscrepancy(
                    tenant_id=tenant_id,
                    payment_intent_id=pi.id,
                    discrepancy_type="AMOUNT_MISMATCH",
                    gateway_ref=gw_ref,
                    expected_amount=pi.amount,
                    actual_amount=gw_amount,
                    details={"local_amount": str(pi.amount), "gateway_amount": str(gw_amount)},
                )
                session.add(disc)
                session.flush()
                discrepancies.append(_to_dto(disc))

            expected_gw_statuses = LOCAL_STATUS_TO_STRIPE.get(pi.status, [])
            if gw_status not in expected_gw_statuses and expected_gw_statuses:
                resolved_by_fix = False
                if auto_fix:
                    new_local_status = STRIPE_STATUS_TO_LOCAL.get(gw_status)
                    if new_local_status and new_local_status != pi.status:
                        old_status = pi.status
                        pi.status = new_local_status
                        pi.updated_at = _utcnow()
                        resolved_by_fix = True
                        auto_fixed.append(gw_ref)
                        log.info(
                            "reconciliation auto-fix applied",
                            extra={
                                "tenant_id": tenant_id,
                                "gateway_ref": gw_ref,
                                "old_status": old_status,
                                "new_status": new_local_status,
                                "gateway_status": gw_status,
                            },
                        )

                disc = ReconciliationDiscrepancy(
                    tenant_id=tenant_id,
                    payment_intent_id=pi.id,
                    discrepancy_type="STATUS_MISMATCH",
                    gateway_ref=gw_ref,
                    expected_status=pi.status,
                    actual_status=gw_status,
                    resolved=resolved_by_fix,
                    details={
                        "expected_gateway_statuses": expected_gw_statuses,
                        "auto_fixed": resolved_by_fix,
                    },
                )
                session.add(disc)
                session.flush()
                discrepancies.append(_to_dto(disc))

        local_with_ref = (
            session.execute(
                select(PaymentIntent).where(
                    PaymentIntent.tenant_id == tenant_id,
                    PaymentIntent.gateway_ref.isnot(None),
                )
            )
            .scalars()
            .all()
        )

        gw_refs = {gtx["gateway_ref"] for gtx in gateway_transactions}
        for pi in local_with_ref:
            if pi.gateway_ref and pi.gateway_ref not in gw_refs:
                disc = ReconciliationDiscrepancy(
                    tenant_id=tenant_id,
                    payment_intent_id=pi.id,
                    discrepancy_type="MISSING_REMOTE",
                    gateway_ref=pi.gateway_ref,
                    expected_amount=pi.amount,
                    expected_status=pi.status,
                    details={"payment_intent_id": str(pi.id)},
                )
                session.add(disc)
                session.flush()
                discrepancies.append(_to_dto(disc))

        if discrepancies:
            event_payload = {
                "tenant_id": tenant_id,
                "discrepancy_count": len(discrepancies),
                "types": list({d.discrepancy_type for d in discrepancies}),
                "auto_fixed_count": len(auto_fixed),
                "correlation_id": get_correlation_id(),
            }
            session.add(
                OutboxEvent(
                    tenant_id=tenant_id,
                    event_type="reconciliation.discrepancy_found",
                    aggregate_type="Reconciliation",
                    aggregate_id=str(uuid.uuid4()),
                    payload=event_payload,
                )
            )
            enqueue_webhook_deliveries(
                session,
                tenant_id,
                "reconciliation.discrepancy_found",
                event_payload,
            )

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "reconciliation.run",
        f"tenant:{tenant_id}",
        {
            "transaction_count": len(gateway_transactions),
            "discrepancy_count": len(discrepancies),
            "auto_fixed_count": len(auto_fixed),
        },
    )

    return discrepancies


def list_discrepancies(
    session: Session, tenant_id: str, resolved: bool | None = None
) -> list[DiscrepancyDTO]:
    q = select(ReconciliationDiscrepancy).where(ReconciliationDiscrepancy.tenant_id == tenant_id)
    if resolved is not None:
        q = q.where(ReconciliationDiscrepancy.resolved == resolved)
    q = q.order_by(ReconciliationDiscrepancy.created_at.desc()).limit(200)
    rows = session.execute(q).scalars().all()
    return [_to_dto(r) for r in rows]


def resolve_discrepancy(session: Session, tenant_id: str, disc_id: uuid.UUID) -> DiscrepancyDTO:
    with safe_begin(session):
        disc = session.execute(
            select(ReconciliationDiscrepancy).where(
                ReconciliationDiscrepancy.tenant_id == tenant_id,
                ReconciliationDiscrepancy.id == disc_id,
            )
        ).scalar_one_or_none()
        if not disc:
            from src.shared.problem import http_problem

            raise http_problem(
                404,
                "Not Found",
                "discrepancy not found",
                instance=f"/v1/reconciliation/{disc_id}",
            )
        disc.resolved = True

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "discrepancy.resolved",
        f"discrepancy:{disc_id}",
        {"discrepancy_type": disc.discrepancy_type},
    )

    return _to_dto(disc)


def _to_dto(d: ReconciliationDiscrepancy) -> DiscrepancyDTO:
    return DiscrepancyDTO(
        id=str(d.id),
        tenant_id=str(d.tenant_id),
        payment_intent_id=str(d.payment_intent_id) if d.payment_intent_id else None,
        discrepancy_type=d.discrepancy_type,
        gateway_ref=d.gateway_ref,
        expected_amount=str(d.expected_amount) if d.expected_amount is not None else None,
        actual_amount=str(d.actual_amount) if d.actual_amount is not None else None,
        expected_status=d.expected_status,
        actual_status=d.actual_status,
        resolved=d.resolved,
        created_at=d.created_at.isoformat(),
    )
