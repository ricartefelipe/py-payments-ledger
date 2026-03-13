from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application.security import _audit
from src.application.webhooks import enqueue_webhook_deliveries
from src.infrastructure.db.models import (
    AccountConfig,
    LedgerEntry,
    LedgerLine,
    OutboxEvent,
    PaymentIntent,
)
from src.infrastructure.db.session import safe_begin
from src.shared.metrics import (
    PAYMENT_INTENTS_CONFIRMED_TOTAL,
    PAYMENT_INTENTS_CREATED_TOTAL,
    PAYMENT_INTENTS_VOIDED_TOTAL,
)
from src.application.event_broadcaster import broadcaster
from src.shared.problem import http_problem
from src.shared.correlation import get_correlation_id, get_subject
from src.shared.logging import get_logger

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaymentIntentDTO(BaseModel):
    id: str
    amount: str
    currency: str
    status: str
    customer_ref: str
    gateway_ref: str | None = None
    created_at: str
    updated_at: str


def _resolve_account(session: Session, tenant_id: str, code: str) -> str:
    cfg = session.execute(
        select(AccountConfig).where(
            AccountConfig.tenant_id == tenant_id,
            AccountConfig.code == code,
        )
    ).scalar_one_or_none()
    return cfg.code if cfg else code


def create_payment_intent(
    session: Session,
    tenant_id: str,
    amount: float,
    currency: str,
    customer_ref: str,
    gateway: Any = None,
    idempotency_key: str | None = None,
    gateway_provider: str | None = None,
    payment_type: str | None = None,
    settings: Any = None,
    payment_method_id: str | None = None,
) -> PaymentIntentDTO:
    if amount <= 0:
        raise http_problem(400, "Bad Request", "amount must be > 0", instance="/v1/payment-intents")

    SUPPORTED_CURRENCIES = {"BRL", "USD", "EUR", "GBP", "ARS", "CLP", "MXN", "COP", "PEN", "UYU"}
    if currency not in SUPPORTED_CURRENCIES:
        raise http_problem(
            400, "Bad Request", f"unsupported currency: {currency}", instance="/v1/payment-intents"
        )

    gateway_ref: str | None = None
    gw = gateway
    if settings and tenant_id and (gateway_provider or payment_type):
        from src.infrastructure.gateway.factory import get_gateway_for_tenant

        gw = get_gateway_for_tenant(
            session,
            tenant_id,
            settings,
            provider=gateway_provider,
            currency=currency,
            payment_type=payment_type,
        )
    elif gateway:
        gw = gateway

    if settings and tenant_id:
        from sqlalchemy import select as sa_select
        from src.infrastructure.db.models import GatewayConfig

        gw_config = session.execute(
            sa_select(GatewayConfig).where(
                GatewayConfig.tenant_id == tenant_id,
                GatewayConfig.is_default.is_(True),
            )
        ).scalar_one_or_none()
        if (
            gw_config
            and gw_config.supported_currencies
            and currency not in gw_config.supported_currencies
        ):
            has_alternative = session.execute(
                sa_select(GatewayConfig).where(
                    GatewayConfig.tenant_id == tenant_id,
                    GatewayConfig.supported_currencies.any(currency),
                )
            ).scalar_one_or_none()
            if not has_alternative:
                raise http_problem(
                    422,
                    "Unprocessable Entity",
                    f"no gateway configured for currency {currency}",
                    instance="/v1/payment-intents",
                )

    payment_method_token = ""
    if payment_method_id:
        from src.infrastructure.db.models import SavedPaymentMethod

        spm = session.execute(
            select(SavedPaymentMethod).where(
                SavedPaymentMethod.id == uuid.UUID(payment_method_id),
                SavedPaymentMethod.tenant_id == tenant_id,
                SavedPaymentMethod.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if spm:
            payment_method_token = spm.gateway_token

    if gw and idempotency_key:
        import asyncio

        gw_result = asyncio.run(
            gw.authorize(
                tenant_id,
                Decimal(str(amount)),
                currency,
                customer_ref,
                idempotency_key,
                payment_method_token=payment_method_token,
            )
        )
        if not gw_result.success:
            log.error(
                "gateway authorize failed",
                extra={
                    "error_code": gw_result.error_code,
                    "error_message": gw_result.error_message,
                },
            )
            raise http_problem(
                502,
                "Bad Gateway",
                f"gateway error: {gw_result.error_message}",
                instance="/v1/payment-intents",
            )
        gateway_ref = gw_result.gateway_ref

    with safe_begin(session):
        pi = PaymentIntent(
            tenant_id=tenant_id,
            amount=amount,
            currency=currency,
            status="CREATED",
            customer_ref=customer_ref,
            gateway_ref=gateway_ref,
            gateway_provider=gateway_provider,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(pi)
        session.flush()

        event_payload = {
            "payment_intent_id": str(pi.id),
            "amount": str(amount),
            "currency": currency,
            "customer_ref": customer_ref,
            "gateway_ref": gateway_ref or "",
            "correlation_id": get_correlation_id(),
        }
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="payment.intent.created",
                aggregate_type="PaymentIntent",
                aggregate_id=str(pi.id),
                payload=event_payload,
            )
        )
        enqueue_webhook_deliveries(session, tenant_id, "payment.intent.created", event_payload)

    PAYMENT_INTENTS_CREATED_TOTAL.labels(tenant_id).inc()

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "payment_intent.created",
        f"payment_intent:{pi.id}",
        {"amount": str(amount), "currency": currency, "customer_ref": customer_ref},
    )

    broadcaster.broadcast_sync(
        tenant_id,
        "payment.status",
        {
            "paymentIntentId": str(pi.id),
            "status": "CREATED",
            "action": "created",
            "amount": str(amount),
            "currency": currency,
        },
    )

    return _to_dto(pi)


