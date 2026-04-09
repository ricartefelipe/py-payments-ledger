from __future__ import annotations

import asyncio
import json
import os
import pathlib
import signal
import sys
import threading
import time
import types
import uuid
from typing import Any

import httpx

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from src.application.ports.payment_gateway import PaymentGatewayPort
from src.application.outbox import claim_events, mark_failed, mark_sent
from src.application.recurring import process_due_charges
from src.application.reconciliation import reconcile_transactions
from src.application.webhooks import (
    claim_pending_deliveries,
    compute_signature,
    mark_delivery_failed,
    mark_delivery_success,
)
from src.infrastructure.db.models import AuditLog, Tenant
from src.infrastructure.db.session import get_engine, init_db, session_scope
from src.infrastructure.gateway.factory import create_gateway
from src.infrastructure.mq.rabbit import Rabbit, RabbitConfig
from src.shared.config import Settings, load_settings
from src.shared.correlation import set_correlation_id, set_subject, set_tenant_id
from src.shared.logging import configure_logging, get_logger
from src.shared.metrics import OUTBOX_FAILED_TOTAL, OUTBOX_PUBLISHED_TOTAL
from src.worker.handlers.payments import handle_event, set_gateway, set_settings
from src.worker.handlers.tenants import handle_tenant_event

log = get_logger(__name__)

_shutdown_event = threading.Event()
_HEARTBEAT_PATH = pathlib.Path("/tmp/worker-heartbeat")


def _worker_id() -> str:
    return os.getenv("HOSTNAME") or f"worker-{uuid.uuid4().hex[:8]}"


def _set_context(headers: dict[str, Any], payload: dict[str, Any]) -> None:
    cid = str(headers.get("X-Correlation-Id") or payload.get("correlation_id") or uuid.uuid4().hex)
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


def webhook_delivery_loop() -> None:
    log.info("webhook delivery loop started")
    client = httpx.Client(timeout=10.0)
    try:
        while not _shutdown_event.is_set():
            try:
                with session_scope() as session:
                    deliveries = claim_pending_deliveries(session, limit=50)

                for delivery in deliveries:
                    body = {
                        "event_type": delivery.event_type,
                        "payload": delivery.payload,
                    }
                    payload_bytes = json.dumps(body, default=str).encode()
                    signature = compute_signature(delivery.endpoint.secret, payload_bytes)

                    try:
                        resp = client.post(
                            delivery.endpoint.url,
                            content=payload_bytes,
                            headers={
                                "Content-Type": "application/json",
                                "X-Webhook-Signature": signature,
                            },
                        )
                        if 200 <= resp.status_code < 300:
                            with session_scope() as session:
                                mark_delivery_success(session, delivery.id, resp.status_code)
                        else:
                            with session_scope() as session:
                                mark_delivery_failed(session, delivery.id, resp.status_code)
                    except Exception:
                        log.exception(
                            "webhook delivery http error",
                            extra={"delivery_id": str(delivery.id), "url": delivery.endpoint.url},
                        )
                        with session_scope() as session:
                            mark_delivery_failed(session, delivery.id, None)
            except Exception:
                log.exception("webhook delivery loop error")
            _shutdown_event.wait(5.0)
    finally:
        client.close()


