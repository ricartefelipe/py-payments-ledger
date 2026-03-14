from __future__ import annotations

import hashlib
import hmac
import logging

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from src.api.deps.db import get_db
from src.application.payments import update_payment_from_stripe_event
from src.shared.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["mercadopago-webhooks"])

MP_STATUS_MAP: dict[str, str] = {
    "approved": "SETTLED",
    "authorized": "AUTHORIZED",
    "in_process": "AUTHORIZED",
    "rejected": "FAILED",
    "cancelled": "VOIDED",
    "refunded": "REFUNDED",
    "charged_back": "REFUNDED",
}


def verify_mp_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/mercadopago")
async def mercadopago_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    webhook_secret = settings.mercadopago_webhook_secret

    if webhook_secret:
        raw_body = await request.body()
        signature = request.headers.get("x-signature", "")
        if not signature or not verify_mp_signature(raw_body, signature, webhook_secret):
            logger.warning("Invalid Mercado Pago webhook signature")
            return JSONResponse(status_code=400, content={"status": "invalid_signature"})

    try:
        payload = await request.json()
    except Exception:
        logger.warning("Invalid Mercado Pago webhook payload")
        return JSONResponse(status_code=400, content={"status": "invalid_payload"})

    action = payload.get("action", "")
    topic = payload.get("type", payload.get("topic", ""))
    logger.info("Mercado Pago webhook received: action=%s type=%s", action, topic)

    if topic not in ("payment", "payment.created", "payment.updated"):
        logger.debug("Unhandled Mercado Pago webhook type: %s", topic)
        return {"status": "ignored"}

    data_resource = payload.get("data", {})
    payment_id = str(data_resource.get("id", ""))

    if not payment_id:
        resource_url = payload.get("resource", "")
        if resource_url:
            payment_id = resource_url.rstrip("/").split("/")[-1]

    if not payment_id:
        logger.warning("Mercado Pago webhook missing payment id")
        return {"status": "missing_id"}

    settings = get_settings()
    access_token = settings.mercadopago_access_token
    api_url = settings.mercadopago_api_url

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{api_url}/v1/payments/{payment_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 404:
                logger.warning("Payment %s not found in Mercado Pago", payment_id)
                return {"status": "not_found"}
            resp.raise_for_status()
            payment_data = resp.json()
    except Exception:
        logger.exception("Failed to fetch payment %s from Mercado Pago", payment_id)
        return {"status": "fetch_error"}

    mp_status = payment_data.get("status", "")
    internal_status = MP_STATUS_MAP.get(mp_status)

    if not internal_status:
        logger.debug("Unhandled Mercado Pago payment status: %s", mp_status)
        return {"status": "ignored"}

    try:
        update_payment_from_stripe_event(db, payment_id, internal_status)
    except Exception:
        logger.exception("Failed to process Mercado Pago webhook for payment %s", payment_id)

    return {"status": "ok"}
