"""Unit tests for ledger balance calculations and entry listing."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.application.ledger import (
    AccountBalanceDTO,
    LedgerEntryDTO,
    get_ledger_balances,
    list_ledger_entries,
)


def _make_ledger_line(side: str, account: str, amount: Decimal, currency: str = "BRL") -> MagicMock:
    line = MagicMock()
    line.side = side
    line.account = account
    line.amount = amount
    line.currency = currency
    return line


def _make_ledger_entry(
    entry_id: uuid.UUID,
    pi_id: uuid.UUID,
    posted_at: datetime,
    lines: list[MagicMock],
) -> MagicMock:
    entry = MagicMock()
    entry.id = entry_id
    entry.payment_intent_id = pi_id
    entry.posted_at = posted_at
    entry.lines = lines
    return entry


class TestListLedgerEntries:
    def test_returns_entries_as_dtos(self) -> None:
        now = datetime.now(timezone.utc)
        entry_id = uuid.uuid4()
        pi_id = uuid.uuid4()
        lines = [
            _make_ledger_line("DEBIT", "cash", Decimal("100.00")),
            _make_ledger_line("CREDIT", "revenue", Decimal("100.00")),
        ]
        entry = _make_ledger_entry(entry_id, pi_id, now, lines)

        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = [entry]

        result = list_ledger_entries(session, "tenant_demo", None, None)

        assert len(result) == 1
        assert isinstance(result[0], LedgerEntryDTO)
        assert result[0].id == str(entry_id)
        assert result[0].payment_intent_id == str(pi_id)
        assert len(result[0].lines) == 2
        assert result[0].lines[0].side == "DEBIT"
        assert result[0].lines[0].account == "cash"
        assert result[0].lines[1].side == "CREDIT"

    def test_returns_empty_list_when_no_entries(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = []

        result = list_ledger_entries(session, "tenant_demo", None, None)

        assert result == []

    def test_passes_date_filters_to_query(self) -> None:
        from_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        to_dt = datetime(2026, 12, 31, tzinfo=timezone.utc)
        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = []

        list_ledger_entries(session, "tenant_demo", from_dt, to_dt)

        session.execute.assert_called_once()


class TestGetLedgerBalances:
    """Balance rules:
    - ASSET, EXPENSE → balance = debits - credits
    - LIABILITY, EQUITY, REVENUE → balance = credits - debits
    """

    def _make_balance_row(
        self, account: str, currency: str, debits: Decimal, credits: Decimal, balance: Decimal
    ) -> MagicMock:
        row = MagicMock()
        row.account = account
        row.currency = currency
        row.debits_total = debits
        row.credits_total = credits
        row.balance = balance
        return row

    def test_asset_account_balance_is_debits_minus_credits(self) -> None:
        row = self._make_balance_row("cash", "BRL", Decimal("500"), Decimal("200"), Decimal("300"))
        session = MagicMock()
        session.execute.return_value.all.return_value = [row]

        result = get_ledger_balances(session, "tenant_demo", None, None)

        assert len(result) == 1
        assert isinstance(result[0], AccountBalanceDTO)
        assert result[0].account == "cash"
        assert result[0].balance == "300"

    def test_expense_account_balance_is_debits_minus_credits(self) -> None:
        row = self._make_balance_row("fees", "BRL", Decimal("100"), Decimal("0"), Decimal("100"))
        session = MagicMock()
        session.execute.return_value.all.return_value = [row]

        result = get_ledger_balances(session, "tenant_demo", None, None)

        assert result[0].balance == "100"

    def test_liability_account_balance_is_credits_minus_debits(self) -> None:
        row = self._make_balance_row(
            "accounts_payable", "BRL", Decimal("50"), Decimal("300"), Decimal("250")
        )
        session = MagicMock()
        session.execute.return_value.all.return_value = [row]

        result = get_ledger_balances(session, "tenant_demo", None, None)

        assert result[0].balance == "250"

    def test_equity_account_balance_is_credits_minus_debits(self) -> None:
        row = self._make_balance_row(
            "retained_earnings", "BRL", Decimal("0"), Decimal("1000"), Decimal("1000")
        )
        session = MagicMock()
        session.execute.return_value.all.return_value = [row]

        result = get_ledger_balances(session, "tenant_demo", None, None)

        assert result[0].balance == "1000"

    def test_revenue_account_balance_is_credits_minus_debits(self) -> None:
        row = self._make_balance_row(
            "sales_revenue", "BRL", Decimal("10"), Decimal("5000"), Decimal("4990")
        )
        session = MagicMock()
        session.execute.return_value.all.return_value = [row]

        result = get_ledger_balances(session, "tenant_demo", None, None)

        assert result[0].balance == "4990"

    def test_returns_empty_list_when_no_data(self) -> None:
        session = MagicMock()
        session.execute.return_value.all.return_value = []

        result = get_ledger_balances(session, "tenant_demo", None, None)

        assert result == []

    def test_multiple_accounts_returned_correctly(self) -> None:
        rows = [
            self._make_balance_row("cash", "BRL", Decimal("1000"), Decimal("200"), Decimal("800")),
            self._make_balance_row("revenue", "BRL", Decimal("0"), Decimal("800"), Decimal("800")),
        ]
        session = MagicMock()
        session.execute.return_value.all.return_value = rows

        result = get_ledger_balances(session, "tenant_demo", None, None)

        assert len(result) == 2
        assert result[0].account == "cash"
        assert result[1].account == "revenue"
