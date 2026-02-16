from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from src.infrastructure.db.models import OutboxEvent


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ClaimedEvent:
    id: str
    tenant_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    attempts: int


def claim_events(session: Session, worker_id: str, limit: int = 50, lock_timeout_seconds: int = 60) -> list[ClaimedEvent]:
    now = _utcnow()
    stale_before = now - timedelta(seconds=lock_timeout_seconds)

    with session.begin():
        q = (
            select(OutboxEvent)
            .where(
                OutboxEvent.status == "PENDING",
                OutboxEvent.available_at <= now,
                or_(OutboxEvent.locked_at.is_(None), OutboxEvent.locked_at < stale_before),
            )
            .order_by(OutboxEvent.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        rows = session.execute(q).scalars().all()
        for e in rows:
            e.locked_at = now
            e.locked_by = worker_id
        # flush within transaction
    return [
        ClaimedEvent(
            id=str(e.id),
            tenant_id=e.tenant_id,
            event_type=e.event_type,
            aggregate_type=e.aggregate_type,
            aggregate_id=e.aggregate_id,
            payload=e.payload,
            attempts=e.attempts,
        )
        for e in rows
    ]


def mark_sent(session: Session, event_id: str) -> None:
    with session.begin():
        e = session.get(OutboxEvent, event_id)
        if not e:
            return
        e.status = "SENT"
        e.locked_at = None
        e.locked_by = None


def mark_failed(session: Session, event_id: str, max_attempts: int = 7) -> None:
    with session.begin():
        e = session.get(OutboxEvent, event_id)
        if not e:
            return
        e.attempts += 1
        e.locked_at = None
        e.locked_by = None
        if e.attempts >= max_attempts:
            e.status = "DEAD"
            return
        # exponential backoff with jitter
        base = min(60, 2 ** min(6, e.attempts))
        jitter = random.uniform(0, 1.0)
        e.available_at = _utcnow() + timedelta(seconds=base + jitter)
