from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application import gateway_ops, settlement
from src.application.event_broadcaster import broadcaster
from src.application.exceptions import LedgerImbalanceError
from src.application.ports.payment_gateway import GatewayResult, GatewayStatus
from src.application.security import _audit
from src.application.webhooks import enqueue_webhook_deliveries
from src.infrastructure.db.models import (
    AccountConfig,
    LedgerEntry,
    OutboxEvent,
    PaymentIntent,
)
from src.infrastructure.db.session import safe_begin
from src.shared.correlation import get_correlation_id, get_subject
from src.shared.logging import get_logger
from src.shared.metrics import (
    PAYMENT_INTENTS_CONFIRMED_TOTAL,
    PAYMENT_INTENTS_CREATED_TOTAL,
    PAYMENT_INTENTS_VOIDED_TOTAL,
)
from src.shared.problem import http_problem

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _commit(session: Session) -> None:
    """Commita se houver transacao aberta.

    Em contexto HTTP o ``safe_begin`` reaproveita a transacao implicita da
    sessao e nao commita na saida do bloco; no worker, ``session.begin()`` ja
    commitou ao sair. Este helper cobre os dois casos: e esse commit que torna o
    claim duravel e libera o lock da linha antes de qualquer chamada de rede.
    """
    if session.in_transaction():
        session.commit()


