from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application import gateway_ops, settlement
from src.application.payments import confirm_payment_intent, create_payment_intent
from src.infrastructure.db.models import GatewayConfig, GatewayOperation, RecurringCharge
from src.infrastructure.db.session import safe_begin
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)

VALID_INTERVALS = ("monthly", "yearly")
VALID_CURRENCIES = ("BRL", "USD", "EUR")
_INSTANCE = "recurring"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _advance_next_charge(current: datetime, interval: str) -> datetime:
    if interval == "monthly":
        return current + relativedelta(months=1)
    if interval == "yearly":
        return current + relativedelta(years=1)
    raise ValueError(f"unknown interval: {interval}")


def _next_future_charge(current: datetime, interval: str, now: datetime) -> datetime:
    """Próxima data depois de `now`, mantendo o dia da cobrança: ciclos perdidos não geram cobrança."""
    upcoming = _advance_next_charge(current, interval)
    while upcoming <= now:
        upcoming = _advance_next_charge(upcoming, interval)
    return upcoming


class RecurringChargeDTO(BaseModel):
    id: str
    tenant_id: str
    description: str
    amount_cents: int
    currency: str
    interval: str
    next_charge_at: str
    status: str
    gateway_customer_ref: str | None = None
    created_at: str
    updated_at: str


def _to_dto(rc: RecurringCharge) -> RecurringChargeDTO:
    return RecurringChargeDTO(
        id=str(rc.id),
        tenant_id=str(rc.tenant_id),
        description=rc.description,
        amount_cents=rc.amount_cents,
        currency=rc.currency,
        interval=rc.interval,
        next_charge_at=rc.next_charge_at.isoformat(),
        status=rc.status,
        gateway_customer_ref=rc.gateway_customer_ref,
        created_at=rc.created_at.isoformat(),
        updated_at=rc.updated_at.isoformat(),
    )


def create_recurring_charge(
    session: Session,
    tenant_id: str,
    description: str,
    amount_cents: int,
    currency: str,
    interval: str,
    gateway_customer_ref: str | None = None,
) -> RecurringChargeDTO:
    if amount_cents <= 0:
        raise http_problem(
            400, "Bad Request", "amount_cents must be > 0", instance="/v1/recurring-charges"
        )
    if currency not in VALID_CURRENCIES:
        raise http_problem(
            400, "Bad Request", "unsupported currency", instance="/v1/recurring-charges"
        )
    if interval not in VALID_INTERVALS:
        raise http_problem(
            400,
            "Bad Request",
            f"interval must be one of {VALID_INTERVALS}",
            instance="/v1/recurring-charges",
        )

    now = _utcnow()
    next_charge = _advance_next_charge(now, interval)

    with safe_begin(session):
        rc = RecurringCharge(
            tenant_id=tenant_id,
            description=description,
            amount_cents=amount_cents,
            currency=currency,
            interval=interval,
            next_charge_at=next_charge,
            status="ACTIVE",
            gateway_customer_ref=gateway_customer_ref,
            created_at=now,
            updated_at=now,
        )
        session.add(rc)
        session.flush()

    log.info("recurring charge created", extra={"charge_id": str(rc.id), "tenant_id": tenant_id})
    return _to_dto(rc)


