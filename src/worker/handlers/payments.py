from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application.payments import post_ledger_for_authorized_payment
from src.application.ports.payment_gateway import PaymentGatewayPort
from src.infrastructure.db.models import OutboxEvent, PaymentIntent
from src.infrastructure.db.session import safe_begin
from src.shared.config import Settings
from src.shared.correlation import get_correlation_id, set_correlation_id
from src.shared.logging import get_logger
from src.worker.handlers.charge_request import parse_charge_payload

log = get_logger(__name__)

_gateway: PaymentGatewayPort | None = None
_settings: Settings | None = None


def set_gateway(gateway: PaymentGatewayPort) -> None:
    global _gateway
    _gateway = gateway


def set_settings(settings: Settings) -> None:
    global _settings
    _settings = settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _emit_retry_exhausted(
    session: Session,
    *,
    tenant_id: str,
    order_id: str,
    amount: Decimal,
    currency: str,
    error_code: str,
    error_message: str,
    correlation_id: str,
) -> None:
    """Emit payment.retry_exhausted event (outbox + webhook + metric + log)."""
    from src.application.webhooks import enqueue_webhook_deliveries
    from src.shared.metrics import PAYMENT_RETRY_EXHAUSTED_TOTAL

    payload = {
        "tenant_id": tenant_id,
        "order_id": order_id,
        "amount": str(amount),
        "currency": currency,
        "error_code": error_code,
        "error_message": error_message,
        "correlation_id": correlation_id,
    }
    session.add(
        OutboxEvent(
            tenant_id=tenant_id,
            event_type="payment.retry_exhausted",
            aggregate_type="ChargeRequest",
            aggregate_id=order_id,
            payload=payload,
        )
    )
    enqueue_webhook_deliveries(session, tenant_id, "payment.retry_exhausted", payload)
    PAYMENT_RETRY_EXHAUSTED_TOTAL.labels(tenant_id=tenant_id).inc()
    log.error(
        "payment retry exhausted",
        extra={
            "tenant_id": tenant_id,
            "order_id": order_id,
            "amount": str(amount),
            "currency": currency,
            "error_code": error_code,
            "error_message": error_message,
            "correlation_id": correlation_id,
        },
    )


def handle_event(session: Session, routing_key: str, payload: dict[str, Any]) -> None:
    if routing_key == "payment.authorized":
        pid_raw = payload.get("payment_intent_id") or payload.get("paymentIntentId")
        pid = uuid.UUID(str(pid_raw))
        tenant_id = str(payload.get("tenant_id") or payload.get("tenantId") or "")
        gateway = _gateway
        if _settings and tenant_id:
            from src.infrastructure.gateway.factory import get_gateway_for_tenant

            pi = session.get(PaymentIntent, pid)
            provider = pi.gateway_provider if pi else None
            gateway = get_gateway_for_tenant(session, tenant_id, _settings, provider=provider)
        post_ledger_for_authorized_payment(session, tenant_id, pid, gateway=gateway)
        log.info(
            "ledger posted",
            extra={
                "payment_intent_id": str(pid),
                "tenant_id": tenant_id,
                "correlation_id": get_correlation_id(),
            },
        )

    elif routing_key in ("payment.charge_requested", "order.confirmed"):
        handle_charge_request(session, payload)

    elif routing_key == "payment.settled":
        # Publicado pelo outbox para payments.x; o worker de orders é que consome em orders.payments.
        # Esta fila (payments.events) também faz bind em # — ack sem efeito para não duplicar lógica.
        log.debug(
            "payment.settled ignored on payments worker (consumed by node-b2b-orders)",
            extra={"routing_key": routing_key},
        )


