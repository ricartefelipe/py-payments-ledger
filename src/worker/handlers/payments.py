from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application import gateway_ops, settlement
from src.application.payments import post_ledger_for_authorized_payment
from src.application.ports.payment_gateway import (
    GatewayResult,
    GatewayStatus,
    PaymentGatewayPort,
)
from src.infrastructure.db.models import OutboxEvent, PaymentIntent
from src.infrastructure.db.session import safe_begin
from src.shared.config import Settings
from src.shared.correlation import get_correlation_id, set_correlation_id
from src.shared.logging import get_logger
from src.worker.handlers.charge_request import parse_charge_payload

log = get_logger(__name__)

_gateway: PaymentGatewayPort | None = None
_settings: Settings | None = None


def _commit(session: Session) -> None:
    """Commita se houver transacao aberta (contexto HTTP ou worker)."""
    if session.in_transaction():
        session.commit()


def set_gateway(gateway: PaymentGatewayPort) -> None:
    global _gateway
    _gateway = gateway


def set_settings(settings: Settings) -> None:
    global _settings
    _settings = settings


def _utcnow() -> datetime:
    return datetime.now(UTC)


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
    """Cria o pagamento de um pedido vindo do node-b2b-orders.

    Tres defeitos foram corrigidos aqui:

    1. **Deduplicacao que nunca encontrava nada.** A consulta comparava
       ``customer_ref == f"order:{order_id}"`` sobre uma coluna cifrada com nonce
       aleatorio: nao casava nunca com ``ENCRYPTION_KEY`` configurada. Toda
       reentrega do RabbitMQ (que entrega pelo menos uma vez) criava um pagamento
       novo e autorizava de novo. Agora a busca usa o indice cego.

    2. **Rede e ``time.sleep`` dentro da transacao.** O laco de retry rodava
       ``asyncio.run(authorize)`` e dormia ate 30s com a transacao aberta. Agora
       o pagamento e persistido primeiro, a transacao fecha, e so depois a rede
       e tocada.

    3. **Nada impedia cobranca dupla no provedor.** A protecao era apenas a
       consulta previa. Agora a autorizacao e registrada em ``gateway_operations``
       com a chave ``charge:<tenant>:<order_id>``, unica por tenant no banco: se
       duas entregas correrem juntas, so uma consegue autorizar.
    """
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
    customer_ref = f"order:{order_id}"
    gateway_hint = parsed.get("gateway")
    payment_type = parsed.get("payment_type")

    # ---- Passo 1: persistir a intencao (transacao curta, sem rede) ----------
    with safe_begin(session):
        existing = settlement.find_by_customer_ref(session, tenant_id, customer_ref)
        if existing:
            log.info(
                "order already processed",
                extra={
                    "order_id": order_id,
                    "payment_intent_id": str(existing.id),
                    "status": existing.status,
                    "correlation_id": parsed["correlation_id"],
                },
            )
            return

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

        now = _utcnow()
        pi = PaymentIntent(
            tenant_id=tenant_id,
            amount=amount,
            currency=currency,
            status="CREATED",
            customer_ref=customer_ref,
            gateway_provider=gateway_provider,
            created_at=now,
            updated_at=now,
        )
        session.add(pi)
        session.flush()

        claim = None
        if gateway is not None:
            try:
                claim = gateway_ops.claim_operation(
                    session,
                    tenant_id,
                    pi,
                    gateway_ops.AUTHORIZE,
                    amount=amount,
                    currency=currency,
                    provider=gateway_provider or "unknown",
                    # Ancorada no pedido: duas entregas da mesma mensagem
                    # colidem nesta chave e so uma autoriza.
                    idempotency_key=f"charge:{tenant_id}:{order_id}",
                )
            except gateway_ops.OperationInFlightError:
                log.info(
                    "another worker is already charging this order",
                    extra={"order_id": order_id, "tenant_id": tenant_id},
                )
                return
            except gateway_ops.UncertainOperationError:
                log.error(
                    "charge not attempted: a previous authorization for this order "
                    "has an unknown outcome",
                    extra={"order_id": order_id, "tenant_id": tenant_id},
                )
                settlement.mark_for_review(
                    session,
                    tenant_id,
                    pi,
                    source="charge_request",
                    reason=f"previous authorization for order {order_id} is uncertain",
                )
                _commit(session)
                return

        pi_id = pi.id

    # Este commit torna duravel tanto o PaymentIntent quanto o claim: se o
    # processo cair agora, a autorizacao remota nao foi emitida, e se tiver sido
    # emitida ha registro para a reconciliacao encontrar.
    _commit(session)

    if claim is None:
        _finish_without_gateway(session, tenant_id, pi_id, order_id, amount, currency, parsed)
        return

    # ---- Passo 2: rede, sem nenhuma transacao aberta -----------------------
    result = _authorize_with_backoff(gateway, claim, customer_ref)

    # ---- Passo 3: finalizar (transacao curta, com CAS) ---------------------
    gateway_ops.record_result_durably(session, claim, result)

    with safe_begin(session):
        pi = gateway_ops.claim_still_valid(session, claim)

        if not gateway_ops.version_unchanged(pi, claim):
            log.warning(
                "discarding late authorize result for order: payment advanced meanwhile",
                extra={"order_id": order_id, "current_status": pi.status},
            )
            _commit(session)
            return

        if result.success:
            gateway_ops.attach_gateway_ref(session, pi, result.gateway_ref)
            if settlement.can_transition(pi.status, settlement.AUTHORIZED):
                pi.status = settlement.AUTHORIZED
                pi.version = (pi.version or 0) + 1
                pi.updated_at = _utcnow()
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
                        "gateway_ref": result.gateway_ref,
                        "gateway_provider": pi.gateway_provider,
                        "correlation_id": parsed["correlation_id"] or get_correlation_id(),
                    },
                )
            )
            log.info(
                "payment intent authorized from charge request",
                extra={
                    "order_id": order_id,
                    "payment_intent_id": str(pi.id),
                    "tenant_id": tenant_id,
                    "correlation_id": parsed["correlation_id"],
                },
            )
        elif result.is_uncertain:
            settlement.mark_for_review(
                session,
                tenant_id,
                pi,
                source="charge_request",
                reason=f"{result.error_code}: {result.error_message}",
            )
            log.error(
                "authorize outcome unknown for order; under reconciliation",
                extra={"order_id": order_id, "error_code": result.error_code},
            )
        else:
            settlement.fail_payment(
                session,
                tenant_id,
                pi,
                source="charge_request",
                error_code=result.error_code,
                error_message=result.error_message,
            )
            _emit_retry_exhausted(
                session,
                tenant_id=tenant_id,
                order_id=order_id,
                amount=amount,
                currency=currency,
                error_code=result.error_code,
                error_message=result.error_message,
                correlation_id=parsed["correlation_id"] or get_correlation_id(),
            )

    _commit(session)


