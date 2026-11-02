"""Processamento de cobranças recorrentes vencidas, sem banco."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.application import recurring
from src.application.recurring import _next_future_charge, process_due_charges


def _charge(next_charge_at: datetime, interval: str = "monthly") -> SimpleNamespace:
    return SimpleNamespace(
        id="c3000000-0000-0000-0000-000000000001",
        tenant_id="00000000-0000-0000-0000-000000000001",
        amount_cents=29900,
        currency="BRL",
        interval=interval,
        status="ACTIVE",
        next_charge_at=next_charge_at,
        gateway_customer_ref=None,
        updated_at=next_charge_at,
    )


def _session_with_due(*charges: SimpleNamespace) -> MagicMock:
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = [c.id for c in charges]
    session.execute.return_value.scalar_one_or_none.side_effect = list(charges)
    return session


def _process(session: MagicMock, now: datetime) -> tuple[int, MagicMock]:
    with (
        patch.object(recurring, "_utcnow", return_value=now),
        patch.object(recurring, "_charge_cycle", return_value=True) as cycle,
        patch.object(recurring, "safe_begin", return_value=nullcontext()),
    ):
        return process_due_charges(session, gateway=object()), cycle


def test_ciclos_atrasados_geram_uma_unica_cobranca() -> None:
    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    charge = _charge(datetime(2026, 1, 16, tzinfo=UTC))

    processed, create = _process(_session_with_due(charge), now)

    assert processed == 1
    create.assert_called_once()
    assert charge.next_charge_at == datetime(2026, 11, 16, tzinfo=UTC)
    assert charge.next_charge_at > now


def test_cobranca_em_dia_avanca_um_ciclo() -> None:
    now = datetime(2026, 11, 16, 0, 1, tzinfo=UTC)
    charge = _charge(datetime(2026, 11, 16, tzinfo=UTC))

    processed, create = _process(_session_with_due(charge), now)

    assert processed == 1
    create.assert_called_once()
    assert charge.next_charge_at == datetime(2026, 12, 16, tzinfo=UTC)


def test_chave_de_idempotencia_usa_o_vencimento_da_cobranca() -> None:
    due = datetime(2026, 1, 16, tzinfo=UTC)
    charge = _charge(due)

    assert recurring._cycle_key(charge) == f"recurring-{charge.id}-{due.isoformat()}"


def test_sem_gateway_nao_cobra() -> None:
    session = _session_with_due(_charge(datetime(2026, 1, 16, tzinfo=UTC)))

    with pytest.raises(ValueError):
        process_due_charges(session)

    session.execute.assert_not_called()


def test_ciclo_sem_desfecho_nao_avanca_a_data() -> None:
    due = datetime(2026, 1, 16, tzinfo=UTC)
    charge = _charge(due)
    with (
        patch.object(recurring, "_utcnow", return_value=datetime(2026, 11, 2, tzinfo=UTC)),
        patch.object(recurring, "_charge_cycle", return_value=False),
        patch.object(recurring, "safe_begin", return_value=nullcontext()),
    ):
        processed = process_due_charges(_session_with_due(charge), gateway=object())

    assert processed == 0
    assert charge.next_charge_at == due


def test_falha_em_uma_cobranca_nao_impede_a_seguinte() -> None:
    due = datetime(2026, 1, 16, tzinfo=UTC)
    first, second = _charge(due), _charge(due)
    second.id = "c3000000-0000-0000-0000-000000000002"
    with (
        patch.object(recurring, "_utcnow", return_value=datetime(2026, 11, 2, tzinfo=UTC)),
        patch.object(recurring, "_charge_cycle", side_effect=[RuntimeError("gateway"), True]),
        patch.object(recurring, "safe_begin", return_value=nullcontext()),
    ):
        processed = process_due_charges(_session_with_due(first, second), gateway=object())

    assert processed == 1
    assert first.next_charge_at == due
    assert second.next_charge_at > due


def test_falha_na_cobranca_nao_avanca_a_data() -> None:
    due = datetime(2026, 1, 16, tzinfo=UTC)
    charge = _charge(due)
    with (
        patch.object(recurring, "_utcnow", return_value=datetime(2026, 11, 2, tzinfo=UTC)),
        patch.object(recurring, "_charge_cycle", side_effect=RuntimeError("gateway")),
        patch.object(recurring, "safe_begin", return_value=nullcontext()),
    ):
        processed = process_due_charges(_session_with_due(charge), gateway=object())

    assert processed == 0
    assert charge.next_charge_at == due


def test_proxima_data_anual_pula_anos_perdidos() -> None:
    now = datetime(2026, 11, 2, tzinfo=UTC)
    assert _next_future_charge(datetime(2023, 3, 1, tzinfo=UTC), "yearly", now) == datetime(
        2027, 3, 1, tzinfo=UTC
    )


def test_data_igual_a_agora_nao_conta_como_futura() -> None:
    now = datetime(2026, 11, 2, tzinfo=UTC)
    assert _next_future_charge(now - timedelta(days=30), "monthly", now) > now
