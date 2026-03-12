from __future__ import annotations

import logging

import stripe
from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from src.api.deps.db import get_db
from src.application.payments import update_payment_from_stripe_event
from src.shared.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["stripe-webhooks"])


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
        return {"status": "invalid_signature"}, 400
    except ValueError:
        logger.warning("Invalid Stripe webhook payload")
        return {"status": "invalid_payload"}, 400

    event_type = event["type"]
    data_object = event["data"]["object"]
    logger.info("Stripe webhook received: type=%s id=%s", event_type, event.get("id"))

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
    else:
        logger.debug("Unhandled Stripe event type: %s", event_type)

    return {"status": "ok"}
