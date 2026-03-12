from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.api.deps.db import get_db
from src.application.payments import update_payment_from_stripe_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["pagseguro-webhooks"])

PAGSEGURO_EVENT_STATUS_MAP: dict[str, str] = {
    "PAID": "SETTLED",
    "CANCELLED": "VOIDED",
    "DECLINED": "FAILED",
    "AUTHORIZED": "AUTHORIZED",
    "IN_ANALYSIS": "AUTHORIZED",
}


@router.post("/pagseguro")
async def pagseguro_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        payload = await request.json()
    except Exception:
        logger.warning("Invalid PagSeguro webhook payload")
        return {"status": "invalid_payload"}

    charges = payload.get("charges", [])
    if not charges and payload.get("id"):
        charges = [payload]

    for charge in charges:
        charge_id = charge.get("id", "")
        ps_status = charge.get("status", "")
        logger.info(
            "PagSeguro webhook received: charge_id=%s status=%s",
            charge_id, ps_status,
        )

        internal_status = PAGSEGURO_EVENT_STATUS_MAP.get(ps_status)
        if not internal_status:
            logger.debug("Unhandled PagSeguro charge status: %s", ps_status)
            continue

        if not charge_id:
            logger.warning("PagSeguro webhook charge missing id")
            continue

        try:
            update_payment_from_stripe_event(db, charge_id, internal_status)
        except Exception:
            logger.exception(
                "Failed to process PagSeguro webhook for charge %s", charge_id
            )

    return {"status": "ok"}
