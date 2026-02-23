from __future__ import annotations

from fastapi import APIRouter, Response

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from src.application.outbox import count_pending
from src.infrastructure.db.session import session_scope
from src.shared.metrics import OUTBOX_PENDING_GAUGE

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def metrics():
    try:
        with session_scope() as session:
            pending = count_pending(session)
        OUTBOX_PENDING_GAUGE.set(pending)
    except Exception:
        pass
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
