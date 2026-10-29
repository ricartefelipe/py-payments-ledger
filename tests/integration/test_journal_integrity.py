"""Invariantes contabeis do journal, provadas contra PostgreSQL real.

Cada teste aqui corresponde a uma afirmacao que "soma zero" nao sustenta:
equilibrio por moeda, imutabilidade, deduplicacao por fato, reversao vinculada
e limite de estorno.
"""

from __future__ import annotations

import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from src.application.exceptions import LedgerImbalanceError
from src.application.journal import (
    CREDIT,
    DEBIT,
    ENTRY_REVERSAL,
    LineSpec,
    entry_total,
    post_entry,
    reverse_entry,
    reversed_total,
    validate_balanced_by_currency,
)
from src.infrastructure.db.models import LedgerEntry, LedgerLine, PaymentIntent
from tests.integration.conftest import new_session, random_ref


def _intent(session: Session, tenant_id: str, amount: str = "100.00", currency: str = "BRL"):
    pi = PaymentIntent(
        tenant_id=tenant_id,
        amount=Decimal(amount),
        currency=currency,
        status="AUTHORIZED",
        customer_ref=f"order:{uuid.uuid4().hex[:8]}",
        gateway_ref=random_ref(),
        gateway_provider="stripe",
    )
    session.add(pi)
    session.commit()
    return pi


def _pair(amount: str, currency: str = "BRL") -> list[LineSpec]:
    return [
        LineSpec(DEBIT, "CASH", Decimal(amount), currency),
        LineSpec(CREDIT, "REVENUE", Decimal(amount), currency),
    ]


# ---------------------------------------------------------------------------
# Equilibrio por moeda
# ---------------------------------------------------------------------------


def test_balanced_single_currency_is_accepted() -> None:
    validate_balanced_by_currency(_pair("100.00"), reference="t")


def test_unbalanced_single_currency_is_rejected() -> None:
    lines = [
        LineSpec(DEBIT, "CASH", Decimal("100.00"), "BRL"),
        LineSpec(CREDIT, "REVENUE", Decimal("90.00"), "BRL"),
    ]
    with pytest.raises(LedgerImbalanceError):
        validate_balanced_by_currency(lines, reference="t")


def test_cross_currency_entry_that_sums_to_zero_is_rejected() -> None:
    """O defeito central: a validacao antiga aceitava este lancamento.

    Debitar 100 USD e creditar 100 BRL soma zero no total e e contabilmente
    impossivel. O equilibrio tem de valer por moeda.
    """
    lines = [
        LineSpec(DEBIT, "CASH", Decimal("100.00"), "USD"),
        LineSpec(CREDIT, "REVENUE", Decimal("100.00"), "BRL"),
    ]
    with pytest.raises(LedgerImbalanceError) as excinfo:
        validate_balanced_by_currency(lines, reference="cross")
    assert "USD" in str(excinfo.value) or "BRL" in str(excinfo.value)


def test_multi_currency_entry_balanced_per_currency_is_accepted() -> None:
    """Multi-moeda e valido quando cada moeda fecha isoladamente."""
    lines = [
        *_pair("100.00", "BRL"),
        *_pair("20.00", "USD"),
    ]
    validate_balanced_by_currency(lines, reference="multi")


def test_line_amount_must_be_decimal() -> None:
    """Dinheiro nao entra no journal como float."""
    with pytest.raises(TypeError, match="Decimal"):
        LineSpec(DEBIT, "CASH", 100.00, "BRL")  # type: ignore[arg-type]


def test_post_entry_rejects_cross_currency(pg_session: Session, tenant_id: str) -> None:
    pi = _intent(pg_session, tenant_id)
    with pytest.raises(LedgerImbalanceError):
        post_entry(
            pg_session,
            tenant_id,
            fact_key=f"bad:{pi.id}",
            lines=[
                LineSpec(DEBIT, "CASH", Decimal("10.00"), "USD"),
                LineSpec(CREDIT, "REVENUE", Decimal("10.00"), "BRL"),
            ],
            payment_intent_id=pi.id,
        )
    pg_session.rollback()
    assert _entry_count(pg_session, tenant_id) == 0


