from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application import gateway_ops, settlement
from src.application.security import _audit
from src.application.webhooks import enqueue_webhook_deliveries
from src.infrastructure.db.models import (
    GatewayOperation,
    OutboxEvent,
    PaymentIntent,
    ReconciliationDiscrepancy,
)
from src.infrastructure.db.session import safe_begin
from src.shared.correlation import get_correlation_id, get_subject
from src.shared.logging import get_logger

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DiscrepancyDTO(BaseModel):
    id: str
    tenant_id: str
    payment_intent_id: str | None
    discrepancy_type: str
    gateway_ref: str | None
    expected_amount: str | None
    actual_amount: str | None
    expected_status: str | None
    actual_status: str | None
    resolved: bool
    created_at: str


STRIPE_STATUS_TO_LOCAL: dict[str, str] = {
    "requires_capture": "AUTHORIZED",
    "requires_confirmation": "AUTHORIZED",
    "succeeded": "SETTLED",
    "canceled": "FAILED",
    "requires_payment_method": "FAILED",
}

LOCAL_STATUS_TO_STRIPE: dict[str, list[str]] = {
    "CREATED": ["requires_confirmation", "requires_payment_method"],
    "AUTHORIZING": ["requires_capture", "requires_confirmation", "processing"],
    "AUTHORIZED": ["requires_capture", "requires_confirmation"],
    "CAPTURING": ["requires_capture", "processing", "succeeded"],
    "SETTLED": ["succeeded"],
    "PARTIALLY_REFUNDED": ["succeeded"],
    "REFUNDED": ["succeeded"],
    "VOIDED": ["canceled"],
    "FAILED": ["canceled", "requires_payment_method"],
    # REVIEW e justamente "nao se sabe": qualquer status remoto e informacao
    # nova, por isso nao ha expectativa a comparar. A resolucao acontece em
    # `resolve_uncertain_operations`.
    "REVIEW": [],
}


def reconcile_transactions(
    session: Session,
    tenant_id: str,
    gateway_transactions: list[dict[str, Any]],
    *,
    auto_fix: bool = False,
) -> list[DiscrepancyDTO]:
    """Compare gateway transactions with local PaymentIntents.

    gateway_transactions: list of dicts with keys:
        gateway_ref, amount, currency, status
    auto_fix: when True, automatically update local status to match gateway
        for STATUS_MISMATCH discrepancies.
    """
    discrepancies: list[DiscrepancyDTO] = []
    auto_fixed: list[str] = []

    with safe_begin(session):
        for gtx in gateway_transactions:
            gw_ref = gtx["gateway_ref"]
            gw_amount = Decimal(str(gtx["amount"]))
            gw_status = gtx["status"]

            # A coluna ``gateway_ref`` e cifrada com nonce aleatorio: a
            # comparacao direta nunca casava e toda transacao do provedor era
            # classificada como MISSING_LOCAL. A busca vai pelo indice cego.
            pi = settlement.find_by_gateway_ref(session, tenant_id, gw_ref, for_update=True)

            if not pi:
                disc = ReconciliationDiscrepancy(
                    tenant_id=tenant_id,
                    discrepancy_type="MISSING_LOCAL",
                    gateway_ref=gw_ref,
                    actual_amount=gw_amount,
                    actual_status=gw_status,
                    details={"gateway_transaction": gtx},
                )
                session.add(disc)
                session.flush()
                discrepancies.append(_to_dto(disc))
                continue

            if Decimal(str(pi.amount)) != gw_amount:
                disc = ReconciliationDiscrepancy(
                    tenant_id=tenant_id,
                    payment_intent_id=pi.id,
                    discrepancy_type="AMOUNT_MISMATCH",
                    gateway_ref=gw_ref,
                    expected_amount=pi.amount,
                    actual_amount=gw_amount,
                    details={"local_amount": str(pi.amount), "gateway_amount": str(gw_amount)},
                )
                session.add(disc)
                session.flush()
                discrepancies.append(_to_dto(disc))
                # Valor divergente indica que o objeto remoto pode nao
                # corresponder a este pagamento: nao alinhar status por cima.
                continue

            expected_gw_statuses = LOCAL_STATUS_TO_STRIPE.get(pi.status, [])
            if gw_status not in expected_gw_statuses and expected_gw_statuses:
                resolved_by_fix = False
                if auto_fix:
                    resolved_by_fix = _align_local_status(
                        session, tenant_id, pi, gw_status, gw_amount
                    )
                    if resolved_by_fix:
                        auto_fixed.append(gw_ref)

                disc = ReconciliationDiscrepancy(
                    tenant_id=tenant_id,
                    payment_intent_id=pi.id,
                    discrepancy_type="STATUS_MISMATCH",
                    gateway_ref=gw_ref,
                    expected_status=pi.status,
                    actual_status=gw_status,
                    resolved=resolved_by_fix,
                    details={
                        "expected_gateway_statuses": expected_gw_statuses,
                        "auto_fixed": resolved_by_fix,
                    },
                )
                session.add(disc)
                session.flush()
                discrepancies.append(_to_dto(disc))

        local_with_ref = (
            session.execute(
                select(PaymentIntent).where(
                    PaymentIntent.tenant_id == tenant_id,
                    PaymentIntent.gateway_ref.isnot(None),
                )
            )
            .scalars()
            .all()
        )

        gw_refs = {gtx["gateway_ref"] for gtx in gateway_transactions}
        for pi in local_with_ref:
            if pi.gateway_ref and pi.gateway_ref not in gw_refs:
                disc = ReconciliationDiscrepancy(
                    tenant_id=tenant_id,
                    payment_intent_id=pi.id,
                    discrepancy_type="MISSING_REMOTE",
                    gateway_ref=pi.gateway_ref,
                    expected_amount=pi.amount,
                    expected_status=pi.status,
                    details={"payment_intent_id": str(pi.id)},
                )
                session.add(disc)
                session.flush()
                discrepancies.append(_to_dto(disc))

        if discrepancies:
            event_payload = {
                "tenant_id": tenant_id,
                "discrepancy_count": len(discrepancies),
                "types": list({d.discrepancy_type for d in discrepancies}),
                "auto_fixed_count": len(auto_fixed),
                "correlation_id": get_correlation_id(),
            }
            session.add(
                OutboxEvent(
                    tenant_id=tenant_id,
                    event_type="reconciliation.discrepancy_found",
                    aggregate_type="Reconciliation",
                    aggregate_id=str(uuid.uuid4()),
                    payload=event_payload,
                )
            )
            enqueue_webhook_deliveries(
                session,
                tenant_id,
                "reconciliation.discrepancy_found",
                event_payload,
            )

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "reconciliation.run",
        f"tenant:{tenant_id}",
        {
            "transaction_count": len(gateway_transactions),
            "discrepancy_count": len(discrepancies),
            "auto_fixed_count": len(auto_fixed),
        },
    )

    return discrepancies


