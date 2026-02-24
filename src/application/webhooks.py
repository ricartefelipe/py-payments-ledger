from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.infrastructure.db.models import WebhookDelivery, WebhookEndpoint
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)

RETRY_DELAYS = [60, 300, 1800]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WebhookEndpointDTO(BaseModel):
    id: str
    url: str
    events: list[str]
    is_active: bool
    created_at: str


class WebhookDeliveryDTO(BaseModel):
    id: str
    endpoint_id: str
    event_type: str
    status: str
    attempts: int
    response_code: int | None
    created_at: str


def create_webhook_endpoint(
    session: Session,
    tenant_id: str,
    url: str,
    events: list[str],
) -> WebhookEndpointDTO:
    secret = secrets.token_hex(32)
    with session.begin():
        endpoint = WebhookEndpoint(
            tenant_id=tenant_id,
            url=url,
            secret=secret,
            events=events,
            is_active=True,
            created_at=_utcnow(),
        )
        session.add(endpoint)
        session.flush()

    return WebhookEndpointDTO(
        id=str(endpoint.id),
        url=endpoint.url,
        events=list(endpoint.events),
        is_active=endpoint.is_active,
        created_at=endpoint.created_at.isoformat(),
    )


def list_webhook_endpoints(session: Session, tenant_id: str) -> list[WebhookEndpointDTO]:
    rows = session.execute(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.tenant_id == tenant_id)
        .order_by(WebhookEndpoint.created_at.desc())
    ).scalars().all()
    return [
        WebhookEndpointDTO(
            id=str(e.id), url=e.url, events=list(e.events),
            is_active=e.is_active, created_at=e.created_at.isoformat(),
        )
        for e in rows
    ]


def delete_webhook_endpoint(session: Session, tenant_id: str, endpoint_id: uuid.UUID) -> None:
    with session.begin():
        ep = session.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.tenant_id == tenant_id,
                WebhookEndpoint.id == endpoint_id,
            )
        ).scalar_one_or_none()
        if not ep:
            raise http_problem(
                404, "Not Found", "webhook endpoint not found",
                instance=f"/v1/webhooks/{endpoint_id}",
            )
        session.delete(ep)


def enqueue_webhook_deliveries(
    session: Session, tenant_id: str, event_type: str, payload: dict[str, Any]
) -> int:
    endpoints = session.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.tenant_id == tenant_id,
            WebhookEndpoint.is_active.is_(True),
        )
    ).scalars().all()

    count = 0
    for ep in endpoints:
        if event_type not in ep.events and "*" not in ep.events:
            continue
        delivery = WebhookDelivery(
            endpoint_id=ep.id,
            tenant_id=tenant_id,
            event_type=event_type,
            payload=payload,
            status="PENDING",
            created_at=_utcnow(),
        )
        session.add(delivery)
        count += 1

    return count


def compute_signature(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def claim_pending_deliveries(session: Session, limit: int = 50) -> list[WebhookDelivery]:
    now = _utcnow()
    with session.begin():
        rows = session.execute(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status.in_(("PENDING", "RETRYING")),
                WebhookDelivery.next_retry_at <= now,
            )
            .order_by(WebhookDelivery.created_at.asc())
            .limit(limit)
        ).scalars().all()
    return list(rows)


def mark_delivery_success(session: Session, delivery_id: uuid.UUID, response_code: int) -> None:
    with session.begin():
        d = session.get(WebhookDelivery, delivery_id)
        if not d:
            return
        d.status = "DELIVERED"
        d.response_code = response_code
        d.attempts += 1
        d.last_attempt_at = _utcnow()


def mark_delivery_failed(session: Session, delivery_id: uuid.UUID, response_code: int | None) -> None:
    with session.begin():
        d = session.get(WebhookDelivery, delivery_id)
        if not d:
            return
        d.attempts += 1
        d.last_attempt_at = _utcnow()
        d.response_code = response_code
        if d.attempts >= len(RETRY_DELAYS):
            d.status = "FAILED"
        else:
            d.status = "RETRYING"
            d.next_retry_at = _utcnow() + timedelta(seconds=RETRY_DELAYS[d.attempts - 1])
