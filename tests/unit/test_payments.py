"""Unit tests for payment intent create/confirm."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.application.payments import (
    PaymentIntentDTO,
    confirm_payment_intent,
    create_payment_intent,
)
from src.infrastructure.db.models import PaymentIntent


def _mock_pi(
    id: str = "550e8400-e29b-41d4-a716-446655440000",
    tenant_id: str = "tenant_demo",
    amount: float = 100.0,
    currency: str = "BRL",
    status: str = "CREATED",
    customer_ref: str = "CUST-1",
) -> PaymentIntent:
    pi = MagicMock(spec=PaymentIntent)
    pi.id = id
    pi.tenant_id = tenant_id
    pi.amount = Decimal(str(amount))
    pi.currency = currency
    pi.status = status
    pi.customer_ref = customer_ref
    pi.created_at = MagicMock()
    pi.created_at.isoformat.return_value = "2026-01-01T00:00:00"
    pi.updated_at = MagicMock()
    pi.updated_at.isoformat.return_value = "2026-01-01T00:00:00"
    return pi


@pytest.fixture
def mock_session() -> MagicMock:
    s = MagicMock()
    s.begin.return_value.__enter__ = MagicMock(return_value=s)
    s.begin.return_value.__exit__ = MagicMock(return_value=None)
    return s


def test_create_payment_intent_returns_dto(mock_session: MagicMock) -> None:
    with patch("src.application.payments.OutboxEvent"):
        dto = create_payment_intent(
            mock_session, "tenant_demo", 50.0, "BRL", "CUST-001"
        )
    assert isinstance(dto, PaymentIntentDTO)
    assert dto.amount == "50.0"
    assert dto.currency == "BRL"
    assert dto.status == "CREATED"
    assert dto.customer_ref == "CUST-001"


def test_create_payment_intent_invalid_amount(mock_session: MagicMock) -> None:
    with pytest.raises(Exception):  # http_problem raises HTTPException
        create_payment_intent(mock_session, "tenant_demo", 0, "BRL", "x")


def test_create_payment_intent_invalid_currency(mock_session: MagicMock) -> None:
    with pytest.raises(Exception):
        create_payment_intent(mock_session, "tenant_demo", 10, "XXX", "x")


def test_confirm_payment_intent_updates_status(mock_session: MagicMock) -> None:
    import uuid

    pi = _mock_pi(status="CREATED")
    mock_session.execute.return_value.scalar_one_or_none.return_value = pi
    mock_session.begin.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_session.begin.return_value.__exit__ = MagicMock(return_value=None)

    with patch("src.application.payments.OutboxEvent"):
        dto = confirm_payment_intent(
            mock_session, "tenant_demo", uuid.UUID(pi.id)
        )
    assert dto.status == "AUTHORIZED"