def _entry_count(session: Session, tenant_id: str) -> int:
    return len(
        session.execute(select(LedgerEntry).where(LedgerEntry.tenant_id == tenant_id))
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Imutabilidade
# ---------------------------------------------------------------------------


def test_ledger_entry_cannot_be_updated(pg_session: Session, tenant_id: str) -> None:
    """A trigger do banco recusa UPDATE, venha de onde vier."""
    pi = _intent(pg_session, tenant_id)
    entry = post_entry(
        pg_session,
        tenant_id,
        fact_key=f"settle:{pi.id}",
        lines=_pair("100.00"),
        payment_intent_id=pi.id,
    )
    assert entry is not None
    pg_session.commit()
    entry_id = entry.id

    with pytest.raises(DatabaseError, match="append-only"):
        pg_session.execute(
            text("UPDATE ledger_entries SET entry_type = 'TAMPERED' WHERE id = :i"),
            {"i": entry_id},
        )
    pg_session.rollback()


def test_ledger_line_amount_cannot_be_updated(pg_session: Session, tenant_id: str) -> None:
    """Nem o caminho mais direto — alterar o valor da linha — e permitido."""
    pi = _intent(pg_session, tenant_id)
    entry = post_entry(
        pg_session,
        tenant_id,
        fact_key=f"settle:{pi.id}",
        lines=_pair("100.00"),
        payment_intent_id=pi.id,
    )
    assert entry is not None
    pg_session.commit()

    with pytest.raises(DatabaseError, match="append-only"):
        pg_session.execute(
            text("UPDATE ledger_lines SET amount = 999999 WHERE entry_id = :i"),
            {"i": entry.id},
        )
    pg_session.rollback()

    refreshed = (
        pg_session.execute(select(LedgerLine).where(LedgerLine.entry_id == entry.id))
        .scalars()
        .all()
    )
    assert all(Decimal(str(line.amount)) == Decimal("100.00") for line in refreshed)


def test_ledger_entry_cannot_be_deleted(pg_session: Session, tenant_id: str) -> None:
    pi = _intent(pg_session, tenant_id)
    entry = post_entry(
        pg_session,
        tenant_id,
        fact_key=f"settle:{pi.id}",
        lines=_pair("100.00"),
        payment_intent_id=pi.id,
    )
    assert entry is not None
    pg_session.commit()

    with pytest.raises(DatabaseError, match="append-only"):
        pg_session.execute(text("DELETE FROM ledger_entries WHERE id = :i"), {"i": entry.id})
    pg_session.rollback()


# ---------------------------------------------------------------------------
# Deduplicacao por fato
# ---------------------------------------------------------------------------


def test_same_fact_posted_twice_yields_one_entry(pg_session: Session, tenant_id: str) -> None:
    pi = _intent(pg_session, tenant_id)
    fact = f"settle:{pi.id}"

    first = post_entry(
        pg_session, tenant_id, fact_key=fact, lines=_pair("100.00"), payment_intent_id=pi.id
    )
    pg_session.commit()
    second = post_entry(
        pg_session, tenant_id, fact_key=fact, lines=_pair("100.00"), payment_intent_id=pi.id
    )
    pg_session.commit()

    assert first is not None
    assert second is None, "o segundo lancamento do mesmo fato deve ser ignorado"
    assert _entry_count(pg_session, tenant_id) == 1


def test_concurrent_sessions_posting_the_same_fact_yield_one_entry(
    pg_engine, pg_session: Session, tenant_id: str
) -> None:
    """Duas transacoes simultaneas: o banco decide, nao o SELECT previo.

    Este e o cenario do endpoint de captura competindo com o webhook do
    provedor. As duas threads sincronizam numa barreira para que ambas facam a
    verificacao previa antes de qualquer commit — e exatamente a corrida que a
    consulta previa nao cobre. O PostgreSQL bloqueia o segundo INSERT ate o
    primeiro commitar e depois recusa por unicidade; `post_entry` absorve esse
    conflito no SAVEPOINT e devolve None, sem abortar a transacao do chamador.
    """
    pi = _intent(pg_session, tenant_id)
    fact = f"settle:{pi.id}"
    pi_id = pi.id
    pg_session.commit()

    barrier = threading.Barrier(2, timeout=30)
    outcome: dict[str, object] = {}

    def worker(name: str) -> None:
        session = new_session(pg_engine)
        try:
            # Falha rapido em vez de pendurar a suite se a invariante regredir.
            session.execute(text("SET LOCAL lock_timeout = '20s'"))
            barrier.wait()
            entry = post_entry(
                session,
                tenant_id,
                fact_key=fact,
                lines=_pair("100.00"),
                payment_intent_id=pi_id,
            )
            session.commit()
            outcome[name] = "created" if entry is not None else "skipped"
        except Exception as exc:  # noqa: BLE001 - o teste classifica a falha
            session.rollback()
            outcome[name] = exc
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(n,), daemon=True) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive(), "thread travada: a deduplicacao por fato regrediu"

    assert sorted(str(v) for v in outcome.values()) == [
        "created",
        "skipped",
    ], f"exatamente uma das duas deve gravar; obtido: {outcome}"
    assert _entry_count(pg_session, tenant_id) == 1


