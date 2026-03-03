from __future__ import annotations

import os
import pathlib
import signal
import sys
import threading
import time
import uuid
from typing import Any

from src.application.outbox import claim_events, mark_failed, mark_sent
from src.infrastructure.db.session import get_engine, init_db, session_scope
from src.infrastructure.gateway.factory import create_gateway
from src.infrastructure.mq.rabbit import Rabbit, RabbitConfig
from src.shared.config import Settings, load_settings
from src.shared.correlation import set_correlation_id, set_subject, set_tenant_id
from src.shared.logging import configure_logging, get_logger
from src.shared.metrics import OUTBOX_FAILED_TOTAL, OUTBOX_PUBLISHED_TOTAL
from src.worker.handlers.payments import handle_event, set_gateway
from src.worker.handlers.tenants import handle_tenant_event

log = get_logger(__name__)

_shutdown_event = threading.Event()
_HEARTBEAT_PATH = pathlib.Path("/tmp/worker-heartbeat")


def _worker_id() -> str:
    return os.getenv("HOSTNAME") or f"worker-{uuid.uuid4().hex[:8]}"


def _set_context(headers: dict[str, Any], payload: dict[str, Any]) -> None:
    cid = str(
        headers.get("X-Correlation-Id") or payload.get("correlation_id") or uuid.uuid4().hex
    )
    tenant_id = str(headers.get("X-Tenant-Id") or payload.get("tenant_id") or "")
    set_correlation_id(cid)
    set_tenant_id(tenant_id)
    set_subject("worker")


def dispatch_loop(rabbit: Rabbit, worker_id: str) -> None:
    log.info("outbox dispatcher started", extra={"worker_id": worker_id})
    while not _shutdown_event.is_set():
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
                            "publish failed",
                            extra={"event_id": e.id, "event_type": e.event_type},
                        )
                        mark_failed(session, e.id)
        except Exception:
            log.exception("dispatcher loop error")
        _shutdown_event.wait(1.0)


def consume_loop(rabbit: Rabbit, queue: str | None = None) -> None:
    def handler(routing_key: str, payload: dict[str, Any], headers: dict[str, Any]) -> None:
        _set_context(headers, payload)
        with session_scope() as session:
            handle_event(session, routing_key, payload)

    rabbit.consume(handler, prefetch=10, queue=queue)


def _start_orders_consumer(settings: Settings) -> Rabbit | None:
    if not settings.orders_integration_enabled:
        return None

    log.info(
        "orders integration enabled",
        extra={
            "exchange": settings.orders_exchange,
            "queue": settings.orders_queue,
            "routing_keys": settings.orders_routing_keys,
        },
    )

    cfg = RabbitConfig(url=settings.rabbitmq_url)
    rabbit_orders = Rabbit(cfg)
    rabbit_orders.connect()
    rabbit_orders.declare_external_queue_multi_bind(
        exchange=settings.orders_exchange,
        queue=settings.orders_queue,
        routing_keys=settings.orders_routing_keys,
    )

    t = threading.Thread(
        target=consume_loop,
        args=(rabbit_orders, settings.orders_queue),
        daemon=True,
    )
    t.start()
    return rabbit_orders


def _consume_tenant_loop(rabbit: Rabbit, queue: str) -> None:
    def handler(routing_key: str, payload: dict[str, Any], headers: dict[str, Any]) -> None:
        _set_context(headers, payload)
        with session_scope() as session:
            handle_tenant_event(session, routing_key, payload)

    rabbit.consume(handler, prefetch=10, queue=queue)


def _start_saas_consumer(settings: Settings) -> Rabbit | None:
    if not settings.saas_integration_enabled:
        return None

    log.info(
        "saas integration enabled",
        extra={
            "exchange": settings.saas_exchange,
            "queue": settings.saas_queue,
            "routing_keys": settings.saas_routing_keys,
        },
    )

    cfg = RabbitConfig(url=settings.rabbitmq_url)
    rabbit_saas = Rabbit(cfg)
    rabbit_saas.connect()
    rabbit_saas.declare_external_queue_multi_bind(
        exchange=settings.saas_exchange,
        queue=settings.saas_queue,
        routing_keys=settings.saas_routing_keys,
    )

    t = threading.Thread(
        target=_consume_tenant_loop,
        args=(rabbit_saas, settings.saas_queue),
        daemon=True,
    )
    t.start()
    return rabbit_saas


def _heartbeat_loop() -> None:
    while not _shutdown_event.is_set():
        try:
            _HEARTBEAT_PATH.write_text(str(time.time()))
        except OSError:
            pass
        _shutdown_event.wait(30.0)


def main() -> None:
    settings = load_settings()
    configure_logging("INFO")
    init_db(settings)

    gateway = create_gateway(settings)
    set_gateway(gateway)
    log.info("payment gateway initialized", extra={"provider": type(gateway).__name__})

    cfg = RabbitConfig(url=settings.rabbitmq_url)
    rabbit_dispatch = Rabbit(cfg)
    rabbit_consume = Rabbit(cfg)
    rabbit_dispatch.connect()
    rabbit_consume.connect()

    worker_id = _worker_id()
    t = threading.Thread(target=dispatch_loop, args=(rabbit_dispatch, worker_id), daemon=True)
    t.start()

    hb = threading.Thread(target=_heartbeat_loop, daemon=True)
    hb.start()

    rabbit_orders = _start_orders_consumer(settings)
    rabbit_saas = _start_saas_consumer(settings)

    def _handle_signal(signum: int, _frame: Any) -> None:
        log.info("received shutdown signal", extra={"signal": signum})
        _shutdown_event.set()
        try:
            if rabbit_consume._ch and rabbit_consume._conn and rabbit_consume._conn.is_open:
                rabbit_consume._conn.add_callback_threadsafe(
                    rabbit_consume._ch.stop_consuming
                )
        except Exception:
            pass

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        consume_loop(rabbit_consume)
    finally:
        log.info("worker shutting down")
        rabbit_dispatch.close()
        rabbit_consume.close()
        if rabbit_orders:
            rabbit_orders.close()
        if rabbit_saas:
            rabbit_saas.close()
        try:
            get_engine().dispose()
        except Exception:
            pass
        log.info("worker shutdown complete")
        sys.exit(0)


if __name__ == "__main__":
    main()
