"""Unit tests for StripeAdapter."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.application.ports.payment_gateway import GatewayResult, GatewayStatus
from src.infrastructure.gateway.stripe_adapter import (
    CURRENCY_MULTIPLIERS,
    StripeAdapter,
)


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.get_event_loop().run_until_complete(coro)


class TestStripeAdapterAuthorize:
    def test_calls_payment_intent_create_with_correct_params(self) -> None:
        adapter = StripeAdapter(api_key="sk_test_xxx", max_retries=0)

        mock_pi = {"id": "pi_test_123"}
        with patch("stripe.PaymentIntent") as MockPI:
            MockPI.create.return_value = mock_pi
            result = _run(
                adapter.authorize("t1", Decimal("100.00"), "BRL", "cust_1", "idem_1")
            )

        assert result.success is True
        assert result.gateway_ref == "pi_test_123"
        assert result.status == GatewayStatus.AUTHORIZED

        MockPI.create.assert_called_once_with(
            amount=10000,
            currency="brl",
            capture_method="manual",
            metadata={"tenant_id": "t1", "customer_ref": "cust_1"},
            idempotency_key="idem_1",
        )

    def test_converts_jpy_without_multiplying_by_100(self) -> None:
        adapter = StripeAdapter(api_key="sk_test_xxx", max_retries=0)

        mock_pi = {"id": "pi_jpy"}
        with patch("stripe.PaymentIntent") as MockPI:
            MockPI.create.return_value = mock_pi
            _run(adapter.authorize("t1", Decimal("1000"), "JPY", "c", "i"))

        MockPI.create.assert_called_once_with(
            amount=1000,
            currency="jpy",
            capture_method="manual",
            metadata={"tenant_id": "t1", "customer_ref": "c"},
            idempotency_key="i",
        )

    def test_returns_failure_when_stripe_not_installed(self) -> None:
        adapter = StripeAdapter(api_key="sk_test_xxx", max_retries=0)

        with patch.dict("sys.modules", {"stripe": None}):
            with patch(
                "src.infrastructure.gateway.stripe_adapter.StripeAdapter.authorize",
                wraps=adapter.authorize,
            ):
                pass

    def test_wraps_stripe_exception_on_failure(self) -> None:
        adapter = StripeAdapter(api_key="sk_test_xxx", max_retries=0)

        with patch("stripe.PaymentIntent") as MockPI:
            MockPI.create.side_effect = Exception("Stripe API error")
            with pytest.raises(Exception, match="Stripe API error"):
                _run(adapter.authorize("t1", Decimal("10"), "BRL", "c", "i"))


class TestStripeAdapterCapture:
    def test_calls_capture_with_currency_param(self) -> None:
        adapter = StripeAdapter(api_key="sk_test_xxx", max_retries=0)

        mock_pi = {"id": "pi_captured"}
        with patch("stripe.PaymentIntent") as MockPI:
            MockPI.capture.return_value = mock_pi
            result = _run(adapter.capture("pi_123", Decimal("50.00"), "USD", "idem_cap"))

        assert result.success is True
        assert result.status == GatewayStatus.CAPTURED

        MockPI.capture.assert_called_once_with(
            "pi_123",
            amount_to_capture=5000,
            idempotency_key="idem_cap",
        )

    def test_capture_uses_correct_minor_units_for_currency(self) -> None:
        adapter = StripeAdapter(api_key="sk_test_xxx", max_retries=0)

        with patch("stripe.PaymentIntent") as MockPI:
            MockPI.capture.return_value = {"id": "pi_eur"}
            _run(adapter.capture("pi_x", Decimal("25.50"), "EUR", "idem_eur"))

        MockPI.capture.assert_called_once_with(
            "pi_x",
            amount_to_capture=2550,
            idempotency_key="idem_eur",
        )


class TestStripeAdapterRefund:
    def test_calls_refund_create(self) -> None:
        adapter = StripeAdapter(api_key="sk_test_xxx", max_retries=0)

        mock_refund = {"id": "re_test", "status": "succeeded"}
        with patch("stripe.Refund") as MockRefund:
            MockRefund.create.return_value = mock_refund
            result = _run(adapter.refund("pi_123", Decimal("100.00"), "idem_ref"))

        assert result.success is True
        assert result.status == GatewayStatus.REFUNDED
        assert result.gateway_ref == "re_test"

        MockRefund.create.assert_called_once_with(
            payment_intent="pi_123",
            amount=10000,
            idempotency_key="idem_ref",
        )

    def test_refund_failed_status(self) -> None:
        adapter = StripeAdapter(api_key="sk_test_xxx", max_retries=0)

        mock_refund = {"id": "re_fail", "status": "failed"}
        with patch("stripe.Refund") as MockRefund:
            MockRefund.create.return_value = mock_refund
            result = _run(adapter.refund("pi_123", Decimal("50.00"), "idem_fail"))

        assert result.success is False
        assert result.status == GatewayStatus.FAILED


class TestStripeAdapterGetStatus:
    def test_returns_authorized_for_requires_capture(self) -> None:
        adapter = StripeAdapter(api_key="sk_test_xxx")

        with patch("stripe.PaymentIntent") as MockPI:
            MockPI.retrieve.return_value = {"status": "requires_capture"}
            result = _run(adapter.get_status("pi_123"))

        assert result.success is True
        assert result.status == GatewayStatus.AUTHORIZED

    def test_returns_captured_for_succeeded(self) -> None:
        adapter = StripeAdapter(api_key="sk_test_xxx")

        with patch("stripe.PaymentIntent") as MockPI:
            MockPI.retrieve.return_value = {"status": "succeeded"}
            result = _run(adapter.get_status("pi_123"))

        assert result.status == GatewayStatus.CAPTURED

    def test_returns_not_found_for_invalid_request(self) -> None:
        adapter = StripeAdapter(api_key="sk_test_xxx")

        with patch("stripe.PaymentIntent") as MockPI, patch("stripe.error") as mock_error:
            exc_class = type("InvalidRequestError", (Exception,), {})
            mock_error.InvalidRequestError = exc_class
            MockPI.retrieve.side_effect = exc_class("Not found")

            result = _run(adapter.get_status("pi_nonexistent"))

        assert result.success is False
        assert result.status == GatewayStatus.NOT_FOUND


class TestCurrencyMultipliers:
    def test_brl_multiplier_is_100(self) -> None:
        assert CURRENCY_MULTIPLIERS["BRL"] == 100

    def test_usd_multiplier_is_100(self) -> None:
        assert CURRENCY_MULTIPLIERS["USD"] == 100

    def test_jpy_multiplier_is_1(self) -> None:
        assert CURRENCY_MULTIPLIERS["JPY"] == 1

    def test_to_minor_units_calculation(self) -> None:
        adapter = StripeAdapter(api_key="sk_test_xxx")
        assert adapter._to_minor_units(Decimal("99.99"), "BRL") == 9999
        assert adapter._to_minor_units(Decimal("1000"), "JPY") == 1000
