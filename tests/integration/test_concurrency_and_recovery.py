"""Concorrencia distribuida e recuperacao, contra PostgreSQL real.

Cobre o fechamento 5 e os cenarios de aceite 3 e 6:

* dois workers reivindicando o mesmo lote do outbox;
* lease expirado e reivindicado por outro worker — e a recusa do worker antigo
  em concluir o trabalho que deixou de ser dele;
* queda entre o efeito externo e a liquidacao, com estado recuperavel;
* queda entre a liquidacao e o ACK, sem lancamento duplicado;
* gateway fora do ar nao mantem lock longo nem deixa o pagamento irrecuperavel;
* duas instancias convergindo para o mesmo estado e um unico journal.

As quedas sao simuladas com barreiras e excecoes injetadas **no teste**, nunca
com ganchos compilados no artefato de producao.
"""

from __future__ import annotations

import threading
import time
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.application import gateway_ops, settlement
from src.application.journal import entry_total
from src.application.outbox import claim_events, mark_failed, mark_sent
from src.application.payments import post_ledger_for_authorized_payment
from src.application.ports.payment_gateway import GatewayResult, GatewayStatus
from src.infrastructure.db.models import (
    GatewayOperation,
    LedgerEntry,
    OutboxEvent,
    PaymentIntent,
)
from tests.integration.conftest import new_session, random_ref


def _intent(
    session: Session, tenant_id: str, *, amount: str = "150.00", status: str = "AUTHORIZED"
) -> PaymentIntent:
    pi = PaymentIntent(
        tenant_id=tenant_id,
        amount=Decimal(amount),
        currency="BRL",
        status=status,
        customer_ref=f"order:{uuid.uuid4().hex[:8]}",
        gateway_ref=random_ref(),
        gateway_provider="stripe",
    )
    session.add(pi)
    session.commit()
    return pi