def _validate_ledger_balance(entry: LedgerEntry) -> None:
    debit_sum = sum(
        (Decimal(str(line.amount)) for line in entry.lines if line.side == "DEBIT"),
        Decimal(0),
    )
    credit_sum = sum(
        (Decimal(str(line.amount)) for line in entry.lines if line.side == "CREDIT"),
        Decimal(0),
    )
    if debit_sum != credit_sum:
        entry_id = getattr(entry, "id", None) or entry.payment_intent_id or "unknown"
        raise LedgerImbalanceError(str(entry_id), debit_sum, credit_sum)


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
    amount: Decimal,
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
                    GatewayConfig.supported_currencies.any(currency),  # type: ignore[arg-type]
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

    # A ordem aqui importa. Antes, a autorizacao no provedor acontecia **antes**
    # de existir qualquer registro local: se o insert falhasse em seguida, o
    # dinheiro ficava autorizado no provedor e localmente nao havia nem o
    # registro de que a operacao foi tentada. Agora a intencao e persistida
    # primeiro, e so depois a rede e tocada.
    with safe_begin(session):
        pi = PaymentIntent(
            tenant_id=tenant_id,
            amount=amount,
            currency=currency,
            status="CREATED",
            customer_ref=customer_ref,
            gateway_ref=None,
            gateway_provider=gateway_provider,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(pi)
        session.flush()

        claim: gateway_ops.OperationClaim | None = None
        if gw and idempotency_key:
            claim = gateway_ops.claim_operation(
                session,
                tenant_id,
                pi,
                gateway_ops.AUTHORIZE,
                amount=Decimal(str(amount)),
                currency=currency,
                provider=gateway_provider or "unknown",
                # A chave vem do cliente: e a identidade que ele escolheu para
                # esta obrigacao, e e com ela que um reenvio alcanca a mesma
                # operacao no provedor.
                idempotency_key=f"authorize:{tenant_id}:{idempotency_key}",
                # A criacao termina em CREATED: a autorizacao remota faz parte
                # da criacao e o cliente confirma em seguida. Adotar o estado
                # transitorio AUTHORIZING aqui faria o `confirm` seguinte
                # recusar com 409.
                apply_transitional=False,
                instance="/v1/payment-intents",
            )

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

    # Commit: o pagamento e o claim ficam duraveis antes de qualquer rede.
    _commit(session)

    if claim is not None:
        gw_result = gateway_ops.run_gateway(
            gw.authorize(
                tenant_id,
                claim.amount,
                claim.currency,
                customer_ref,
                claim.idempotency_key,
                payment_method_token=payment_method_token,
            )
        )
        gateway_ops.record_result_durably(session, claim, gw_result)
        with safe_begin(session):
            pi = gateway_ops.claim_still_valid(session, claim)
            if gw_result.success:
                gateway_ops.attach_gateway_ref(session, pi, gw_result.gateway_ref)
                gateway_ref = gw_result.gateway_ref
            elif gw_result.is_uncertain:
                settlement.mark_for_review(
                    session,
                    tenant_id,
                    pi,
                    source="create",
                    reason=f"{gw_result.error_code}: {gw_result.error_message}",
                )
                _commit(session)
                raise http_problem(
                    502,
                    "Bad Gateway",
                    "the gateway did not confirm the authorization; "
                    "the payment is under reconciliation",
                    instance="/v1/payment-intents",
                )
            else:
                settlement.fail_payment(
                    session,
                    tenant_id,
                    pi,
                    source="create",
                    error_code=gw_result.error_code,
                    error_message=gw_result.error_message,
                )
                log.error(
                    "gateway authorize failed",
                    extra={
                        "payment_intent_id": str(pi.id),
                        "error_code": gw_result.error_code,
                    },
                )
                _commit(session)
                raise http_problem(
                    502,
                    "Bad Gateway",
                    f"gateway error: {gw_result.error_message}",
                    instance="/v1/payment-intents",
                )
        _commit(session)

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
    """Autoriza o pagamento no provedor e marca ``AUTHORIZED``.

    A chamada ao provedor acontece **fora** do lock da linha. O ciclo e:
    reservar a intencao em transacao curta, commitar, falar com o provedor sem
    transacao aberta e finalizar com compare-and-set sobre ``version``.
    """
    instance = f"/v1/payment-intents/{pid}/confirm"

    with safe_begin(session):
        pi = settlement.load_for_update(session, tenant_id, pid, instance=instance)
        if pi.status in (settlement.SETTLED, settlement.FAILED):
            return _to_dto(pi)
        if pi.status == settlement.AUTHORIZED:
            return _to_dto(pi)
        if pi.status != settlement.CREATED:
            raise http_problem(
                409, "Conflict", f"cannot confirm status {pi.status}", instance=instance
            )

        needs_gateway = bool(gateway) and not pi.gateway_ref
        claim: gateway_ops.OperationClaim | None = None
        if needs_gateway:
            try:
                claim = gateway_ops.claim_operation(
                    session,
                    tenant_id,
                    pi,
                    gateway_ops.AUTHORIZE,
                    amount=Decimal(str(pi.amount)),
                    currency=pi.currency,
                    provider=pi.gateway_provider or "unknown",
                    allowed_from=frozenset({settlement.CREATED}),
                    instance=instance,
                )
            except gateway_ops.OperationInFlightError as exc:
                raise http_problem(
                    409,
                    "Conflict",
                    "another request is already performing this operation",
                    instance=instance,
                ) from exc
            except gateway_ops.UncertainOperationError as exc:
                # Ja existe uma autorizacao cujo desfecho nao se conhece.
                # Emitir outra poderia autorizar duas vezes.
                settlement.mark_for_review(
                    session, tenant_id, pi, source="confirm", reason=str(exc)
                )
                _commit(session)
                raise http_problem(
                    409,
                    "Conflict",
                    "a previous authorization for this payment has an unknown outcome; "
                    "it is under reconciliation",
                    instance=instance,
                ) from exc
        else:
            settlement_outcome_status = settlement.AUTHORIZED
            if settlement.can_transition(pi.status, settlement_outcome_status):
                pi.status = settlement_outcome_status
                pi.version = (pi.version or 0) + 1
                pi.updated_at = _utcnow()
            _emit_authorized(session, tenant_id, pi)

    # O commit torna o claim duravel e libera o lock antes da rede.
    _commit(session)

    if claim is None:
        _after_authorized(session, tenant_id, pid)
        with safe_begin(session):
            return _to_dto(settlement.load_for_update(session, tenant_id, pid, instance=instance))

    if claim.already_resolved:
        gw_result = GatewayResult(
            success=True,
            gateway_ref=claim.existing_ref or "",
            status=GatewayStatus.AUTHORIZED,
        )
    else:
        gw_result = gateway_ops.run_gateway(
            gateway.authorize(
                tenant_id,
                claim.amount,
                claim.currency,
                "",
                claim.idempotency_key,
            )
        )

    gateway_ops.record_result_durably(session, claim, gw_result)

    with safe_begin(session):
        pi = gateway_ops.claim_still_valid(session, claim)

        if not gateway_ops.version_unchanged(pi, claim):
            # Outro caminho concluiu este pagamento enquanto a chamada estava em
            # curso. Um resultado tardio nao pode sobrescrever o desfecho.
            log.warning(
                "discarding late authorize result: payment advanced meanwhile",
                extra={
                    "payment_intent_id": str(pi.id),
                    "claimed_version": claim.claimed_version,
                    "current_version": pi.version,
                    "current_status": pi.status,
                },
            )
            _commit(session)
            return _to_dto(pi)

        if gw_result.success:
            gateway_ops.attach_gateway_ref(session, pi, gw_result.gateway_ref)
            if settlement.can_transition(pi.status, settlement.AUTHORIZED):
                pi.status = settlement.AUTHORIZED
                pi.version = (pi.version or 0) + 1
                pi.updated_at = _utcnow()
            _emit_authorized(session, tenant_id, pi)
        elif gw_result.is_uncertain:
            settlement.mark_for_review(
                session,
                tenant_id,
                pi,
                source="confirm",
                reason=f"{gw_result.error_code}: {gw_result.error_message}",
            )
            _commit(session)
            raise http_problem(
                502,
                "Bad Gateway",
                "the gateway did not confirm the authorization; it is under reconciliation",
                instance=instance,
            )
        else:
            settlement.fail_payment(
                session,
                tenant_id,
                pi,
                source="confirm",
                error_code=gw_result.error_code,
                error_message=gw_result.error_message,
            )
            _commit(session)
            raise http_problem(
                502,
                "Bad Gateway",
                f"gateway error: {gw_result.error_message}",
                instance=instance,
            )

    _commit(session)
    _after_authorized(session, tenant_id, pid)
    with safe_begin(session):
        return _to_dto(settlement.load_for_update(session, tenant_id, pid, instance=instance))


def _emit_authorized(session: Session, tenant_id: str, pi: PaymentIntent) -> None:
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


def _after_authorized(session: Session, tenant_id: str, pid: uuid.UUID) -> None:
    PAYMENT_INTENTS_CONFIRMED_TOTAL.labels(tenant_id).inc()
    pi = session.get(PaymentIntent, pid)
    if pi is None:  # pragma: no cover
        return
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
            "status": pi.status,
            "action": "confirmed",
            "amount": str(pi.amount),
            "currency": pi.currency,
        },
    )


