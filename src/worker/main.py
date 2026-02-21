from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any


from src.application.outbox import claim_events, mark_failed, mark_sent
from src.infrastructure.db.session import init_db, session_scope
from src.infrastructure.mq.rabbit import Rabbit, RabbitConfig
from src.shared.config import load_settings
from src.shared.correlation import set_correlation_id, set_subject, set_tenant_id
from src.shared.logging import configure_logging, get_logger
from src.shared.metrics import OUTBOX_FAILED_TOTAL, OUTBOX_PUBLISHED_TOTAL
from src.worker.handlers.payments import handle_event

log = get_logger(__name__)


def _worker_id() -> str:
    return os.getenv("HOSTNAME") or f"worker-{uuid.uuid4().hex[:8]}"


def dispatch_loop(rabbit: Rabbit, worker_id: str) -> None:
    log.info("outbox dispatcher started", extra={"worker_id": worker_id})
    while True:
        try:
            with session_scope() as session:
                events = claim_events(session, worker_id, limit=50)
                for e in events:
                    try:
                        headers = {
                            "X-Correlation-Id": e.payload.get("correlation_id", ""),
                            "X-Tenant-Id": e.tenant_id,
                        }
                        message = dict(e.payload)
                        message["tenant_id"] = e.tenant_id
                        rabbit.publish(e.event_type, message, headers=headers)
                        OUTBOX_PUBLISHED_TOTAL.labels(e.event_type).inc()
                        mark_sent(session, e.id)
                    except Exception:
                        OUTBOX_FAILED_TOTAL.labels(e.event_type).inc()
                        log.exception(
                            "publish failed", extra={"event_id": e.id, "event_type": e.event_type}
                        )
                        mark_failed(session, e.id)
        except Exception:
            log.exception("dispatcher loop error")
        time.sleep(1.0)


def consume_loop(rabbit: Rabbit) -> None:
    def handler(routing_key: str, payload: dict[str, Any], headers: dict[str, Any]) -> None:
        cid = str(
            headers.get("X-Correlation-Id") or payload.get("correlation_id") or uuid.uuid4().hex
        )
        tenant_id = str(headers.get("X-Tenant-Id") or payload.get("tenant_id") or "")
        set_correlation_id(cid)
        set_tenant_id(tenant_id)
        set_subject("worker")
        with session_scope() as session:
            handle_event(session, routing_key, payload)

    rabbit.consume(handler, prefetch=10)


def main() -> None:
    settings = load_settings()
    configure_logging("INFO")
    init_db(settings)

    cfg = RabbitConfig(url=settings.rabbitmq_url)
    rabbit_dispatch = Rabbit(cfg)
    rabbit_consume = Rabbit(cfg)
    rabbit_dispatch.connect()
    rabbit_consume.connect()

    worker_id = _worker_id()
    t = threading.Thread(target=dispatch_loop, args=(rabbit_dispatch, worker_id), daemon=True)
    t.start()

    try:
        consume_loop(rabbit_consume)
    finally:
        rabbit_dispatch.close()
        rabbit_consume.close()


if __name__ == "__main__":
    main()