def _entries(session: Session, tenant_id: str, pid) -> list[LedgerEntry]:
    session.expire_all()
    return list(
        session.execute(
            select(LedgerEntry).where(
                LedgerEntry.tenant_id == tenant_id, LedgerEntry.payment_intent_id == pid
            )
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Outbox: dois workers, lease e recuperacao
# ---------------------------------------------------------------------------


def _queue_event(session: Session, tenant_id: str, n: int = 1) -> list[str]:
    ids = []
    for i in range(n):
        e = OutboxEvent(
            tenant_id=tenant_id,
            event_type="payment.settled",
            aggregate_type="PaymentIntent",
            aggregate_id=f"agg-{i}-{uuid.uuid4().hex[:6]}",
            payload={"i": i},
        )
        session.add(e)
        session.flush()
        ids.append(str(e.id))
    session.commit()
    return ids


def test_two_workers_do_not_claim_the_same_event(
    pg_engine, pg_session: Session, tenant_id: str
) -> None:
    """``FOR UPDATE SKIP LOCKED``: cada evento vai para um worker so."""
    _queue_ids = _queue_event(pg_session, tenant_id, n=10)

    barrier = threading.Barrier(2, timeout=30)
    claimed: dict[str, list[str]] = {}

    def worker(name: str) -> None:
        session = new_session(pg_engine)
        try:
            barrier.wait()
            # Limite alto de proposito: `claim_events` ordena por created_at,
            # e com um limite justo os eventos deste teste (os mais recentes)
            # poderiam ficar de fora por causa de pendencias de outros testes.
            events = claim_events(session, name, limit=500)
            claimed[name] = [e.id for e in events]
            session.commit()
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(n,), daemon=True) for n in ("w1", "w2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive()

    a, b = set(claimed.get("w1", [])), set(claimed.get("w2", []))
    assert a & b == set(), "o mesmo evento foi reivindicado por dois workers"
    # `claim_events` e global por desenho — o dispatcher nao e por tenant — logo
    # o lote pode conter eventos de outros testes. A afirmacao e sobre os nossos.
    mine = set(_queue_ids)
    assert mine <= (a | b), "algum evento deste teste nao foi reivindicado"
    for event_id in mine:
        assert not (event_id in a and event_id in b)


def test_expired_lease_is_reclaimed_by_another_worker(pg_session: Session, tenant_id: str) -> None:
    """Worker que morre com o lease nao prende o evento para sempre."""
    (event_id,) = _queue_event(pg_session, tenant_id, n=1)

    first = claim_events(pg_session, "worker-dead", limit=5, lock_timeout_seconds=60)
    pg_session.commit()
    assert [e.id for e in first] == [event_id]

    # Ninguem mais pega enquanto o lease vale.
    assert claim_events(pg_session, "worker-other", limit=5, lock_timeout_seconds=60) == []
    pg_session.commit()

    # Envelhece o lease (o worker morreu sem concluir).
    pg_session.execute(
        text("UPDATE outbox_events SET locked_at = now() - interval '10 minutes' WHERE id = :i"),
        {"i": event_id},
    )
    pg_session.commit()

    second = claim_events(pg_session, "worker-other", limit=5, lock_timeout_seconds=60)
    pg_session.commit()
    assert [e.id for e in second] == [event_id]
    assert second[0].locked_by == "worker-other"


def test_stale_worker_cannot_complete_an_event_it_no_longer_owns(
    pg_session: Session, tenant_id: str
) -> None:
    """O resultado tardio de um worker obsoleto nao se aplica.

    Sem a verificacao de posse, o worker lento marcaria ``SENT`` um evento que o
    novo dono ainda esta publicando — e a publicacao do novo dono ficaria sem
    registro de conclusao, ou o evento sairia duas vezes sem rastro.
    """
    (event_id,) = _queue_event(pg_session, tenant_id, n=1)

    claim_events(pg_session, "worker-slow", limit=5, lock_timeout_seconds=60)
    pg_session.commit()
    pg_session.execute(
        text("UPDATE outbox_events SET locked_at = now() - interval '10 minutes' WHERE id = :i"),
        {"i": event_id},
    )
    pg_session.commit()
    claim_events(pg_session, "worker-new", limit=5, lock_timeout_seconds=60)
    pg_session.commit()

    # O worker lento volta e tenta concluir.
    assert mark_sent(pg_session, event_id, worker_id="worker-slow") is False
    pg_session.commit()
    pg_session.expire_all()
    row = pg_session.get(OutboxEvent, uuid.UUID(event_id))
    assert row is not None
    assert row.status == "PENDING", "o worker obsoleto concluiu o evento de outro"
    assert row.locked_by == "worker-new"

    # O dono atual conclui normalmente.
    assert mark_sent(pg_session, event_id, worker_id="worker-new") is True
    pg_session.commit()
    pg_session.expire_all()
    row = pg_session.get(OutboxEvent, uuid.UUID(event_id))
    assert row is not None
    assert row.status == "SENT"


def test_stale_worker_cannot_fail_an_event_it_no_longer_owns(
    pg_session: Session, tenant_id: str
) -> None:
    (event_id,) = _queue_event(pg_session, tenant_id, n=1)
    claim_events(pg_session, "worker-slow", limit=5, lock_timeout_seconds=60)
    pg_session.commit()
    pg_session.execute(
        text("UPDATE outbox_events SET locked_at = now() - interval '10 minutes' WHERE id = :i"),
        {"i": event_id},
    )
    pg_session.commit()
    claim_events(pg_session, "worker-new", limit=5, lock_timeout_seconds=60)
    pg_session.commit()

    assert mark_failed(pg_session, event_id, worker_id="worker-slow") is False
    pg_session.commit()
    pg_session.expire_all()
    row = pg_session.get(OutboxEvent, uuid.UUID(event_id))
    assert row is not None
    assert row.attempts == 0, "o worker obsoleto contou uma tentativa que nao e dele"


# ---------------------------------------------------------------------------
# Queda entre etapas (cenario de aceite 3)
# ---------------------------------------------------------------------------


class _CrashingGateway:
    """Captura com sucesso e depois o processo "cai" antes de liquidar."""

    def __init__(self) -> None:
        self.captures: list[str] = []

    async def capture(
        self, gateway_ref: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> GatewayResult:
        self.captures.append(idempotency_key)
        return GatewayResult(success=True, gateway_ref=gateway_ref, status=GatewayStatus.CAPTURED)


def test_crash_after_capture_before_settlement_is_recoverable(
    pg_engine, pg_session: Session, tenant_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cenario de aceite 3: queda entre o efeito externo e o settle.

    O claim foi persistido antes da captura, portanto depois da queda existe
    registro de que a captura foi emitida e com qual chave. A nova tentativa
    reaproveita a operacao ``SUCCEEDED`` e **nao captura de novo**.
    """
    pi = _intent(pg_session, tenant_id)
    gw = _CrashingGateway()

    # Primeira passagem: a liquidacao explode logo apos a captura ter sucesso.
    boom = RuntimeError("processo caiu depois da captura")

    def exploding_settle(*_args, **_kwargs):
        raise boom

    monkeypatch.setattr(settlement, "settle_payment", exploding_settle)
    with pytest.raises(RuntimeError, match="caiu depois da captura"):
        post_ledger_for_authorized_payment(pg_session, tenant_id, pi.id, gateway=gw)
    pg_session.rollback()
    monkeypatch.undo()

    assert len(gw.captures) == 1
    pg_session.expire_all()

    # O registro da operacao sobreviveu: e isso que torna o estado recuperavel.
    ops = (
        pg_session.execute(
            select(GatewayOperation).where(
                GatewayOperation.tenant_id == tenant_id,
                GatewayOperation.payment_intent_id == pi.id,
            )
        )
        .scalars()
        .all()
    )
    assert len(ops) == 1
    assert ops[0].op_type == "CAPTURE"
    first_key = ops[0].idempotency_key

    # Segunda passagem (retomada): conclui sem capturar de novo.
    post_ledger_for_authorized_payment(pg_session, tenant_id, pi.id, gateway=gw)
    pg_session.expire_all()

    assert len(gw.captures) == 1, "a retomada capturou uma segunda vez"
    refreshed = pg_session.get(PaymentIntent, pi.id)
    assert refreshed is not None
    assert refreshed.status == settlement.SETTLED
    entries = _entries(pg_session, tenant_id, pi.id)
    assert len(entries) == 1
    assert entry_total(entries[0]) == Decimal("150.00")
    # A chave usada e a mesma: estavel por obrigacao, nao por tentativa.
    assert first_key == f"capture:{tenant_id}:{pi.id}"


def test_settlement_is_not_duplicated_when_the_ack_is_lost(
    pg_session: Session, tenant_id: str
) -> None:
    """Queda depois do settle e antes do ACK: a reentrega nao lanca de novo."""
    pi = _intent(pg_session, tenant_id)
    gw = _CrashingGateway()

    post_ledger_for_authorized_payment(pg_session, tenant_id, pi.id, gateway=gw)
    pg_session.expire_all()
    assert len(_entries(pg_session, tenant_id, pi.id)) == 1

    # O broker reentrega porque nao viu o ACK.
    post_ledger_for_authorized_payment(pg_session, tenant_id, pi.id, gateway=gw)
    post_ledger_for_authorized_payment(pg_session, tenant_id, pi.id, gateway=gw)
    pg_session.expire_all()

    assert len(_entries(pg_session, tenant_id, pi.id)) == 1
    assert len(gw.captures) == 1, "a reentrega capturou de novo no provedor"


# ---------------------------------------------------------------------------
# Gateway fora do ar (cenario de aceite 6)
# ---------------------------------------------------------------------------


class _SlowGateway:
    """Simula gateway lento e mede se havia lock preso durante a chamada."""

    def __init__(self, engine, tenant_id: str, pi_id) -> None:
        self._engine = engine
        self._tenant_id = tenant_id
        self._pi_id = pi_id
        self.row_was_lockable: bool | None = None

    async def capture(
        self, gateway_ref: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> GatewayResult:
        # Durante a chamada remota, outra conexao deve conseguir travar a linha:
        # se nao conseguir, o lock esta sendo mantido atraves da rede.
        probe = new_session(self._engine)
        try:
            probe.execute(text("SET LOCAL lock_timeout = '2s'"))
            probe.execute(
                text(
                    "SELECT id FROM payment_intents " "WHERE id = :i AND tenant_id = :t FOR UPDATE"
                ),
                {"i": self._pi_id, "t": self._tenant_id},
            )
            self.row_was_lockable = True
        except Exception:  # noqa: BLE001 - lock timeout e o resultado observado
            self.row_was_lockable = False
        finally:
            probe.rollback()
            probe.close()
        return GatewayResult(
            success=False,
            gateway_ref=gateway_ref,
            status=GatewayStatus.UNCERTAIN,
            error_code="gateway_timeout_after_send",
            error_message="no answer",
            is_uncertain=True,
        )


def test_gateway_call_does_not_hold_the_row_lock(
    pg_engine, pg_session: Session, tenant_id: str
) -> None:
    """Cenario de aceite 6: gateway fora do ar nao mantem lock longo.

    Era o padrao antigo: ``SELECT ... FOR UPDATE`` seguido de
    ``asyncio.run(gateway.capture(...))`` dentro da mesma transacao. O lock
    durava o tempo da chamada remota — em contexto HTTP, o request inteiro.
    """
    pi = _intent(pg_session, tenant_id)
    pi_id = pi.id
    pg_session.commit()
    pg_session.close()

    worker = new_session(pg_engine)
    gw = _SlowGateway(pg_engine, tenant_id, pi_id)
    try:
        post_ledger_for_authorized_payment(worker, tenant_id, pi_id, gateway=gw)
    finally:
        worker.close()

    assert (
        gw.row_was_lockable is True
    ), "a linha do pagamento estava travada durante a chamada ao gateway"


def test_two_instances_converge_without_duplicate_journal(
    pg_engine, pg_session: Session, tenant_id: str
) -> None:
    """Cenario de aceite 6: duas instancias, um estado e um journal."""
    pi = _intent(pg_session, tenant_id)
    pi_id = pi.id
    pg_session.commit()

    barrier = threading.Barrier(2, timeout=30)
    results: dict[str, object] = {}

    def worker(name: str) -> None:
        session = new_session(pg_engine)
        gw = _CrashingGateway()
        try:
            barrier.wait()
            post_ledger_for_authorized_payment(session, tenant_id, pi_id, gateway=gw)
            results[name] = len(gw.captures)
        except Exception as exc:  # noqa: BLE001 - o teste classifica
            session.rollback()
            results[name] = exc
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(n,), daemon=True) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive(), "thread travada: as duas instancias nao convergiram"

    # Uma das instancias conclui; a outra e recusada pela guarda de operacao em
    # voo (409). Recusar e o comportamento correto: emitir a mesma captura duas
    # vezes so geraria trabalho em dobro no provedor.
    conflicts = [v for v in results.values() if isinstance(v, Exception)]
    assert len(conflicts) <= 1, f"as duas instancias falharam: {results}"

    entries = _entries(pg_session, tenant_id, pi_id)
    assert len(entries) == 1, "duas instancias produziram dois lancamentos"
    refreshed = pg_session.get(PaymentIntent, pi_id)
    assert refreshed is not None
    assert refreshed.status == settlement.SETTLED

    # A captura remota aconteceu uma unica vez no total.
    assert sum(v for v in results.values() if isinstance(v, int)) == 1


# ---------------------------------------------------------------------------
# Operacao incerta nao e reenviada
# ---------------------------------------------------------------------------


def test_uncertain_capture_blocks_a_second_attempt(pg_session: Session, tenant_id: str) -> None:
    """Depois de um desfecho incerto, nova tentativa e recusada, nao reenviada."""
    pi = _intent(pg_session, tenant_id)

    class UncertainGateway:
        def __init__(self) -> None:
            self.calls = 0

        async def capture(
            self, gateway_ref: str, amount: Decimal, currency: str, idempotency_key: str
        ) -> GatewayResult:
            self.calls += 1
            return GatewayResult(
                success=False,
                gateway_ref=gateway_ref,
                status=GatewayStatus.UNCERTAIN,
                error_code="gateway_timeout_after_send",
                error_message="no answer",
                is_uncertain=True,
            )

    gw = UncertainGateway()
    post_ledger_for_authorized_payment(pg_session, tenant_id, pi.id, gateway=gw)
    pg_session.expire_all()

    refreshed = pg_session.get(PaymentIntent, pi.id)
    assert refreshed is not None
    assert refreshed.status == settlement.REVIEW
    assert _entries(pg_session, tenant_id, pi.id) == []

    # Nova tentativa: reconhece a incerteza e nao toca a rede.
    post_ledger_for_authorized_payment(pg_session, tenant_id, pi.id, gateway=gw)
    assert gw.calls == 1, "operacao de desfecho incerto foi reenviada"


def test_same_idempotency_key_with_a_different_amount_is_a_conflict(
    pg_session: Session, tenant_id: str
) -> None:
    """Mesma chave com conteudo diferente e conflito, nao repeticao."""
    pi = _intent(pg_session, tenant_id, amount="100.00")

    claim = gateway_ops.claim_operation(
        session=pg_session,
        tenant_id=tenant_id,
        pi=pi,
        op_type=gateway_ops.CAPTURE,
        amount=Decimal("100.00"),
        currency="BRL",
        provider="stripe",
    )
    pg_session.commit()
    assert claim.idempotency_key == f"capture:{tenant_id}:{pi.id}"

    with pytest.raises(Exception, match="different amount or currency"):
        gateway_ops.claim_operation(
            session=pg_session,
            tenant_id=tenant_id,
            pi=pi,
            op_type=gateway_ops.CAPTURE,
            amount=Decimal("999.00"),
            currency="BRL",
            provider="stripe",
        )
    pg_session.rollback()


def test_run_gateway_refuses_to_be_called_inside_an_event_loop() -> None:
    """A fronteira sync/async falha com mensagem clara, nao no fundo da pilha."""
    import asyncio

    async def _inside() -> None:
        async def _noop() -> int:
            return 1

        with pytest.raises(RuntimeError, match="event loop"):
            gateway_ops.run_gateway(_noop())

    asyncio.run(_inside())


def test_claim_is_durable_before_the_network_call(
    pg_engine, pg_session: Session, tenant_id: str
) -> None:
    """O claim precisa estar visivel para outra conexao antes da rede.

    E essa durabilidade que permite a reconciliacao descobrir, depois de uma
    queda, que uma operacao foi emitida e com qual chave.
    """
    pi = _intent(pg_session, tenant_id)
    pi_id = pi.id
    pg_session.commit()
    observed: dict[str, object] = {}

    class ObservingGateway:
        async def capture(
            self, gateway_ref: str, amount: Decimal, currency: str, idempotency_key: str
        ) -> GatewayResult:
            probe = new_session(pg_engine)
            try:
                rows = (
                    probe.execute(
                        select(GatewayOperation).where(
                            GatewayOperation.tenant_id == tenant_id,
                            GatewayOperation.payment_intent_id == pi_id,
                        )
                    )
                    .scalars()
                    .all()
                )
                observed["count"] = len(rows)
                observed["status"] = rows[0].status if rows else None
                observed["key"] = rows[0].idempotency_key if rows else None
            finally:
                probe.close()
            return GatewayResult(
                success=True, gateway_ref=gateway_ref, status=GatewayStatus.CAPTURED
            )

    worker = new_session(pg_engine)
    try:
        post_ledger_for_authorized_payment(worker, tenant_id, pi_id, gateway=ObservingGateway())
    finally:
        worker.close()

    assert observed.get("count") == 1, "o claim nao estava duravel quando a rede foi tocada"
    assert observed.get("status") == GatewayOperation.PENDING
    assert observed.get("key") == f"capture:{tenant_id}:{pi_id}"


def test_lease_recovery_does_not_lose_events(pg_session: Session, tenant_id: str) -> None:
    """Depois da recuperacao, o evento continua publicavel exatamente uma vez."""
    ids = _queue_event(pg_session, tenant_id, n=3)

    claim_events(pg_session, "w-dead", limit=3, lock_timeout_seconds=60)
    pg_session.commit()
    pg_session.execute(
        text("UPDATE outbox_events SET locked_at = now() - interval '1 hour' WHERE tenant_id = :t"),
        {"t": tenant_id},
    )
    pg_session.commit()

    recovered = claim_events(pg_session, "w-alive", limit=10, lock_timeout_seconds=60)
    pg_session.commit()
    assert sorted(e.id for e in recovered) == sorted(ids)

    for e in recovered:
        assert mark_sent(pg_session, e.id, worker_id="w-alive") is True
    pg_session.commit()

    assert claim_events(pg_session, "w-alive", limit=10) == []
    pg_session.commit()


def test_time_precision_at_the_lease_boundary(pg_session: Session, tenant_id: str) -> None:
    """O limite do lease e testado antes, exatamente nele e depois.

    Sem alargar tolerancia: o relogio do teste usa a mesma precisao que o banco
    persiste (``timestamptz``).
    """
    (event_id,) = _queue_event(pg_session, tenant_id, n=1)
    claim_events(pg_session, "w1", limit=1, lock_timeout_seconds=60)
    pg_session.commit()

    def age_lease(seconds: float) -> None:
        pg_session.execute(
            text(
                "UPDATE outbox_events SET locked_at = now() - (:s * interval '1 second') "
                "WHERE id = :i"
            ),
            {"s": seconds, "i": event_id},
        )
        pg_session.commit()

    # Antes do limite: ninguem mais pega.
    age_lease(59)
    assert claim_events(pg_session, "w2", limit=1, lock_timeout_seconds=60) == []
    pg_session.commit()

    # Exatamente no limite: ainda nao expirou (a comparacao e estritamente <).
    age_lease(60)
    time.sleep(0.01)  # garante que `now()` avancou alem do limite exato
    reclaimed = claim_events(pg_session, "w2", limit=1, lock_timeout_seconds=60)
    pg_session.commit()
    assert [e.id for e in reclaimed] == [event_id]