def test_different_facts_coexist(pg_session: Session, tenant_id: str) -> None:
    pi = _intent(pg_session, tenant_id)
    assert post_entry(
        pg_session,
        tenant_id,
        fact_key=f"settle:{pi.id}",
        lines=_pair("100.00"),
        payment_intent_id=pi.id,
    )
    assert post_entry(
        pg_session,
        tenant_id,
        fact_key=f"fee:{pi.id}",
        lines=_pair("2.50"),
        payment_intent_id=pi.id,
    )
    pg_session.commit()
    assert _entry_count(pg_session, tenant_id) == 2


def test_same_fact_key_in_another_tenant_is_independent(
    pg_session: Session, tenant_id: str, other_tenant_id: str
) -> None:
    """A unicidade e por tenant: dois tenants podem ter o mesmo fact_key."""
    mine = _intent(pg_session, tenant_id)
    theirs = _intent(pg_session, other_tenant_id)
    assert post_entry(
        pg_session,
        tenant_id,
        fact_key="settle:shared",
        lines=_pair("100.00"),
        payment_intent_id=mine.id,
    )
    assert post_entry(
        pg_session,
        other_tenant_id,
        fact_key="settle:shared",
        lines=_pair("100.00"),
        payment_intent_id=theirs.id,
    )
    pg_session.commit()
    assert _entry_count(pg_session, tenant_id) == 1
    assert _entry_count(pg_session, other_tenant_id) == 1


# ---------------------------------------------------------------------------
# Reversao vinculada e limite
# ---------------------------------------------------------------------------


def test_reversal_is_linked_and_preserves_the_original(pg_session: Session, tenant_id: str) -> None:
    pi = _intent(pg_session, tenant_id)
    original = post_entry(
        pg_session,
        tenant_id,
        fact_key=f"settle:{pi.id}",
        lines=_pair("100.00"),
        payment_intent_id=pi.id,
    )
    assert original is not None
    pg_session.commit()

    reversal = reverse_entry(pg_session, tenant_id, original, fact_key=f"void:{pi.id}")
    assert reversal is not None
    pg_session.commit()

    assert reversal.entry_type == ENTRY_REVERSAL
    assert reversal.reverses_entry_id == original.id
    # O original continua intacto e consultavel.
    kept = pg_session.execute(select(LedgerEntry).where(LedgerEntry.id == original.id)).scalar_one()
    assert entry_total(kept) == Decimal("100.00")
    # As linhas sao espelhadas lado a lado.
    sides = {(line.side, line.account) for line in reversal.lines}
    assert sides == {(CREDIT, "CASH"), (DEBIT, "REVENUE")}
    # E o par soma zero por moeda.
    assert entry_total(kept) - entry_total(reversal) == Decimal("0.00")