def _to_dto(pi: PaymentIntent) -> PaymentIntentDTO:
    return PaymentIntentDTO(
        id=str(pi.id),
        amount=str(pi.amount),
        currency=pi.currency,
        status=pi.status,
        customer_ref=pi.customer_ref,
        gateway_ref=pi.gateway_ref,
        created_at=pi.created_at.isoformat(),
        updated_at=pi.updated_at.isoformat(),
    )


def list_payment_intents(
    session: Session,
    tenant_id: str,
    status: str | None = None,
    customer_ref: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[PaymentIntentDTO], int]:
    q = select(PaymentIntent).where(PaymentIntent.tenant_id == tenant_id)
    if status:
        q = q.where(PaymentIntent.status == status)
    if customer_ref:
        q = q.where(PaymentIntent.customer_ref.ilike(f"%{customer_ref}%"))

    from sqlalchemy import func

    count_q = select(func.count()).select_from(q.subquery())
    total = session.execute(count_q).scalar() or 0

    q = q.order_by(PaymentIntent.created_at.desc())
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = session.execute(q).scalars().all()
    return [_to_dto(r) for r in rows], total


def get_payment_intent(session: Session, tenant_id: str, pid: uuid.UUID) -> PaymentIntentDTO:
    pi = session.execute(
        select(PaymentIntent).where(PaymentIntent.tenant_id == tenant_id, PaymentIntent.id == pid)
    ).scalar_one_or_none()
    if not pi:
        raise http_problem(
            404, "Not Found", "payment intent not found", instance=f"/v1/payment-intents/{pid}"
        )
    return _to_dto(pi)


def confirm_payment_intent(
    session: Session,
    tenant_id: str,
    pid: uuid.UUID,
    gateway: Any = None,
    idempotency_key: str | None = None,
) -> PaymentIntentDTO:
    with safe_begin(session):
        pi = session.execute(
            select(PaymentIntent)
            .where(PaymentIntent.tenant_id == tenant_id, PaymentIntent.id == pid)
            .with_for_update()
        ).scalar_one_or_none()
        if not pi:
            raise http_problem(
                404,
                "Not Found",
                "payment intent not found",
                instance=f"/v1/payment-intents/{pid}/confirm",
            )
        if pi.status in ("SETTLED", "FAILED"):
            return _to_dto(pi)
        if pi.status != "CREATED":
            raise http_problem(
                409,
                "Conflict",
                f"cannot confirm status {pi.status}",
                instance=f"/v1/payment-intents/{pid}/confirm",
            )

        if gateway and idempotency_key and not pi.gateway_ref:
            import asyncio

            gw_result = asyncio.run(
                gateway.authorize(
                    tenant_id,
                    Decimal(str(pi.amount)),
                    pi.currency,
                    pi.customer_ref,
                    idempotency_key,
                )
            )
            if not gw_result.success:
                log.error(
                    "gateway authorize on confirm failed",
                    extra={
                        "error_code": gw_result.error_code,
                        "error_message": gw_result.error_message,
                    },
                )
                raise http_problem(
                    502,
                    "Bad Gateway",
                    f"gateway error: {gw_result.error_message}",
                    instance=f"/v1/payment-intents/{pid}/confirm",
                )
            pi.gateway_ref = gw_result.gateway_ref

        pi.status = "AUTHORIZED"
        pi.updated_at = _utcnow()

        event_payload = {
            "payment_intent_id": str(pi.id),
            "amount": str(pi.amount),
            "currency": pi.currency,
            "gateway_ref": pi.gateway_ref or "",
            "correlation_id": get_correlation_id(),
        }
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="payment.authorized",
                aggregate_type="PaymentIntent",
                aggregate_id=str(pi.id),
                payload=event_payload,
            )
        )
        enqueue_webhook_deliveries(session, tenant_id, "payment.authorized", event_payload)

    PAYMENT_INTENTS_CONFIRMED_TOTAL.labels(tenant_id).inc()

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "payment_intent.confirmed",
        f"payment_intent:{pi.id}",
        {"amount": str(pi.amount), "currency": pi.currency},
    )

    broadcaster.broadcast_sync(
        tenant_id,
        "payment.status",
        {
            "paymentIntentId": str(pi.id),
            "status": "AUTHORIZED",
            "action": "confirmed",
            "amount": str(pi.amount),
            "currency": pi.currency,
        },
    )

    return _to_dto(pi)