def post_ledger_for_authorized_payment(
    session: Session, tenant_id: str, pid: uuid.UUID, gateway: Any = None
) -> None:
    """Captura no provedor e liquida localmente.

    Separacao em tres passos: reserva da intencao (transacao curta), captura
    remota (sem transacao aberta) e liquidacao (transacao curta com
    compare-and-set). A liquidacao usa ``settlement.settle_payment``, o mesmo
    caminho do webhook e da reconciliacao, de modo que nao existe forma de
    liquidar sem lancamento no journal nem de lancar duas vezes.
    """
    instance = "worker"

    with safe_begin(session):
        pi = settlement.load_for_update(session, tenant_id, pid, instance=instance)

        if pi.status in settlement.SETTLED_STATES:
            # Pode ser liquidacao ja feita, ou o rastro do defeito antigo
            # (status liquidado sem journal). `settle_payment` resolve os dois.
            settlement.settle_payment(session, tenant_id, pi, source="capture")
            _commit(session)
            return

        if pi.status not in (settlement.AUTHORIZED, settlement.CAPTURING):
            log.info(
                "skipping capture: payment is not authorized",
                extra={"payment_intent_id": str(pid), "status": pi.status},
            )
            return

        needs_gateway = bool(gateway) and bool(pi.gateway_ref)
        claim: gateway_ops.OperationClaim | None = None
        if needs_gateway:
            try:
                claim = gateway_ops.claim_operation(
                    session,
                    tenant_id,
                    pi,
                    gateway_ops.CAPTURE,
                    amount=Decimal(str(pi.amount)),
                    currency=pi.currency,
                    provider=pi.gateway_provider or "unknown",
                    allowed_from=frozenset({settlement.AUTHORIZED, settlement.CAPTURING}),
                    instance=instance,
                )
            except gateway_ops.OperationInFlightError as exc:
                raise http_problem(
                    409,
                    "Conflict",
                    "another request is already performing this operation",
                    instance=instance,
                ) from exc
            except gateway_ops.UncertainOperationError as exc:
                settlement.mark_for_review(
                    session, tenant_id, pi, source="capture", reason=str(exc)
                )
                _commit(session)
                log.error(
                    "capture not attempted: a previous capture has an unknown outcome",
                    extra={"payment_intent_id": str(pid)},
                )
                return
        else:
            settlement.settle_payment(session, tenant_id, pi, source="capture")
            _commit(session)
            return

    _commit(session)

    if claim.already_resolved:
        capture_result = GatewayResult(
            success=True,
            gateway_ref=claim.existing_ref or claim.gateway_ref or "",
            status=GatewayStatus.CAPTURED,
        )
    else:
        capture_result = gateway_ops.run_gateway(
            gateway.capture(
                claim.gateway_ref,
                claim.amount,
                claim.currency,
                claim.idempotency_key,
            )
        )

    # Desfecho gravado e commitado antes da liquidacao: se a liquidacao falhar,
    # a retomada sabe que a captura foi aceita e nao a reenvia.
    gateway_ops.record_result_durably(session, claim, capture_result)

    with safe_begin(session):
        pi = gateway_ops.claim_still_valid(session, claim)

        if not gateway_ops.version_unchanged(pi, claim):
            log.warning(
                "discarding late capture result: payment advanced meanwhile",
                extra={
                    "payment_intent_id": str(pi.id),
                    "claimed_version": claim.claimed_version,
                    "current_version": pi.version,
                    "current_status": pi.status,
                },
            )
            _commit(session)
            return

        if capture_result.success:
            settlement.settle_payment(session, tenant_id, pi, source="capture")
        elif capture_result.is_uncertain:
            settlement.mark_for_review(
                session,
                tenant_id,
                pi,
                source="capture",
                reason=f"{capture_result.error_code}: {capture_result.error_message}",
            )
            log.error(
                "capture outcome unknown; payment is under reconciliation",
                extra={
                    "payment_intent_id": str(pi.id),
                    "gateway_ref": pi.gateway_ref,
                    "error_code": capture_result.error_code,
                },
            )
        else:
            # Recusa definitiva: volta ao estado estavel anterior. O dinheiro
            # segue autorizado no provedor e pode ser cancelado por `void`.
            gateway_ops.release_claim(session, claim, pi, settlement.AUTHORIZED)
            log.error(
                "gateway capture rejected",
                extra={
                    "payment_intent_id": str(pi.id),
                    "gateway_ref": pi.gateway_ref,
                    "error_code": capture_result.error_code,
                    "error_message": capture_result.error_message,
                },
            )
            _commit(session)
            raise http_problem(
                502,
                "Bad Gateway",
                f"gateway capture error: {capture_result.error_message}",
                instance=instance,
            )

    _commit(session)