def reconciliation_loop(settings: Settings, gateway: PaymentGatewayPort) -> None:
    interval = settings.reconciliation_interval_minutes * 60
    log.info(
        "reconciliation loop started",
        extra={"interval_minutes": settings.reconciliation_interval_minutes},
    )
    while not _shutdown_event.is_set():
        try:
            created_after = int(time.time()) - (interval * 2)
            with session_scope() as session:
                tenants = session.execute(select(Tenant)).scalars().all()
                for tenant in tenants:
                    try:
                        gateway_transactions = asyncio.run(
                            gateway.list_payment_intents(created_after=created_after, limit=100)
                        )
                    except Exception:
                        log.exception(
                            "reconciliation: failed to fetch gateway transactions",
                            extra={"tenant_id": tenant.id},
                        )
                        gateway_transactions = []

                    if gateway_transactions:
                        tenant_txns = [
                            tx
                            for tx in gateway_transactions
                            if tx.get("metadata", {}).get("tenant_id") == tenant.id
                        ]
                        if not tenant_txns:
                            log.debug(
                                "reconciliation skipped: no transactions for tenant",
                                extra={
                                    "tenant_id": tenant.id,
                                    "total_fetched": len(gateway_transactions),
                                },
                            )
                            continue
                        discrepancies = reconcile_transactions(
                            session,
                            tenant.id,
                            tenant_txns,
                            auto_fix=True,
                        )
                        log.info(
                            "reconciliation completed",
                            extra={
                                "tenant_id": tenant.id,
                                "transactions_checked": len(tenant_txns),
                                "discrepancy_count": len(discrepancies),
                            },
                        )
                    else:
                        log.debug(
                            "reconciliation skipped: no gateway transactions",
                            extra={"tenant_id": tenant.id},
                        )
        except Exception:
            log.exception("reconciliation loop error")
        _shutdown_event.wait(interval)


def recurring_charge_loop(gateway: PaymentGatewayPort) -> None:
    interval = 60
    log.info("recurring charge loop started", extra={"interval_seconds": interval})
    while not _shutdown_event.is_set():
        try:
            with session_scope() as session:
                processed = process_due_charges(session, gateway=gateway)
                if processed:
                    log.info("recurring charges processed", extra={"count": processed})
        except Exception:
            log.exception("recurring charge loop error")
        _shutdown_event.wait(interval)


def audit_retention_loop(settings: Settings) -> None:
    retention_days = settings.audit_retention_days
    interval = 24 * 60 * 60  # once per day
    batch_size = 1000
    log.info(
        "audit retention loop started",
        extra={"retention_days": retention_days},
    )
    while not _shutdown_event.is_set():
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            total_purged = 0
            while True:
                with session_scope() as session:
                    subq = select(AuditLog.id).where(AuditLog.created_at < cutoff).limit(batch_size)
                    result = session.execute(delete(AuditLog).where(AuditLog.id.in_(subq)))
                    deleted = result.rowcount  # type: ignore[attr-defined]
                    session.commit()
                total_purged += deleted
                if deleted < batch_size:
                    break
            if total_purged > 0:
                log.info(
                    "audit retention purge completed",
                    extra={"purged": total_purged, "cutoff": cutoff.isoformat()},
                )
        except Exception:
            log.exception("audit retention loop error")
        _shutdown_event.wait(interval)


def main() -> None:
    settings = load_settings()
    configure_logging("INFO")
    init_db(settings)

    gateway = create_gateway(settings)
    set_gateway(gateway)
    set_settings(settings)
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

    wh = threading.Thread(target=webhook_delivery_loop, daemon=True)
    wh.start()

    if settings.reconciliation_enabled:
        rt = threading.Thread(target=reconciliation_loop, args=(settings, gateway), daemon=True)
        rt.start()
    else:
        log.info("reconciliation loop disabled (RECONCILIATION_ENABLED=false)")

    rc = threading.Thread(target=recurring_charge_loop, args=(gateway,), daemon=True)
    rc.start()

    if settings.audit_retention_days > 0:
        ar = threading.Thread(target=audit_retention_loop, args=(settings,), daemon=True)
        ar.start()
    else:
        log.info("audit retention loop disabled (AUDIT_RETENTION_DAYS <= 0)")

    rabbit_orders = _start_orders_consumer(settings)
    rabbit_saas = _start_saas_consumer(settings)

    def _handle_signal(signum: int, _frame: types.FrameType | None) -> None:
        log.info("received shutdown signal", extra={"signal": signum})
        _shutdown_event.set()
        try:
            ch = rabbit_consume._ch
            conn = rabbit_consume._conn
            if ch and conn and conn.is_open:
                conn.add_callback_threadsafe(ch.stop_consuming)
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