def post_ledger_for_authorized_payment(
    session: Session, tenant_id: str, pid: uuid.UUID, gateway: Any = None
) -> None:
    with safe_begin(session):
        pi = session.execute(
            select(PaymentIntent)
            .where(PaymentIntent.tenant_id == tenant_id, PaymentIntent.id == pid)
            .with_for_update()
        ).scalar_one_or_none()
        if not pi:
            raise http_problem(404, "Not Found", "payment intent not found", instance="worker")
        if pi.status != "AUTHORIZED":
            return

        if gateway and pi.gateway_ref:
            import asyncio

            capture_result = asyncio.run(
                gateway.capture(
                    pi.gateway_ref,
                    Decimal(str(pi.amount)),
                    pi.currency,
                    f"capture:{tenant_id}:{pi.id}",
                )
            )
            if not capture_result.success:
                log.error(
                    "gateway capture failed",
                    extra={
                        "payment_intent_id": str(pi.id),
                        "gateway_ref": pi.gateway_ref,
                        "error_code": capture_result.error_code,
                        "error_message": capture_result.error_message,
                    },
                )
                raise http_problem(
                    502,
                    "Bad Gateway",
                    f"gateway capture error: {capture_result.error_message}",
                    instance="worker",
                )

        debit_account = _resolve_account(session, tenant_id, "CASH")
        credit_account = _resolve_account(session, tenant_id, "REVENUE")

        entry = LedgerEntry(tenant_id=tenant_id, payment_intent_id=pi.id, posted_at=_utcnow())
        entry.lines = [
            LedgerLine(
                tenant_id=tenant_id,
                side="DEBIT",
                account=debit_account,
                amount=pi.amount,
                currency=pi.currency,
            ),
            LedgerLine(
                tenant_id=tenant_id,
                side="CREDIT",
                account=credit_account,
                amount=pi.amount,
                currency=pi.currency,
            ),
        ]
        session.add(entry)

        pi.status = "SETTLED"
        pi.updated_at = _utcnow()

        order_id = ""
        if pi.customer_ref.startswith("order:"):
            order_id = pi.customer_ref.removeprefix("order:").strip()

        event_payload = {
            "order_id": order_id,
            "tenant_id": tenant_id,
            "correlation_id": get_correlation_id(),
            "payment_intent_id": str(pi.id),
            "status": "SETTLED",
            "amount": str(pi.amount),
            "currency": pi.currency,
        }
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="payment.settled",
                aggregate_type="PaymentIntent",
                aggregate_id=str(pi.id),
                payload=event_payload,
            )
        )
        enqueue_webhook_deliveries(session, tenant_id, "payment.settled", event_payload)

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "payment_intent.settled",
        f"payment_intent:{pi.id}",
        {"amount": str(pi.amount), "currency": pi.currency},
    )

    broadcaster.broadcast_sync(
        tenant_id,
        "payment.status",
        {
            "paymentIntentId": str(pi.id),
            "status": "SETTLED",
            "action": "settled",
            "amount": str(pi.amount),
            "currency": pi.currency,
        },
    )


