from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.application import gateway_ops, settlement
from src.application.journal import (
    CREDIT,
    DEBIT,
    ENTRY_REVERSAL,
    LineSpec,
    entry_total,
    post_entry,
    resolve_account,
    reversed_total,
)
from src.application.ports.payment_gateway import (
    GatewayResult,
    GatewayStatus,
    PaymentGatewayPort,
)
from src.application.security import _audit
from src.application.webhooks import enqueue_webhook_deliveries
from src.infrastructure.db.models import (
    AccountConfig,
    OutboxEvent,
    Refund,
)
from src.infrastructure.db.session import safe_begin
from src.shared.correlation import get_correlation_id, get_subject
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RefundDTO(BaseModel):
    id: str
    payment_intent_id: str
    amount: str
    reason: str | None
    status: str
    gateway_ref: str | None
    created_at: str


def _resolve_account(session: Session, tenant_id: str, code: str, fallback: str) -> str:
    cfg = session.execute(
        select(AccountConfig).where(
            AccountConfig.tenant_id == tenant_id,
            AccountConfig.code == code,
        )
    ).scalar_one_or_none()
    return cfg.code if cfg else fallback


def create_refund(
    session: Session,
    tenant_id: str,
    payment_intent_id: uuid.UUID,
    amount: Decimal,
    reason: str | None = None,
    gateway: PaymentGatewayPort | None = None,
    idempotency_key: str | None = None,
) -> RefundDTO:
    """Estorna total ou parcialmente, com a chamada remota fora do lock.

    Mudancas em relacao a versao anterior:

    * ``asyncio.run(gateway.refund(...))`` rodava com a linha do pagamento
      travada; agora o ``Refund`` e o claim sao persistidos, a transacao fecha,
      e so depois a rede e tocada;
    * o lancamento era montado a mao, sem ``fact_key`` e sem vinculo com a
      liquidacao original. Agora passa por ``journal.post_entry`` com
      ``entry_type=REVERSAL`` e ``reverses_entry_id`` apontando para o
      lancamento da liquidacao, de modo que o par original + estorno conta a
      historia e a soma dos estornos nao pode passar do valor liquidado;
    * a chave de idempotencia do provedor e estavel por estorno
      (``refund:<tenant>:<payment>:<refund_id>``), e nao um UUID novo por
      tentativa.

    O plano de contas e preservado: o estorno debita ``REFUND_EXPENSE`` e credita
    ``CASH``, como antes, em vez de reverter a receita.
    """
    instance = f"/v1/payment-intents/{payment_intent_id}/refund"

    if amount <= 0:
        raise http_problem(400, "Bad Request", "refund amount must be > 0", instance=instance)

    # ---- Passo 1: validar e reservar (transacao curta, sem rede) -----------
    with safe_begin(session):
        pi = settlement.load_for_update(session, tenant_id, payment_intent_id, instance=instance)

        if pi.status not in settlement.SETTLED_STATES or pi.status == settlement.REFUNDED:
            raise http_problem(
                409,
                "Conflict",
                f"cannot refund payment with status {pi.status}",
                instance=instance,
            )

        _total_raw = session.execute(
            select(func.coalesce(func.sum(Refund.amount), Decimal(0))).where(
                Refund.payment_intent_id == payment_intent_id,
                Refund.tenant_id == tenant_id,
                Refund.status.in_(("COMPLETED", "PENDING", "PROCESSING")),
            )
        ).scalar()
        total_refunded = Decimal(str(_total_raw)) if _total_raw is not None else Decimal(0)

        if total_refunded + amount > Decimal(str(pi.amount)):
            raise http_problem(
                422,
                "Unprocessable Entity",
                f"total refunds ({total_refunded + amount}) would exceed "
                f"payment amount ({pi.amount})",
                instance=instance,
            )

        settlement_entry = settlement.find_settlement_entry(session, tenant_id, pi.id)
        if settlement_entry is None:
            raise http_problem(
                422,
                "Unprocessable Entity",
                f"payment {pi.id} has no settlement entry to refund",
                instance=instance,
            )
        already_reversed = reversed_total(session, tenant_id, settlement_entry)
        settled_total = entry_total(settlement_entry)
        if already_reversed + amount > settled_total:
            raise http_problem(
                422,
                "Unprocessable Entity",
                f"refund {amount} exceeds refundable {settled_total - already_reversed}",
                instance=instance,
            )

        refund = Refund(
            tenant_id=tenant_id,
            payment_intent_id=payment_intent_id,
            amount=amount,
            reason=reason,
            status="PENDING",
            created_at=_utcnow(),
        )
        session.add(refund)
        session.flush()
        refund_id = refund.id
        currency = pi.currency

        claim = None
        if gateway and pi.gateway_ref:
            try:
                claim = gateway_ops.claim_operation(
                    session,
                    tenant_id,
                    pi,
                    gateway_ops.REFUND,
                    amount=amount,
                    currency=currency,
                    provider=pi.gateway_provider or "unknown",
                    suffix=str(refund_id),
                    idempotency_key=(
                        idempotency_key or f"refund:{tenant_id}:{payment_intent_id}:{refund_id}"
                    ),
                    instance=instance,
                )
            except gateway_ops.UncertainOperationError as exc:
                refund.status = "PROCESSING"
                settlement.mark_for_review(session, tenant_id, pi, source="refund", reason=str(exc))
                _commit(session)
                raise http_problem(
                    409,
                    "Conflict",
                    "a previous refund for this payment has an unknown outcome; "
                    "it is under reconciliation",
                    instance=instance,
                ) from exc

    _commit(session)

    # ---- Passo 2: rede, sem transacao aberta -------------------------------
    gw_result = None
    if claim is not None:
        if claim.already_resolved:
            gw_result = GatewayResult(
                success=True,
                gateway_ref=claim.existing_ref or "",
                status=GatewayStatus.REFUNDED,
            )
        else:
            # O claim so e criado quando ha gateway e referencia do provedor.
            assert gateway is not None  # noqa: S101
            assert claim.gateway_ref is not None  # noqa: S101
            gw_result = gateway_ops.run_gateway(
                gateway.refund(
                    claim.gateway_ref,
                    claim.amount,
                    claim.currency,
                    claim.idempotency_key,
                )
            )

    if claim is not None and gw_result is not None:
        gateway_ops.record_result_durably(session, claim, gw_result)

    # ---- Passo 3: finalizar (transacao curta) ------------------------------
    with safe_begin(session):
        pi = settlement.load_for_update(session, tenant_id, payment_intent_id, instance=instance)
        stored = session.get(Refund, refund_id)
        if stored is None:  # pragma: no cover
            raise http_problem(500, "Internal Server Error", "refund vanished", instance=instance)
        refund = stored

        if claim is not None and gw_result is not None:
            if gw_result.is_uncertain:
                # Pode ter sido estornado no provedor: nao marcar como falha nem
                # emitir outro estorno. Fica para a reconciliacao.
                refund.status = "PROCESSING"
                settlement.mark_for_review(
                    session,
                    tenant_id,
                    pi,
                    source="refund",
                    reason=f"{gw_result.error_code}: {gw_result.error_message}",
                )
                _commit(session)
                log.error(
                    "refund outcome unknown; under reconciliation",
                    extra={"refund_id": str(refund_id), "error_code": gw_result.error_code},
                )
                return _refund_dto(refund)

            if not gw_result.success:
                refund.status = "FAILED"
                log.error(
                    "gateway refund rejected",
                    extra={
                        "payment_intent_id": str(payment_intent_id),
                        "amount": str(amount),
                        "error_code": gw_result.error_code,
                    },
                )
                _commit(session)
                return _refund_dto(refund)

            refund.gateway_ref = gw_result.gateway_ref

        # Lancamento do estorno: vinculado a liquidacao, deduplicado por fato.
        settlement_entry = settlement.find_settlement_entry(session, tenant_id, pi.id)
        if settlement_entry is None:  # pragma: no cover - verificado no passo 1
            raise http_problem(
                422, "Unprocessable Entity", "settlement entry vanished", instance=instance
            )

        entry = post_entry(
            session,
            tenant_id,
            fact_key=f"refund:{pi.id}:{refund_id}",
            lines=[
                LineSpec(
                    DEBIT,
                    resolve_account(session, tenant_id, "REFUND_EXPENSE"),
                    amount,
                    pi.currency,
                ),
                LineSpec(CREDIT, resolve_account(session, tenant_id, "CASH"), amount, pi.currency),
            ],
            payment_intent_id=pi.id,
            entry_type=ENTRY_REVERSAL,
            reverses_entry_id=settlement_entry.id,
        )

        if entry is not None:
            total_after = reversed_total(session, tenant_id, settlement_entry)
            target = (
                settlement.REFUNDED
                if total_after >= entry_total(settlement_entry)
                else settlement.PARTIALLY_REFUNDED
            )
            if settlement.can_transition(pi.status, target):
                pi.status = target
                pi.version = (pi.version or 0) + 1
            pi.updated_at = _utcnow()

            refund.status = "COMPLETED"

            event_payload = {
                "payment_intent_id": str(payment_intent_id),
                "refund_id": str(refund_id),
                "amount": str(amount),
                "currency": pi.currency,
                "reason": reason or "",
                "payment_status": pi.status,
                "correlation_id": get_correlation_id(),
            }
            session.add(
                OutboxEvent(
                    tenant_id=tenant_id,
                    event_type="payment.refunded",
                    aggregate_type="PaymentIntent",
                    aggregate_id=str(payment_intent_id),
                    payload=event_payload,
                )
            )
            enqueue_webhook_deliveries(session, tenant_id, "payment.refunded", event_payload)
        else:
            # O fato ja estava lancado (reentrega): o estorno e idempotente.
            refund.status = "COMPLETED"

        _audit(
            session,
            tenant_id,
            get_subject() or "system",
            "refund.created",
            f"refund:{refund_id}",
            {
                "payment_intent_id": str(payment_intent_id),
                "amount": str(amount),
                "status": refund.status,
            },
        )
        dto = _refund_dto(refund)

    _commit(session)
    return dto


def _refund_dto(refund: Refund) -> RefundDTO:
    return RefundDTO(
        id=str(refund.id),
        payment_intent_id=str(refund.payment_intent_id),
        amount=str(refund.amount),
        reason=refund.reason,
        status=refund.status,
        gateway_ref=refund.gateway_ref,
        created_at=refund.created_at.isoformat(),
    )


def _commit(session: Session) -> None:
    """Commita se houver transacao aberta (contexto HTTP ou worker)."""
    if session.in_transaction():
        session.commit()


def list_refunds(session: Session, tenant_id: str, payment_intent_id: uuid.UUID) -> list[RefundDTO]:
    rows = (
        session.execute(
            select(Refund)
            .where(
                Refund.tenant_id == tenant_id,
                Refund.payment_intent_id == payment_intent_id,
            )
            .order_by(Refund.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        RefundDTO(
            id=str(r.id),
            payment_intent_id=str(r.payment_intent_id),
            amount=str(r.amount),
            reason=r.reason,
            status=r.status,
            gateway_ref=r.gateway_ref,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