def resolve_payment_by_gateway_ref(
    session: Session, gateway_ref: str, *, provider: str | None = None
) -> PaymentIntent | None:
    """Encontra o pagamento a partir da referencia do provedor, sem tenant.

    Um webhook do provedor chega sem nocao de tenant: o Stripe nao conhece a
    nossa divisao. Por isso esta e a **unica** consulta do servico que atravessa
    tenants deliberadamente, e ela:

    * usa o indice cego (a coluna cifrada nao serve para igualdade);
    * restringe por ``gateway_provider`` quando informado, reduzindo colisao;
    * **recusa** quando a referencia aponta para mais de um pagamento, em vez de
      escolher um arbitrariamente e liquidar o pagamento do tenant errado.

    Todo o trabalho seguinte acontece no tenant do pagamento encontrado.
    """
    from src.shared.blind_index import blind_index_for

    q = select(PaymentIntent).where(PaymentIntent.gateway_ref_bidx == blind_index_for(gateway_ref))
    if provider:
        q = q.where(PaymentIntent.gateway_provider == provider)
    rows = session.execute(q).scalars().all()

    if not rows:
        return None
    if len(rows) > 1:
        tenants = sorted({r.tenant_id for r in rows})
        log.error(
            "gateway reference maps to more than one payment intent; refusing to guess",
            extra={"provider": provider or "", "tenants": tenants, "count": len(rows)},
        )
        raise http_problem(
            409,
            "Conflict",
            "this gateway reference maps to more than one payment intent",
            instance="webhook",
        )
    return rows[0]