def handle_charge_request(session: Session, payload: dict[str, Any]) -> None:
    parsed = parse_charge_payload(payload)
    order_id = parsed["order_id"]
    tenant_id = parsed["tenant_id"]

    if not order_id or not tenant_id:
        log.warning(
            "charge request missing order_id or tenant_id",
            extra={"payload_keys": list(payload.keys()), "parsed": parsed},
        )
        return

    set_correlation_id(parsed["correlation_id"] or get_correlation_id())
    amount = Decimal(parsed["total_amount"])
    currency = parsed["currency"]
    customer_ref = parsed["customer_ref"] or f"order:{order_id}"
    gateway_hint = parsed.get("gateway")
    payment_type = parsed.get("payment_type")

    with safe_begin(session):
        existing = session.execute(
            select(PaymentIntent).where(
                PaymentIntent.tenant_id == tenant_id,
                PaymentIntent.customer_ref == f"order:{order_id}",
            )
        ).scalar_one_or_none()
        if existing:
            log.info(
                "order already processed",
                extra={
                    "order_id": order_id,
                    "payment_intent_id": str(existing.id),
                    "correlation_id": parsed["correlation_id"],
                },
            )
            return

        gateway_ref = ""
        gateway_provider: str | None = gateway_hint
        gateway = _gateway
        if _settings and tenant_id:
            from src.infrastructure.db.models import GatewayConfig
            from src.infrastructure.gateway.factory import get_gateway_for_tenant

            gateway = get_gateway_for_tenant(
                session,
                tenant_id,
                _settings,
                provider=gateway_hint,
                currency=currency,
                payment_type=payment_type,
            )
            if not gateway_provider:
                default_cfg = session.execute(
                    select(GatewayConfig).where(
                        GatewayConfig.tenant_id == tenant_id,
                        GatewayConfig.is_default.is_(True),
                    )
                ).scalar_one_or_none()
                if default_cfg:
                    gateway_provider = default_cfg.provider

        if gateway is not None:
            idempotency_key = f"charge:{tenant_id}:{order_id}"
            max_retries = getattr(_settings, "charge_request_max_retries", 3) if _settings else 3
            last_result = None
            for attempt in range(max_retries + 1):
                result = asyncio.run(
                    gateway.authorize(tenant_id, amount, currency, customer_ref, idempotency_key)
                )
                last_result = result
                if result.success:
                    gateway_ref = result.gateway_ref
                    log.info(
                        "gateway authorize succeeded",
                        extra={"order_id": order_id, "gateway_ref": gateway_ref},
                    )
                    break
                if not result.is_retryable or attempt >= max_retries:
                    break
                delay = min(2**attempt, 30)
                time.sleep(delay)

            if not last_result or not last_result.success:
                _emit_retry_exhausted(
                    session,
                    tenant_id=tenant_id,
                    order_id=order_id,
                    amount=amount,
                    currency=currency,
                    error_code=last_result.error_code if last_result else "unknown",
                    error_message=last_result.error_message if last_result else "no result",
                    correlation_id=parsed["correlation_id"] or get_correlation_id(),
                )
                return

        now = _utcnow()
        pi = PaymentIntent(
            tenant_id=tenant_id,
            amount=amount,
            currency=currency,
            status="AUTHORIZED",
            customer_ref=f"order:{order_id}",
            gateway_ref=gateway_ref or None,
            gateway_provider=gateway_provider,
            created_at=now,
            updated_at=now,
        )
        session.add(pi)
        session.flush()

        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="payment.authorized",
                aggregate_type="PaymentIntent",
                aggregate_id=str(pi.id),
                payload={
                    "payment_intent_id": str(pi.id),
                    "amount": str(amount),
                    "currency": currency,
                    "order_id": order_id,
                    "customer_ref": pi.customer_ref,
                    "gateway_ref": gateway_ref,
                    "gateway_provider": gateway_provider,
                    "correlation_id": parsed["correlation_id"] or get_correlation_id(),
                },
            )
        )

    log.info(
        "payment intent created from charge request",
        extra={
            "order_id": order_id,
            "payment_intent_id": str(pi.id),
            "tenant_id": tenant_id,
            "gateway_ref": gateway_ref,
            "correlation_id": parsed["correlation_id"],
        },
    )
