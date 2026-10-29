"""Conversa com o gateway: claim duravel, rede fora do lock, resultado incerto.

O padrao antigo era:

    with safe_begin(session):
        pi = select(...).with_for_update()       # <- lock na linha
        gw = asyncio.run(gateway.capture(...))  # <- rede, ainda com o lock
        ...                                     # <- lanca e muda status

Isso tem tres problemas distintos:

1. **Lock durante a rede.** O lock da linha fica retido pelo tempo da chamada
   remota. Em contexto HTTP o ``safe_begin`` nem commita no fim do bloco (quem
   commita e a dependencia da sessao), portanto o lock dura o request inteiro.
   Gateway lento ou fora do ar vira contencao no banco.

2. **Efeito remoto sem registro local.** Se a captura tem sucesso e a transacao
   local falha depois (erro de banco, lancamento invalido, queda do processo), o
   dinheiro foi capturado no provedor e localmente nao ha nada — nem sequer o
   registro de que a operacao foi tentada.

3. **Timeout tratado como recusa.** ``not result.success`` levava a ``502`` e
   rollback. Uma nova tentativa do cliente vinha com chave de idempotencia nova
   e emitia uma segunda autorizacao.

O desenho aqui separa o ciclo em tres passos, cada um com a sua transacao curta:

    claim = claim_operation(...)   # tx curta: registra a intencao e libera o lock
    result = run_gateway(...)      # rede, sem nenhuma transacao aberta
    finalize_operation(...)        # tx curta: CAS por version + journal + outbox

A chave de idempotencia e **deduzida da obrigacao**, nao gerada por tentativa:
duas tentativas da mesma captura usam a mesma chave e alcancam a mesma operacao
no provedor. Se o desfecho e incerto, a operacao fica ``UNCERTAIN`` e a saida e
consulta — nunca uma operacao nova.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.application import settlement
from src.application.ports.payment_gateway import GatewayResult, GatewayStatus
from src.infrastructure.db.models import GatewayOperation, PaymentIntent
from src.infrastructure.db.session import safe_begin
from src.shared.logging import get_logger
from src.shared.problem import http_problem

log = get_logger(__name__)

AUTHORIZE = "AUTHORIZE"
CAPTURE = "CAPTURE"
VOID = "VOID"
REFUND = "REFUND"

#: Estado transitorio adotado durante cada operacao remota. Serve para que um
#: segundo processo veja que alguem ja esta conduzindo esta operacao.
_IN_FLIGHT_STATUS = {
    AUTHORIZE: settlement.AUTHORIZING,
    CAPTURE: settlement.CAPTURING,
}

#: Por quanto tempo uma operacao ``PENDING`` e considerada "em voo".
#:
#: Antes desse prazo, uma segunda tentativa e recusada: ha outra instancia
#: conduzindo a mesma operacao, e emitir de novo so geraria trabalho em dobro no
#: provedor. Depois dele, assume-se que a tentativa anterior morreu sem
#: reportar, e a operacao pode ser retomada — com a **mesma** chave de
#: idempotencia, que e o que impede efeito duplicado.
IN_FLIGHT_TIMEOUT = timedelta(seconds=30)


class OperationInFlightError(Exception):
    """Outra instancia esta conduzindo esta operacao agora."""

    def __init__(self, operation: GatewayOperation):
        self.operation = operation
        super().__init__(
            f"operation {operation.op_type} for payment {operation.payment_intent_id} "
            "is already in flight"
        )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def run_gateway[T](coro: Coroutine[Any, Any, T]) -> T:
    """Executa uma coroutine de gateway a partir de codigo sincrono.

    Existe para dar um unico ponto a essa fronteira e uma mensagem clara quando
    ela e cruzada errado. ``asyncio.run`` espalhado pelo dominio falhava com
    ``RuntimeError: asyncio.run() cannot be called from a running event loop``
    no fundo da pilha quando chamado de um endpoint ``async def``.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    coro.close()
    raise RuntimeError(
        "run_gateway() foi chamado de dentro de um event loop. O caminho "
        "sincrono do dominio nao pode ser usado a partir de um endpoint "
        "`async def`; use o endpoint sincrono ou `await` o adapter diretamente."
    )