def list_discrepancies(
    session: Session, tenant_id: str, resolved: bool | None = None
) -> list[DiscrepancyDTO]:
    q = select(ReconciliationDiscrepancy).where(ReconciliationDiscrepancy.tenant_id == tenant_id)
    if resolved is not None:
        q = q.where(ReconciliationDiscrepancy.resolved == resolved)
    q = q.order_by(ReconciliationDiscrepancy.created_at.desc()).limit(200)
    rows = session.execute(q).scalars().all()
    return [_to_dto(r) for r in rows]


def resolve_discrepancy(session: Session, tenant_id: str, disc_id: uuid.UUID) -> DiscrepancyDTO:
    with safe_begin(session):
        disc = session.execute(
            select(ReconciliationDiscrepancy).where(
                ReconciliationDiscrepancy.tenant_id == tenant_id,
                ReconciliationDiscrepancy.id == disc_id,
            )
        ).scalar_one_or_none()
        if not disc:
            from src.shared.problem import http_problem

            raise http_problem(
                404,
                "Not Found",
                "discrepancy not found",
                instance=f"/v1/reconciliation/{disc_id}",
            )
        disc.resolved = True

    _audit(
        session,
        tenant_id,
        get_subject() or "system",
        "discrepancy.resolved",
        f"discrepancy:{disc_id}",
        {"discrepancy_type": disc.discrepancy_type},
    )

    return _to_dto(disc)


def _to_dto(d: ReconciliationDiscrepancy) -> DiscrepancyDTO:
    return DiscrepancyDTO(
        id=str(d.id),
        tenant_id=str(d.tenant_id),
        payment_intent_id=str(d.payment_intent_id) if d.payment_intent_id else None,
        discrepancy_type=d.discrepancy_type,
        gateway_ref=d.gateway_ref,
        expected_amount=str(d.expected_amount) if d.expected_amount is not None else None,
        actual_amount=str(d.actual_amount) if d.actual_amount is not None else None,
        expected_status=d.expected_status,
        actual_status=d.actual_status,
        resolved=d.resolved,
        created_at=d.created_at.isoformat(),
    )


