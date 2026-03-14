from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from src.api.deps.db import get_db
from src.application.payments import update_payment_from_stripe_event
from src.shared.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["pagseguro-webhooks"])

PAGSEGURO_EVENT_STATUS_MAP: dict[str, str] = {
    "PAID": "SETTLED",
    "CANCELLED": "VOIDED",
    "DECLINED": "FAILED",
    "AUTHORIZED": "AUTHORIZED",
    "IN_ANALYSIS": "AUTHORIZED",
}

REQUIRED_PAGSEGURO_HEADERS = ("x-reference-id",)


async def _fetch_charge_status(charge_id: str, token: str, api_url: str) -> str | None:
    """Fetch charge directly from PagSeguro API to verify reported status."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{api_url}/charges/{charge_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 404:
                logger.warning("Charge %s not found in PagSeguro API", charge_id)
                return None
            resp.raise_for_status()
            return resp.json().get("status")
    except Exception:
        logger.exception("Failed to fetch charge %s from PagSeguro", charge_id)
        return None


@router.post("/pagseguro")
async def pagseguro_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    for hdr in REQUIRED_PAGSEGURO_HEADERS:
        if hdr not in request.headers:
            logger.warning("PagSeguro webhook missing required header: %s", hdr)
            return JSONResponse(
                status_code=400, content={"status": "missing_header", "header": hdr}
            )

    try:
        payload = await request.json()
    except Exception:
        logger.warning("Invalid PagSeguro webhook payload")
        return JSONResponse(status_code=400, content={"status": "invalid_payload"})

    settings = get_settings()

    charges = payload.get("charges", [])
    if not charges and payload.get("id"):
        charges = [payload]

    for charge in charges:
        charge_id = charge.get("id", "")
        ps_status = charge.get("status", "")
        logger.info(
            "PagSeguro webhook received: charge_id=%s status=%s",
            charge_id,
            ps_status,
        )

        if not charge_id:
            logger.warning("PagSeguro webhook charge missing id")
            continue

        verified_status = await _fetch_charge_status(
            charge_id, settings.pagseguro_token, settings.pagseguro_api_url
        )
        if verified_status is None:
            logger.warning("Could not verify charge %s, skipping", charge_id)
            continue

        if verified_status != ps_status:
            logger.warning(
                "PagSeguro status mismatch: webhook=%s api=%s for charge %s",
                ps_status,
                verified_status,
                charge_id,
            )

        internal_status = PAGSEGURO_EVENT_STATUS_MAP.get(verified_status)
        if not internal_status:
            logger.debug("Unhandled PagSeguro charge status: %s", verified_status)
            continue

        try:
            update_payment_from_stripe_event(db, charge_id, internal_status)
        except Exception:
            logger.exception("Failed to process PagSeguro webhook for charge %s", charge_id)

    return {"status": "ok"}