def operation_key(tenant_id: str, payment_intent_id: Any, op_type: str, suffix: str = "") -> str:
    """Chave de idempotencia deduzida da obrigacao, nao da tentativa.

    Estavel por (tenant, pagamento, operacao): um reenvio depois de timeout
    alcanca a mesma operacao no provedor em vez de criar outra. ``suffix``
    distingue operacoes legitimamente repetiveis sobre o mesmo pagamento, como
    estornos parciais sucessivos.
    """
    base = f"{op_type.lower()}:{tenant_id}:{payment_intent_id}"
    return f"{base}:{suffix}" if suffix else base


def fingerprint(
    tenant_id: str, payment_intent_id: Any, op_type: str, amount: Decimal, currency: str
) -> str:
    """Digest do conteudo da operacao.

    Mesma chave com conteudo diferente e conflito, nao repeticao: evita que um
    segundo pedido com outro valor passe por idempotente.
    """
    material = f"{tenant_id}|{payment_intent_id}|{op_type}|{amount}|{currency.upper()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OperationClaim:
    """Intencao registrada de executar uma operacao remota."""

    operation_id: Any
    tenant_id: str
    payment_intent_id: Any
    op_type: str
    idempotency_key: str
    fingerprint: str
    amount: Decimal
    currency: str
    gateway_ref: str | None
    provider: str
    #: ``version`` do pagamento no momento do claim. A finalizacao so se aplica
    #: se a versao nao mudou — e o que impede um worker obsoleto de concluir.
    claimed_version: int
    #: Quando verdadeiro, a operacao ja tem desfecho conhecido e nao deve ser
    #: reenviada; ``existing_result`` carrega esse desfecho.
    already_resolved: bool = False
    existing_status: str | None = None
    existing_ref: str | None = None


class UncertainOperationError(Exception):
    """Existe uma operacao em aberto cujo desfecho nao se conhece.

    Reenviar e proibido: pode duplicar o efeito. A saida e consultar o provedor
    com a mesma chave (``resolve_uncertain_operation``).
    """

    def __init__(self, operation: GatewayOperation):
        self.operation = operation
        super().__init__(
            f"operation {operation.op_type} for payment {operation.payment_intent_id} "
            f"is {operation.status}; resolve it by querying the provider with "
            f"idempotency key {operation.idempotency_key}"
        )


