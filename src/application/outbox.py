from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.infrastructure.db.models import OutboxEvent
from src.infrastructure.db.session import safe_begin
from src.shared.logging import get_logger

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ClaimedEvent:
    id: str
    tenant_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    attempts: int
    #: Quem reservou o evento. Vai junto para que a conclusao possa ser
    #: condicionada a posse ainda estar valida.
    locked_by: str = ""


def claim_events(
    session: Session, worker_id: str, limit: int = 50, lock_timeout_seconds: int = 60
) -> list[ClaimedEvent]:
    now = _utcnow()
    stale_before = now - timedelta(seconds=lock_timeout_seconds)

    with safe_begin(session):
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
    return [
        ClaimedEvent(
            id=str(e.id),
            tenant_id=str(e.tenant_id),
            event_type=e.event_type,
            aggregate_type=e.aggregate_type,
            aggregate_id=e.aggregate_id,
            payload=e.payload,
            attempts=e.attempts,
            locked_by=e.locked_by or "",
        )
        for e in rows
    ]


def mark_sent(session: Session, event_id: str, worker_id: str | None = None) -> bool:
    """Conclui o evento, condicionado a posse do lease.

    Sem ``worker_id`` a conclusao e incondicional, como antes — e por isso o
    parametro tem default: nao quebra chamadores existentes. Com ele, a escrita
    so se aplica se o lease ainda for deste worker.

    Isso fecha uma janela real: o lease expira por ``lock_timeout_seconds``,
    outro worker reivindica o evento e passa a publica-lo; se o worker original
    (lento, nao morto) voltasse e chamasse ``mark_sent``, marcaria como enviado
    um evento que o segundo worker ainda esta processando. Devolve ``False``
    quando a posse foi perdida, e nesse caso o chamador deve descartar o
    trabalho em vez de concluir.
    """
    with safe_begin(session):
        e = session.get(OutboxEvent, event_id)
        if not e:
            return False
        if worker_id is not None and e.locked_by != worker_id:
            log.warning(
                "refusing to complete an outbox event owned by another worker",
                extra={
                    "event_id": str(event_id),
                    "owner": e.locked_by or "",
                    "caller": worker_id,
                },
            )
            return False
        e.status = "SENT"
        e.locked_at = None
        e.locked_by = None
        return True


def count_pending(session: Session) -> int:
    from sqlalchemy import func

    row = session.execute(
        select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == "PENDING")
    ).scalar()
    return int(row) if row is not None else 0


def mark_failed(
    session: Session, event_id: str, max_attempts: int = 7, worker_id: str | None = None
) -> bool:
    """Registra falha e reagenda, condicionado a posse do lease."""
    with safe_begin(session):
        e = session.get(OutboxEvent, event_id)
        if not e:
            return False
        if worker_id is not None and e.locked_by != worker_id:
            log.warning(
                "refusing to fail an outbox event owned by another worker",
                extra={
                    "event_id": str(event_id),
                    "owner": e.locked_by or "",
                    "caller": worker_id,
                },
            )
            return False
        e.attempts += 1
        e.locked_at = None
        e.locked_by = None
        if e.attempts >= max_attempts:
            e.status = "DEAD"
            return True
        base = min(60, 2 ** min(6, e.attempts))
        jitter = random.uniform(0, 1.0)
        e.available_at = _utcnow() + timedelta(seconds=base + jitter)
        return True