def update_payment_from_stripe_event(
    session: Session,
    gateway_ref: str,
    new_status: str,
    *,
    provider: str | None = None,
    amount: Decimal | None = None,
    currency: str | None = None,
    event_ref: str | None = None,
) -> bool:
    """Aplica um evento do provedor pelo caminho de dominio unico.

    A versao anterior apenas trocava ``status`` e commitava: um evento
    ``payment_intent.succeeded`` deixava o pagamento ``SETTLED`` **sem nenhum
    lancamento no journal e sem evento no outbox** — estado financeiro sem fato
    economico. Alem disso buscava por ``gateway_ref`` sem tenant e sem lock.

    Agora delega para ``settlement``, que em uma unica transacao valida a
    transicao, confere valor e moeda, lanca no journal com deduplicacao por fato
    e publica no outbox. Devolve ``True`` quando algo mudou.

    ``amount``/``currency`` vem do evento e sao conferidos contra o registro
    local: a assinatura prova que o provedor enviou o evento, nao que o evento
    se refere a este valor.
    """
    found = resolve_payment_by_gateway_ref(session, gateway_ref, provider=provider)
    if not found:
        log.warning(
            "provider webhook: no payment intent for this gateway reference",
            extra={"provider": provider or "", "new_status": new_status},
        )
        return False

    tenant_id = found.tenant_id
    with safe_begin(session):
        pi = settlement.load_for_update(session, tenant_id, found.id, instance="webhook")
        source = f"webhook:{provider or 'stripe'}"

        if new_status == settlement.SETTLED:
            outcome = settlement.settle_payment(
                session, tenant_id, pi, source=source, amount=amount, currency=currency
            )
        elif new_status == settlement.REFUNDED:
            outcome = settlement.refund_payment(
                session,
                tenant_id,
                pi,
                source=source,
                amount=amount,
                refund_ref=event_ref,
            )
        elif new_status == settlement.VOIDED:
            outcome = settlement.void_payment(session, tenant_id, pi, source=source)
        elif new_status == settlement.FAILED:
            outcome = settlement.fail_payment(session, tenant_id, pi, source=source)
        elif new_status == settlement.AUTHORIZED:
            # Autorizacao nao produz fato contabil (o journal nasce na
            # liquidacao), mas o estado e o evento no outbox precisam avancar.
            settlement.verify_matches(
                pi, amount=amount, currency=currency, source=source, instance="webhook"
            )
            if pi.status == settlement.AUTHORIZED:
                outcome = settlement.SettlementOutcome(
                    status=pi.status, changed=False, already_applied=True
                )
            elif settlement.can_transition(pi.status, settlement.AUTHORIZED):
                pi.status = settlement.AUTHORIZED
                pi.version = (pi.version or 0) + 1
                pi.updated_at = _utcnow()
                _emit_authorized(session, tenant_id, pi)
                outcome = settlement.SettlementOutcome(status=settlement.AUTHORIZED, changed=True)
            else:
                log.info(
                    "provider webhook: refusing to move back to AUTHORIZED",
                    extra={"payment_intent_id": str(pi.id), "status": pi.status},
                )
                outcome = settlement.SettlementOutcome(
                    status=pi.status, changed=False, already_applied=True
                )
        else:
            log.info(
                "provider webhook: unsupported target status",
                extra={"new_status": new_status, "payment_intent_id": str(pi.id)},
            )
            return False

    _commit(session)
    log.info(
        "provider webhook applied",
        extra={
            "payment_intent_id": str(found.id),
            "tenant_id": tenant_id,
            "new_status": outcome.status,
            "changed": outcome.changed,
            "already_applied": outcome.already_applied,
        },
    )
    return outcome.changed