def claim_operation(
    session: Session,
    tenant_id: str,
    pi: PaymentIntent,
    op_type: str,
    *,
    amount: Decimal,
    currency: str,
    provider: str,
    suffix: str = "",
    idempotency_key: str | None = None,
    allowed_from: frozenset[str] | None = None,
    apply_transitional: bool = True,
    instance: str = "gateway",
) -> OperationClaim:
    """Registra a intencao de executar a operacao e devolve o claim.

    Deve ser chamado com ``pi`` carregado via ``FOR UPDATE``. Ao retornar, o
    chamador precisa **commitar** antes de tocar a rede: e esse commit que torna
    a intencao duravel e libera o lock.

    ``idempotency_key`` permite ancorar a operacao em uma obrigacao externa em
    vez do pagamento local. O consumer de pedidos usa isso: a chave e
    ``charge:<tenant>:<order_id>``, estavel por pedido. Assim, se duas entregas
    da mesma mensagem criarem dois PaymentIntents locais, apenas uma consegue
    registrar a autorizacao — a unicidade ``(tenant_id, idempotency_key)``
    recusa a segunda, e o pedido nao e cobrado duas vezes.
    """
    if allowed_from is not None and pi.status not in allowed_from:
        raise http_problem(
            409,
            "Conflict",
            f"cannot {op_type.lower()} payment with status {pi.status}",
            instance=instance,
        )

    key = idempotency_key or operation_key(tenant_id, pi.id, op_type, suffix)
    fp = fingerprint(tenant_id, pi.id, op_type, amount, currency)

    existing = session.execute(
        select(GatewayOperation).where(
            GatewayOperation.tenant_id == tenant_id,
            GatewayOperation.idempotency_key == key,
        )
    ).scalar_one_or_none()

    if existing is not None:
        if existing.fingerprint != fp:
            log.error(
                "idempotency key reused with different content",
                extra={
                    "tenant_id": tenant_id,
                    "idempotency_key": key,
                    "stored_fingerprint": existing.fingerprint,
                    "request_fingerprint": fp,
                },
            )
            raise http_problem(
                409,
                "Conflict",
                "this operation was already requested with a different amount or currency",
                instance=instance,
            )
        if existing.status == GatewayOperation.SUCCEEDED:
            log.info(
                "operation already succeeded; not sending again",
                extra={"idempotency_key": key, "provider_ref": existing.provider_ref},
            )
            return OperationClaim(
                operation_id=existing.id,
                tenant_id=tenant_id,
                payment_intent_id=pi.id,
                op_type=op_type,
                idempotency_key=key,
                fingerprint=fp,
                amount=amount,
                currency=currency,
                gateway_ref=pi.gateway_ref,
                provider=provider,
                claimed_version=pi.version or 0,
                already_resolved=True,
                existing_status=existing.status,
                existing_ref=existing.provider_ref,
            )
        if existing.status == GatewayOperation.UNCERTAIN:
            # Nao reenviar: o provedor pode ter aceitado a primeira.
            raise UncertainOperationError(existing)
        if existing.status == GatewayOperation.PENDING:
            age = _utcnow() - (existing.updated_at or existing.created_at)
            if age < IN_FLIGHT_TIMEOUT:
                # Outra instancia esta conduzindo esta operacao agora mesmo.
                raise OperationInFlightError(existing)
            # Passado o prazo, a tentativa anterior e considerada morta. A
            # retomada usa a mesma chave: o provedor reconhece e nao repete o
            # efeito.
            log.warning(
                "resuming a stale PENDING operation; the previous attempt never reported back",
                extra={
                    "idempotency_key": key,
                    "attempts": existing.attempts,
                    "age_seconds": int(age.total_seconds()),
                },
            )
        existing.attempts += 1
        existing.status = GatewayOperation.PENDING
        existing.updated_at = _utcnow()
        operation = existing
    else:
        operation = GatewayOperation(
            tenant_id=tenant_id,
            payment_intent_id=pi.id,
            op_type=op_type,
            idempotency_key=key,
            fingerprint=fp,
            provider=provider,
            status=GatewayOperation.PENDING,
            amount=amount,
            currency=currency.upper(),
            attempts=1,
        )
        session.add(operation)
        session.flush()

    # `apply_transitional=False` para a autorizacao feita **dentro** da criacao:
    # ali o estado publico apos a operacao e `CREATED` (o cliente confirma em
    # seguida), e adotar `AUTHORIZING` faria o `confirm` recusar com 409. A
    # protecao contra operacao duplicada nao depende deste estado: ela vem da
    # unicidade de `(tenant_id, idempotency_key)`.
    transitional = _IN_FLIGHT_STATUS.get(op_type) if apply_transitional else None
    if transitional and settlement.can_transition(pi.status, transitional):
        pi.status = transitional
        pi.version = (pi.version or 0) + 1
        pi.updated_at = _utcnow()

    log.info(
        "gateway operation claimed",
        extra={
            "tenant_id": tenant_id,
            "payment_intent_id": str(pi.id),
            "op_type": op_type,
            "idempotency_key": key,
            "payment_status": pi.status,
        },
    )
    return OperationClaim(
        operation_id=operation.id,
        tenant_id=tenant_id,
        payment_intent_id=pi.id,
        op_type=op_type,
        idempotency_key=key,
        fingerprint=fp,
        amount=amount,
        currency=currency.upper(),
        gateway_ref=pi.gateway_ref,
        provider=provider,
        claimed_version=pi.version or 0,
    )