def test_partial_reversal_is_proportional(pg_session: Session, tenant_id: str) -> None:
    pi = _intent(pg_session, tenant_id, amount="100.00")
    original = post_entry(
        pg_session,
        tenant_id,
        fact_key=f"settle:{pi.id}",
        lines=_pair("100.00"),
        payment_intent_id=pi.id,
    )
    assert original is not None
    pg_session.commit()

    partial = reverse_entry(
        pg_session,
        tenant_id,
        original,
        fact_key=f"refund:{pi.id}:1",
        amount=Decimal("30.00"),
    )
    assert partial is not None
    pg_session.commit()
    assert entry_total(partial) == Decimal("30.00")
    assert reversed_total(pg_session, tenant_id, original) == Decimal("30.00")


def test_successive_partial_reversals_cannot_exceed_the_original(
    pg_session: Session, tenant_id: str
) -> None:
    """Estornos somados nao podem passar do valor lancado."""
    pi = _intent(pg_session, tenant_id, amount="100.00")
    original = post_entry(
        pg_session,
        tenant_id,
        fact_key=f"settle:{pi.id}",
        lines=_pair("100.00"),
        payment_intent_id=pi.id,
    )
    assert original is not None
    pg_session.commit()

    reverse_entry(
        pg_session, tenant_id, original, fact_key=f"refund:{pi.id}:1", amount=Decimal("60.00")
    )
    pg_session.commit()
    reverse_entry(
        pg_session, tenant_id, original, fact_key=f"refund:{pi.id}:2", amount=Decimal("40.00")
    )
    pg_session.commit()

    assert reversed_total(pg_session, tenant_id, original) == Decimal("100.00")
    # O limite por lancamento individual tambem vale.
    with pytest.raises(Exception, match="exceeds"):
        reverse_entry(
            pg_session,
            tenant_id,
            original,
            fact_key=f"refund:{pi.id}:3",
            amount=Decimal("100.01"),
        )
    pg_session.rollback()


def test_reversal_amount_must_be_positive(pg_session: Session, tenant_id: str) -> None:
    pi = _intent(pg_session, tenant_id)
    original = post_entry(
        pg_session,
        tenant_id,
        fact_key=f"settle:{pi.id}",
        lines=_pair("100.00"),
        payment_intent_id=pi.id,
    )
    assert original is not None
    pg_session.commit()
    with pytest.raises(Exception, match="must be > 0"):
        reverse_entry(pg_session, tenant_id, original, fact_key=f"bad:{pi.id}", amount=Decimal("0"))
    pg_session.rollback()


def test_money_keeps_decimal_precision_end_to_end(pg_session: Session, tenant_id: str) -> None:
    """Um valor com centavos "difíceis" em binário sobrevive ao lançamento.

    `150.55` não tem representação binária exata. Com `amount: float` nas
    assinaturas e `float(link.amount)` nos chamadores internos, o valor podia
    chegar ao razão alterado. O tipo agora é `Decimal` de ponta a ponta.
    """
    pi = _intent(pg_session, tenant_id, amount="150.55")
    entry = post_entry(
        pg_session,
        tenant_id,
        fact_key=f"settle:{pi.id}",
        lines=_pair("150.55"),
        payment_intent_id=pi.id,
    )
    assert entry is not None
    pg_session.commit()
    pg_session.expire_all()

    stored = pg_session.get(PaymentIntent, pi.id)
    assert stored is not None
    assert Decimal(str(stored.amount)) == Decimal("150.55")

    reloaded = pg_session.execute(
        select(LedgerEntry).where(LedgerEntry.id == entry.id)
    ).scalar_one()
    assert entry_total(reloaded) == Decimal("150.55")
    for line in reloaded.lines:
        assert isinstance(line.amount, Decimal)
        assert str(line.amount) == "150.55"