def _get_charge_or_404(session: Session, charge_id: uuid.UUID, tenant_id: str) -> RecurringCharge:
    rc = session.execute(
        select(RecurringCharge).where(
            RecurringCharge.id == charge_id,
            RecurringCharge.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if not rc:
        raise http_problem(
            404,
            "Not Found",
            "recurring charge not found",
            instance=f"/v1/recurring-charges/{charge_id}",
        )
    return rc


def pause_recurring_charge(
    session: Session, charge_id: uuid.UUID, tenant_id: str
) -> RecurringChargeDTO:
    with safe_begin(session):
        rc = _get_charge_or_404(session, charge_id, tenant_id)
        if rc.status != "ACTIVE":
            raise http_problem(
                409,
                "Conflict",
                f"cannot pause charge with status {rc.status}",
                instance=f"/v1/recurring-charges/{charge_id}/pause",
            )
        rc.status = "PAUSED"
        rc.updated_at = _utcnow()

    return _to_dto(rc)


def resume_recurring_charge(
    session: Session, charge_id: uuid.UUID, tenant_id: str
) -> RecurringChargeDTO:
    with safe_begin(session):
        rc = _get_charge_or_404(session, charge_id, tenant_id)
        if rc.status != "PAUSED":
            raise http_problem(
                409,
                "Conflict",
                f"cannot resume charge with status {rc.status}",
                instance=f"/v1/recurring-charges/{charge_id}/resume",
            )
        rc.status = "ACTIVE"
        rc.updated_at = _utcnow()

    return _to_dto(rc)


def cancel_recurring_charge(
    session: Session, charge_id: uuid.UUID, tenant_id: str
) -> RecurringChargeDTO:
    with safe_begin(session):
        rc = _get_charge_or_404(session, charge_id, tenant_id)
        if rc.status == "CANCELLED":
            return _to_dto(rc)
        rc.status = "CANCELLED"
        rc.updated_at = _utcnow()

    return _to_dto(rc)


def list_recurring_charges(session: Session, tenant_id: str) -> list[RecurringChargeDTO]:
    rows = (
        session.execute(
            select(RecurringCharge)
            .where(RecurringCharge.tenant_id == tenant_id)
            .order_by(RecurringCharge.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [_to_dto(r) for r in rows]


def _cycle_key(rc: RecurringCharge) -> str:
    """Identidade da obrigação: um ciclo de uma cobrança, estável entre tentativas."""
    return f"recurring-{rc.id}-{rc.next_charge_at.isoformat()}"


def _cycle_operation(session: Session, tenant_id: str, key: str) -> GatewayOperation | None:
    return session.execute(
        select(GatewayOperation).where(
            GatewayOperation.tenant_id == tenant_id,
            GatewayOperation.idempotency_key == f"authorize:{tenant_id}:{key}",
        )
    ).scalar_one_or_none()


def _gateway_for_tenant(
    session: Session, tenant_id: str, currency: str, gateway: Any, settings: Any
) -> tuple[Any, str | None]:
    """O mesmo gateway que a captura vai usar: a autorização não pode ir para outro provedor."""
    if settings is None:
        return gateway, None
    from src.infrastructure.gateway.factory import get_gateway_for_tenant

    selected = get_gateway_for_tenant(session, tenant_id, settings, currency=currency)
    config = session.execute(
        select(GatewayConfig).where(
            GatewayConfig.tenant_id == tenant_id,
            GatewayConfig.is_default.is_(True),
        )
    ).scalar_one_or_none()
    return selected, config.provider if config else None


def _close_cycle(session: Session, tenant_id: str, op: GatewayOperation, gateway: Any) -> bool:
    """Leva o pagamento do ciclo adiante. Verdadeiro quando o ciclo pode ser encerrado.

    Vale para a primeira tentativa e para a retomada. Nenhum caminho autoriza de novo: a
    autorização só acontece em ``create_payment_intent``, com a chave do ciclo. O ``confirm``
    aqui sempre encontra a referência do provedor e apenas avança o estado.
    """
    pid = op.payment_intent_id
    if pid is None:  # pragma: no cover - a operação do ciclo nasce ligada ao pagamento
        raise RuntimeError(f"gateway operation {op.id} has no payment intent")
    with safe_begin(session):
        pi = settlement.load_for_update(session, tenant_id, pid, instance=_INSTANCE)
        if pi.status != settlement.CREATED:
            # Em REVIEW o desfecho no provedor é desconhecido: o ciclo só fecha depois da
            # reconciliação, porque a data seguinte daria a impressão de cobrança resolvida.
            return pi.status not in (settlement.AUTHORIZING, settlement.REVIEW)

        if op.status == GatewayOperation.SUCCEEDED:
            gateway_ops.attach_gateway_ref(session, pi, op.provider_ref or "")
        elif op.status == GatewayOperation.FAILED:
            settlement.fail_payment(
                session,
                tenant_id,
                pi,
                source="recurring",
                error_code=op.error_code or "",
                error_message=op.error_message or "",
                instance=_INSTANCE,
            )
            return True
        else:
            age = _utcnow() - (op.updated_at or op.created_at)
            if op.status != GatewayOperation.PENDING or age >= gateway_ops.IN_FLIGHT_TIMEOUT:
                # A tentativa anterior morreu sem resposta, ou a resposta não chegou. Autorizar
                # de novo poderia cobrar duas vezes: a saída é a fila de revisão.
                settlement.mark_for_review(
                    session,
                    tenant_id,
                    pi,
                    source="recurring",
                    reason=f"authorization {op.idempotency_key} is {op.status}",
                    instance=_INSTANCE,
                )
            return False

    session.commit()
    confirm_payment_intent(session, tenant_id, pid, gateway=gateway)
    return True


def _charge_cycle(session: Session, rc: RecurringCharge, gateway: Any, settings: Any) -> bool:
    """Cobra o ciclo vencido, retomando a tentativa anterior se ela existir."""
    tenant_id = str(rc.tenant_id)
    key = _cycle_key(rc)
    gw, provider = _gateway_for_tenant(session, tenant_id, rc.currency, gateway, settings)

    if _cycle_operation(session, tenant_id, key) is None:
        # Unidade minima (centavos) convertida com quantize explicito; o
        # valor segue como Decimal ate o lancamento. Antes era passado por
        # `float()`, que reintroduzia arredondamento binario em dinheiro.
        amount = (Decimal(rc.amount_cents) / Decimal(100)).quantize(Decimal("0.01"))
        try:
            create_payment_intent(
                session,
                tenant_id,
                amount,
                rc.currency,
                rc.gateway_customer_ref or f"recurring:{rc.id}",
                gateway=gw,
                idempotency_key=key,
                gateway_provider=provider,
            )
        except HTTPException as exc:
            # Recusa e incerteza ficam registradas na operação: quem decide é `_close_cycle`.
            if exc.status_code != 502:
                raise

    session.commit()
    op = _cycle_operation(session, tenant_id, key)
    if op is None:
        raise RuntimeError(f"recurring cycle {key} has no gateway operation")
    return _close_cycle(session, tenant_id, op, gw)


def process_due_charges(session: Session, gateway: Any = None, settings: Any = None) -> int:
    """Cobra as recorrências vencidas até a autorização e avança a data de cada uma.

    A autorização gera ``payment.authorized``, e o consumidor do worker captura e lança no
    ledger, como em qualquer outro pagamento. Sem gateway não há operação para deduplicar a
    tentativa, então a cobrança exige um: é o que o worker sempre fornece.
    """
    if gateway is None:
        raise ValueError("recurring charges require a payment gateway")

    now = _utcnow()
    processed = 0

    due_ids = (
        session.execute(
            select(RecurringCharge.id).where(
                RecurringCharge.status == "ACTIVE",
                RecurringCharge.next_charge_at <= now,
            )
        )
        .scalars()
        .all()
    )
    session.rollback()

    for charge_id in due_ids:
        try:
            # Lock por cobrança: cada commit do fluxo de pagamento solta os locks abertos, e um
            # lock único na lista inteira deixaria as seguintes livres para outro worker.
            rc = session.execute(
                select(RecurringCharge)
                .where(
                    RecurringCharge.id == charge_id,
                    RecurringCharge.status == "ACTIVE",
                    RecurringCharge.next_charge_at <= now,
                )
                .with_for_update(skip_locked=True)
            ).scalar_one_or_none()
            if rc is None:
                session.rollback()
                continue

            due_at = rc.next_charge_at
            interval = rc.interval
            tenant_id = str(rc.tenant_id)

            if not _charge_cycle(session, rc, gateway, settings):
                session.commit()
                log.warning(
                    "recurring charge waiting for a known outcome",
                    extra={"charge_id": str(charge_id), "tenant_id": tenant_id},
                )
                continue

            with safe_begin(session):
                # Com o worker parado, cobrar cada ciclo vencido geraria uma cobrança por minuto ao voltar.
                rc.next_charge_at = _next_future_charge(due_at, interval, now)
                rc.updated_at = _utcnow()
            session.commit()

            processed += 1
            log.info(
                "recurring charge processed",
                extra={"charge_id": str(charge_id), "tenant_id": tenant_id},
            )
        except Exception:
            session.rollback()
            log.exception(
                "failed to process recurring charge",
                extra={"charge_id": str(charge_id)},
            )

    return processed