def record_result(
    session: Session, claim: OperationClaim, result: GatewayResult
) -> GatewayOperation:
    """Grava o desfecho da operacao remota, distinguindo recusa de incerteza."""
    operation = session.get(GatewayOperation, claim.operation_id)
    if operation is None:  # pragma: no cover - o claim acabou de criar a linha
        raise RuntimeError(f"gateway operation {claim.operation_id} disappeared")

    if result.success:
        operation.status = GatewayOperation.SUCCEEDED
        operation.provider_ref = result.gateway_ref or operation.provider_ref
        operation.error_code = ""
        operation.error_message = ""
    elif result.is_uncertain or result.status == GatewayStatus.UNCERTAIN:
        operation.status = GatewayOperation.UNCERTAIN
        operation.error_code = result.error_code
        operation.error_message = result.error_message[:1024]
    else:
        operation.status = GatewayOperation.FAILED
        operation.error_code = result.error_code
        operation.error_message = result.error_message[:1024]

    operation.updated_at = _utcnow()
    log.info(
        "gateway operation result recorded",
        extra={
            "idempotency_key": operation.idempotency_key,
            "status": operation.status,
            "error_code": operation.error_code or "",
        },
    )
    return operation


def record_result_durably(
    session: Session, claim: OperationClaim, result: GatewayResult
) -> GatewayOperation:
    """Grava o desfecho da operacao **em transacao propria**, antes de liquidar.

    E a diferenca entre um estado recuperavel e um irrecuperavel. Se o desfecho
    fosse gravado na mesma transacao da liquidacao, uma falha ali desfaria o
    registro: a captura teria acontecido no provedor e localmente nao haveria
    nem a anotacao de que ela foi emitida. A retomada reenviaria a operacao.

    Com o registro commitado em separado, a retomada encontra
    ``SUCCEEDED`` e conclui **sem tocar a rede**.
    """
    with safe_begin(session):
        operation = record_result(session, claim, result)
    if session.in_transaction():
        session.commit()
    return operation


def claim_still_valid(session: Session, claim: OperationClaim) -> PaymentIntent:
    """Recarrega o pagamento com lock e confirma que o claim nao foi superado.

    Se a ``version`` mudou, outro caminho ja finalizou este pagamento enquanto a
    chamada remota estava em curso. O resultado tardio nao pode se aplicar.
    """
    pi = settlement.load_for_update(
        session, claim.tenant_id, claim.payment_intent_id, instance="gateway"
    )
    return pi


def version_unchanged(pi: PaymentIntent, claim: OperationClaim) -> bool:
    return (pi.version or 0) == claim.claimed_version


def release_claim(session: Session, claim: OperationClaim, pi: PaymentIntent, target: str) -> None:
    """Devolve o pagamento a um estado estavel apos falha definitiva."""
    if pi.status == target:
        return
    if settlement.can_transition(pi.status, target):
        pi.status = target
        pi.version = (pi.version or 0) + 1
        pi.updated_at = _utcnow()
        log.info(
            "claim released",
            extra={"payment_intent_id": str(pi.id), "status": target, "op": claim.op_type},
        )


def attach_gateway_ref(session: Session, pi: PaymentIntent, gateway_ref: str) -> None:
    """Grava a referencia do provedor (o indice cego acompanha no flush)."""
    if gateway_ref and pi.gateway_ref != gateway_ref:
        pi.gateway_ref = gateway_ref
        pi.updated_at = _utcnow()


def list_uncertain_operations(
    session: Session, tenant_id: str, *, limit: int = 100
) -> list[GatewayOperation]:
    """Fila de revisao: operacoes cujo desfecho remoto nao se conhece."""
    return list(
        session.execute(
            select(GatewayOperation)
            .where(
                GatewayOperation.tenant_id == tenant_id,
                GatewayOperation.status.in_([GatewayOperation.UNCERTAIN, GatewayOperation.PENDING]),
            )
            .order_by(GatewayOperation.created_at.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def mark_operation_resolved(
    session: Session, operation_id: Any, *, status: str, provider_ref: str | None = None
) -> None:
    """Fecha uma operacao incerta depois da consulta ao provedor."""
    session.execute(
        update(GatewayOperation)
        .where(GatewayOperation.id == operation_id)
        .values(status=status, provider_ref=provider_ref, updated_at=_utcnow())
    )