def _align_local_status(
    session: Session,
    tenant_id: str,
    pi: PaymentIntent,
    gateway_status: str,
    gateway_amount: Decimal,
) -> bool:
    """Alinha o estado local ao que o provedor **ja** reporta.

    Passa pelas operacoes de dominio em vez de atribuir ``pi.status``: e isso
    que garante que uma correcao para ``SETTLED`` tambem produza o lancamento
    contabil e o evento no outbox. A versao anterior atribuia o status direto e
    deixava pagamentos liquidados sem nenhum lancamento.

    A reconciliacao **nunca** inicia um efeito novo no provedor; ela so registra
    localmente um fato que o provedor confirma.
    """
    target = STRIPE_STATUS_TO_LOCAL.get(gateway_status)
    if not target or target == pi.status:
        return False
    if not settlement.can_transition(pi.status, target):
        log.info(
            "reconciliation: refusing to regress state",
            extra={
                "payment_intent_id": str(pi.id),
                "local_status": pi.status,
                "gateway_status": gateway_status,
                "would_be": target,
            },
        )
        return False

    before = pi.status
    if target == settlement.SETTLED:
        settlement.settle_payment(
            session,
            tenant_id,
            pi,
            source="reconciliation",
            amount=gateway_amount,
            instance="reconciliation",
        )
    elif target == settlement.VOIDED:
        settlement.void_payment(
            session, tenant_id, pi, source="reconciliation", instance="reconciliation"
        )
    elif target == settlement.FAILED:
        settlement.fail_payment(
            session,
            tenant_id,
            pi,
            source="reconciliation",
            error_code="reconciled",
            error_message=f"gateway reports {gateway_status}",
            instance="reconciliation",
        )
    elif target == settlement.AUTHORIZED:
        pi.status = settlement.AUTHORIZED
        pi.version = (pi.version or 0) + 1
        pi.updated_at = _utcnow()
    else:
        return False

    log.info(
        "reconciliation auto-fix applied",
        extra={
            "tenant_id": tenant_id,
            "payment_intent_id": str(pi.id),
            "old_status": before,
            "new_status": pi.status,
            "gateway_status": gateway_status,
        },
    )
    return True


def resolve_uncertain_operations(
    session: Session,
    tenant_id: str,
    gateway: Any,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fecha operacoes cujo desfecho remoto nao se conhece, por **consulta**.

    Este e o caminho de saida do estado ``UNCERTAIN``: quando a resposta de uma
    autorizacao ou captura nao chega, o desfecho so pode ser descoberto
    perguntando ao provedor — nunca emitindo a operacao de novo. A consulta usa
    a referencia que o provedor devolveu (quando houve uma) e nao produz efeito
    colateral, portanto pode ser repetida a vontade.

    Devolve um resumo por operacao, para a fila de revisao.
    """
    resolved: list[dict[str, Any]] = []
    operations = gateway_ops.list_uncertain_operations(session, tenant_id, limit=limit)

    for op in operations:
        pi = session.get(PaymentIntent, op.payment_intent_id) if op.payment_intent_id else None
        probe_ref = op.provider_ref or (pi.gateway_ref if pi else None)
        if not probe_ref:
            # Sem referencia nao ha o que consultar: a operacao pode nao ter
            # chegado a criar objeto no provedor. Fica na fila para analise.
            resolved.append(
                {
                    "operation_id": str(op.id),
                    "idempotency_key": op.idempotency_key,
                    "outcome": "no_reference_to_query",
                }
            )
            continue

        status_result = gateway_ops.run_gateway(gateway.get_status(probe_ref))

        if not status_result.success:
            resolved.append(
                {
                    "operation_id": str(op.id),
                    "idempotency_key": op.idempotency_key,
                    "outcome": "query_failed",
                    "error_code": status_result.error_code,
                }
            )
            continue

        remote = str(status_result.status)
        with safe_begin(session):
            if pi is not None:
                locked = settlement.load_for_update(
                    session, tenant_id, pi.id, instance="reconciliation"
                )
                if remote in ("CAPTURED", "AUTHORIZED") and remote == "CAPTURED":
                    settlement.settle_payment(
                        session,
                        tenant_id,
                        locked,
                        source="reconciliation",
                        instance="reconciliation",
                    )
                elif remote == "AUTHORIZED" and settlement.can_transition(
                    locked.status, settlement.AUTHORIZED
                ):
                    locked.status = settlement.AUTHORIZED
                    locked.version = (locked.version or 0) + 1
                    locked.updated_at = _utcnow()
                elif remote == "VOIDED":
                    settlement.void_payment(
                        session,
                        tenant_id,
                        locked,
                        source="reconciliation",
                        instance="reconciliation",
                    )
                elif remote in ("FAILED", "NOT_FOUND"):
                    settlement.fail_payment(
                        session,
                        tenant_id,
                        locked,
                        source="reconciliation",
                        error_code=(
                            "reconciled_not_found" if remote == "NOT_FOUND" else "reconciled_failed"
                        ),
                        error_message=f"gateway reports {remote}",
                        instance="reconciliation",
                    )
            gateway_ops.mark_operation_resolved(
                session,
                op.id,
                status=(
                    GatewayOperation.FAILED
                    if remote in ("FAILED", "NOT_FOUND")
                    else GatewayOperation.SUCCEEDED
                ),
                provider_ref=status_result.gateway_ref or op.provider_ref,
            )

        log.info(
            "uncertain operation resolved by query",
            extra={
                "operation_id": str(op.id),
                "idempotency_key": op.idempotency_key,
                "remote_status": remote,
            },
        )
        resolved.append(
            {
                "operation_id": str(op.id),
                "idempotency_key": op.idempotency_key,
                "outcome": "resolved",
                "remote_status": remote,
            }
        )

    return resolved