def void_payment_intent(
    session: Session,
    tenant_id: str,
    pid: uuid.UUID,
    gateway: Any = None,
    idempotency_key: str | None = None,
) -> PaymentIntentDTO:
    """Cancela a autorizacao no provedor e compensa o lancamento, se houver.

    O ``void`` tambem recebe chave de idempotencia estavel: um reenvio apos
    timeout alcanca a mesma operacao no provedor.
    """
    instance = f"/v1/payment-intents/{pid}/void"

    with safe_begin(session):
        pi = settlement.load_for_update(session, tenant_id, pid, instance=instance)
        if pi.status == settlement.VOIDED:
            return _to_dto(pi)
        if not settlement.can_transition(pi.status, settlement.VOIDED):
            raise http_problem(
                409,
                "Conflict",
                f"cannot void payment with status {pi.status}",
                instance=instance,
            )

        needs_gateway = bool(gateway) and bool(pi.gateway_ref)
        claim: gateway_ops.OperationClaim | None = None
        if needs_gateway:
            try:
                claim = gateway_ops.claim_operation(
                    session,
                    tenant_id,
                    pi,
                    gateway_ops.VOID,
                    amount=Decimal(str(pi.amount)),
                    currency=pi.currency,
                    provider=pi.gateway_provider or "unknown",
                    instance=instance,
                )
            except gateway_ops.OperationInFlightError as exc:
                raise http_problem(
                    409,
                    "Conflict",
                    "another request is already performing this operation",
                    instance=instance,
                ) from exc
            except gateway_ops.UncertainOperationError as exc:
                settlement.mark_for_review(session, tenant_id, pi, source="void", reason=str(exc))
                _commit(session)
                raise http_problem(
                    409,
                    "Conflict",
                    "a previous void for this payment has an unknown outcome; "
                    "it is under reconciliation",
                    instance=instance,
                ) from exc
        else:
            outcome = settlement.void_payment(session, tenant_id, pi, source="void")
            PAYMENT_INTENTS_VOIDED_TOTAL.labels(tenant_id).inc()
            dto = _to_dto(pi)
            log.info(
                "payment voided without gateway call",
                extra={"payment_intent_id": str(pi.id), "entry_id": outcome.entry_id},
            )
            _commit(session)
            return dto

    _commit(session)

    if claim.already_resolved:
        gw_result = GatewayResult(
            success=True,
            gateway_ref=claim.gateway_ref or "",
            status=GatewayStatus.VOIDED,
        )
    else:
        gw_result = gateway_ops.run_gateway(gateway.void(claim.gateway_ref, claim.idempotency_key))

    gateway_ops.record_result_durably(session, claim, gw_result)

    with safe_begin(session):
        pi = gateway_ops.claim_still_valid(session, claim)

        if not gateway_ops.version_unchanged(pi, claim):
            log.warning(
                "discarding late void result: payment advanced meanwhile",
                extra={"payment_intent_id": str(pi.id), "current_status": pi.status},
            )
            _commit(session)
            return _to_dto(pi)

        if gw_result.success:
            settlement.void_payment(session, tenant_id, pi, source="void")
            PAYMENT_INTENTS_VOIDED_TOTAL.labels(tenant_id).inc()
            dto = _to_dto(pi)
        elif gw_result.is_uncertain:
            settlement.mark_for_review(
                session,
                tenant_id,
                pi,
                source="void",
                reason=f"{gw_result.error_code}: {gw_result.error_message}",
            )
            _commit(session)
            raise http_problem(
                502,
                "Bad Gateway",
                "the gateway did not confirm the void; it is under reconciliation",
                instance=instance,
            )
        else:
            _commit(session)
            raise http_problem(
                502,
                "Bad Gateway",
                f"gateway error: {gw_result.error_message}",
                instance=instance,
            )

    _commit(session)
    return dto