def update_payment_from_stripe_event(session: Session, gateway_ref: str, new_status: str) -> None:
    """Reconcile payment intent status from a Stripe webhook event."""
    intent = session.execute(
        select(PaymentIntent).where(PaymentIntent.gateway_ref == gateway_ref)
    ).scalar_one_or_none()
    if not intent:
        log.warning("Stripe webhook: no PaymentIntent found for gateway_ref=%s", gateway_ref)
        return

    current = intent.status
    allowed_transitions: dict[str, list[str]] = {
        "AUTHORIZED": ["SETTLED", "FAILED", "VOIDED"],
        "SETTLED": ["REFUNDED"],
        "CREATED": ["FAILED", "VOIDED"],
    }

    if new_status not in allowed_transitions.get(current, []):
        log.info(
            "Stripe webhook: skipping transition %s -> %s for gateway_ref=%s",
            current,
            new_status,
            gateway_ref,
        )
        return

    intent.status = new_status
    intent.updated_at = _utcnow()
    session.commit()
    log.info(
        "Stripe webhook: updated PaymentIntent gateway_ref=%s from %s to %s",
        gateway_ref,
        current,
        new_status,
    )


def void_payment_intent(
    session: Session,
    tenant_id: str,
    pid: uuid.UUID,
    gateway: Any = None,
    idempotency_key: str | None = None,
) -> PaymentIntentDTO:
    with safe_begin(session):
        pi = session.execute(
            select(PaymentIntent)
            .where(PaymentIntent.tenant_id == tenant_id, PaymentIntent.id == pid)
            .with_for_update()
        ).scalar_one_or_none()
        if not pi:
            raise http_problem(
                404,
                "Not Found",
                "payment intent not found",
                instance=f"/v1/payment-intents/{pid}/void",
            )
        if pi.status == "VOIDED":
            return _to_dto(pi)
        if pi.status != "AUTHORIZED":
            raise http_problem(
                409,
                "Conflict",
                f"cannot void payment with status {pi.status}",
                instance=f"/v1/payment-intents/{pid}/void",
            )

        if gateway and pi.gateway_ref:
            import asyncio

            gw_result = asyncio.run(gateway.void(pi.gateway_ref))
            if not gw_result.success:
                log.error(
                    "gateway void failed",
                    extra={
                        "error_code": gw_result.error_code,
                        "error_message": gw_result.error_message,
                    },
                )
                raise http_problem(
                    502,
                    "Bad Gateway",
                    f"gateway error: {gw_result.error_message}",
                    instance=f"/v1/payment-intents/{pid}/void",
                )

        existing_entries = (
            session.execute(
                select(LedgerEntry).where(
                    LedgerEntry.tenant_id == tenant_id,
                    LedgerEntry.payment_intent_id == pi.id,
                )
            )
            .scalars()
            .all()
        )
        if existing_entries:
            debit_account = _resolve_account(session, tenant_id, "REVENUE")
            credit_account = _resolve_account(session, tenant_id, "CASH")

            reversal = LedgerEntry(
                tenant_id=tenant_id, payment_intent_id=pi.id, posted_at=_utcnow()
            )
            reversal.lines = [
                LedgerLine(
                    tenant_id=tenant_id,
                    side="DEBIT",
                    account=debit_account,
                    amount=pi.amount,
                    currency=pi.currency,
                ),
                LedgerLine(
                    tenant_id=tenant_id,
                    side="CREDIT",
                    account=credit_account,
                    amount=pi.amount,
                    currency=pi.currency,
                ),
            ]
            session.add(reversal)

        pi.status = "VOIDED"
        pi.updated_at = _utcnow()

        event_payload = {
            "payment_intent_id": str(pi.id),
            "amount": str(pi.amount),
            "currency": pi.currency,
            "gateway_ref": pi.gateway_ref or "",
            "correlation_id": get_correlation_id(),
        }
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="payment.voided",
                aggregate_type="PaymentIntent",
                aggregate_id=str(pi.id),
                payload=event_payload,
            )
        )
        enqueue_webhook_deliveries(session, tenant_id, "payment.voided", event_payload)

    PAYMENT_INTENTS_VOIDED_TOTAL.labels(tenant_id).inc()

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "payment_intent.voided",
        f"payment_intent:{pi.id}",
        {"amount": str(pi.amount), "currency": pi.currency},
    )

    broadcaster.broadcast_sync(
        tenant_id,
        "payment.status",
        {
            "paymentIntentId": str(pi.id),
            "status": "VOIDED",
            "action": "voided",
            "amount": str(pi.amount),
            "currency": pi.currency,
        },
    )

    return _to_dto(pi)
