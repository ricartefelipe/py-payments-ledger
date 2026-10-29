from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.exc import SQLAlchemyError

from src.application.outbox import count_pending
from src.infrastructure.db.session import session_scope
from src.shared.logging import get_logger
from src.shared.metrics import OUTBOX_PENDING_GAUGE

log = get_logger(__name__)

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def metrics():
    try:
        with session_scope() as session:
            pending = count_pending(session)
        OUTBOX_PENDING_GAUGE.set(pending)
    except SQLAlchemyError:
        # O gauge fica com o valor anterior; /metrics continua servindo o resto.
        log.warning("failed to refresh outbox pending gauge", exc_info=True)
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
