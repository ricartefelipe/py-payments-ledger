from __future__ import annotations

import logging

import stripe
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.deps.db import get_db
from src.application.disputes import open_dispute, resolve_dispute
from src.application.payments import update_payment_from_stripe_event
from src.infrastructure.db.models import PaymentIntent
from src.shared.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["stripe-webhooks"])


def _validate_event_tenant(db: Session, request: Request, gateway_ref: str | None) -> bool:
    """When X-Tenant-Id is present, verify the payment belongs to that tenant."""
    tenant_id = request.headers.get("x-tenant-id")
    if not tenant_id or not gateway_ref:
        return True
    pi = db.execute(
        select(PaymentIntent).where(PaymentIntent.gateway_ref == gateway_ref)
    ).scalar_one_or_none()
    if pi and str(pi.tenant_id) != tenant_id:
        logger.warning(
            "Stripe webhook tenant mismatch: header=%s, payment_tenant=%s, gateway_ref=%s",
            tenant_id,
            pi.tenant_id,
            gateway_ref,
        )
        return False
    return True


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
    stripe_signature: str = Header(alias="Stripe-Signature"),
):
    settings = get_settings()
    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, settings.stripe_webhook_secret
        )
    except stripe.error.SignatureVerificationError:
        logger.warning("Invalid Stripe webhook signature")
        return JSONResponse(status_code=400, content={"status": "invalid_signature"})
    except ValueError:
        logger.warning("Invalid Stripe webhook payload")
        return JSONResponse(status_code=400, content={"status": "invalid_payload"})

    event_type = event["type"]
    data_object = event["data"]["object"]
    logger.info("Stripe webhook received: type=%s id=%s", event_type, event.get("id"))

    gateway_ref = data_object.get("payment_intent") or data_object.get("id")
    if not _validate_event_tenant(db, request, gateway_ref):
        return JSONResponse(status_code=403, content={"status": "tenant_mismatch"})

    if event_type == "payment_intent.succeeded":
        update_payment_from_stripe_event(db, data_object["id"], "SETTLED")
    elif event_type == "payment_intent.payment_failed":
        update_payment_from_stripe_event(db, data_object["id"], "FAILED")
    elif event_type == "payment_intent.canceled":
        update_payment_from_stripe_event(db, data_object["id"], "VOIDED")
    elif event_type == "charge.refunded":
        pi_id = data_object.get("payment_intent")
        if pi_id:
            update_payment_from_stripe_event(db, pi_id, "REFUNDED")
    elif event_type == "charge.dispute.created":
        _handle_dispute_created(db, data_object)
    elif event_type == "charge.dispute.closed":
        _handle_dispute_closed(db, data_object)
    else:
        logger.debug("Unhandled Stripe event type: %s", event_type)

    return {"status": "ok"}


def _handle_dispute_created(db: Session, data_object: dict) -> None:
    from decimal import Decimal

    dispute_ref = data_object.get("id", "")
    amount = data_object.get("amount", 0)
    reason_map = {
        "fraudulent": "FRAUDULENT",
        "duplicate": "DUPLICATE",
        "product_not_received": "PRODUCT_NOT_RECEIVED",
    }
    raw_reason = data_object.get("reason", "other")
    reason = reason_map.get(raw_reason, "OTHER")

    pi_id_str = data_object.get("payment_intent")
    if not pi_id_str:
        logger.warning("Stripe dispute has no payment_intent: dispute=%s", dispute_ref)
        return

    pi = db.execute(
        select(PaymentIntent).where(PaymentIntent.gateway_ref == pi_id_str)
    ).scalar_one_or_none()
    if not pi:
        logger.warning("No PaymentIntent for gateway_ref=%s from dispute", pi_id_str)
        return

    dispute_amount = Decimal(str(amount)) / Decimal("100") if amount else Decimal(str(pi.amount))

    try:
        open_dispute(
            db,
            str(pi.tenant_id),
            pi.id,
            reason,
            amount=dispute_amount,
            gateway_dispute_ref=dispute_ref,
        )
        logger.info("Auto-created dispute from Stripe: dispute_ref=%s pi=%s", dispute_ref, pi.id)
    except Exception:
        logger.exception("Failed to auto-create dispute from Stripe webhook")


def _handle_dispute_closed(db: Session, data_object: dict) -> None:
    from src.infrastructure.db.models import Dispute

    dispute_ref = data_object.get("id", "")
    status = data_object.get("status", "")

    d = db.execute(
        select(Dispute).where(Dispute.gateway_dispute_ref == dispute_ref)
    ).scalar_one_or_none()
    if not d:
        logger.warning("No Dispute found for gateway_dispute_ref=%s", dispute_ref)
        return

    if d.status not in ("OPEN", "UNDER_REVIEW"):
        logger.info("Dispute %s already resolved (status=%s), skipping", d.id, d.status)
        return

    won = status == "won"
    try:
        resolve_dispute(db, str(d.tenant_id), d.id, won=won)
        logger.info(
            "Auto-resolved dispute from Stripe: dispute_ref=%s outcome=%s",
            dispute_ref,
            "WON" if won else "LOST",
        )
    except Exception:
        logger.exception("Failed to auto-resolve dispute from Stripe webhook")