def _authorize_with_backoff(gateway: Any, claim: Any, customer_ref: str) -> GatewayResult:
    """Autoriza com backoff, **fora** de qualquer transacao.

    So reenvia quando o erro e explicitamente transitorio (nao chegou ao
    provedor). Resultado incerto nao e reenviado: o provedor pode ter aceitado,
    e a mesma chave sera usada pela reconciliacao para consultar.
    """
    max_retries = getattr(_settings, "charge_request_max_retries", 3) if _settings else 3
    last: GatewayResult | None = None
    for attempt in range(max_retries + 1):
        last = gateway_ops.run_gateway(
            gateway.authorize(
                claim.tenant_id,
                claim.amount,
                claim.currency,
                customer_ref,
                claim.idempotency_key,
            )
        )
        if last.success or last.is_uncertain or not last.is_retryable or attempt >= max_retries:
            break
        delay = min(2**attempt, 30)
        log.warning(
            "retrying authorize after a transient failure",
            extra={"attempt": attempt + 1, "delay": delay, "error_code": last.error_code},
        )
        time.sleep(delay)
    return last or GatewayResult(
        success=False,
        gateway_ref="",
        status=GatewayStatus.FAILED,
        error_code="no_result",
        error_message="gateway produced no result",
    )


def _finish_without_gateway(
    session: Session,
    tenant_id: str,
    pi_id: Any,
    order_id: str,
    amount: Decimal,
    currency: str,
    parsed: dict[str, Any],
) -> None:
    """Fluxo sem gateway configurado: autoriza localmente e publica o evento."""
    with safe_begin(session):
        pi = settlement.load_for_update(session, tenant_id, pi_id, instance="charge_request")
        if settlement.can_transition(pi.status, settlement.AUTHORIZED):
            pi.status = settlement.AUTHORIZED
            pi.version = (pi.version or 0) + 1
            pi.updated_at = _utcnow()
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
                    "gateway_ref": "",
                    "gateway_provider": pi.gateway_provider,
                    "correlation_id": parsed["correlation_id"] or get_correlation_id(),
                },
            )
        )
    _commit(session)
    log.info(
        "payment intent created from charge request (no gateway configured)",
        extra={"order_id": order_id, "payment_intent_id": str(pi_id), "tenant_id": tenant_id},
    )
