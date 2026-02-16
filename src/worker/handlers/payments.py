from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from src.application.payments import post_ledger_for_authorized_payment
from src.shared.logging import get_logger

log = get_logger(__name__)


def handle_event(session: Session, routing_key: str, payload: dict[str, Any]) -> None:
    if routing_key == "payment.authorized":
        pid = uuid.UUID(payload["payment_intent_id"])
        tenant_id = payload["tenant_id"]
        post_ledger_for_authorized_payment(session, tenant_id, pid)
        log.info("ledger posted", extra={"payment_intent_id": str(pid), "tenant_id": tenant_id})
        return
    # ignore other events
