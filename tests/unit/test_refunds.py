from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


def _make_pi(
    status: str = "SETTLED",
    amount: Decimal = Decimal("100.00"),
    currency: str = "BRL",
    tenant_id: str = "t1",
) -> MagicMock:
    pi = MagicMock()
    pi.id = uuid.uuid4()
    pi.tenant_id = tenant_id
    pi.amount = amount
    pi.currency = currency
    pi.status = status
    pi.customer_ref = "test"
    pi.gateway_ref = None
    pi.created_at = datetime.now(timezone.utc)
    pi.updated_at = datetime.now(timezone.utc)
    return pi


class TestCreateRefund:
    def test_refund_not_found_raises_404(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None
        session.begin.return_value.__enter__ = MagicMock(return_value=None)
        session.begin.return_value.__exit__ = MagicMock(return_value=False)

        from src.application.refunds import create_refund
        with pytest.raises(Exception) as exc_info:
            create_refund(session, "t1", uuid.uuid4(), Decimal("10.00"))
        assert exc_info.value.status_code == 404

    def test_refund_wrong_status_raises_409(self) -> None:
        pi = _make_pi(status="CREATED")
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = pi
        session.begin.return_value.__enter__ = MagicMock(return_value=None)
        session.begin.return_value.__exit__ = MagicMock(return_value=False)

        from src.application.refunds import create_refund
        with pytest.raises(Exception) as exc_info:
            create_refund(session, "t1", pi.id, Decimal("10.00"))
        assert exc_info.value.status_code == 409

    def test_refund_negative_amount_raises_400(self) -> None:
        pi = _make_pi()
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = pi
        session.begin.return_value.__enter__ = MagicMock(return_value=None)
        session.begin.return_value.__exit__ = MagicMock(return_value=False)

        from src.application.refunds import create_refund
        with pytest.raises(Exception) as exc_info:
            create_refund(session, "t1", pi.id, Decimal("-10.00"))
        assert exc_info.value.status_code == 400


class TestRefundDTO:
    def test_refund_dto_fields(self) -> None:
        from src.application.refunds import RefundDTO
        dto = RefundDTO(
            id="abc",
            payment_intent_id="def",
            amount="50.00",
            reason="test",
            status="COMPLETED",
            gateway_ref=None,
            created_at="2026-02-23T10:00:00+00:00",
        )
        assert dto.amount == "50.00"
        assert dto.status == "COMPLETED"
        assert dto.reason == "test"
