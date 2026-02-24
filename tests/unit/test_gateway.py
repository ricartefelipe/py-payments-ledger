from __future__ import annotations

import asyncio
import time
from decimal import Decimal

import pytest

from src.application.ports.payment_gateway import GatewayResult, GatewayStatus
from src.infrastructure.gateway.fake import FakeGatewayAdapter
from src.infrastructure.gateway.stripe_adapter import CircuitBreaker


class TestFakeGatewayAdapter:
    def test_authorize_success(self) -> None:
        adapter = FakeGatewayAdapter()
        result = asyncio.get_event_loop().run_until_complete(
            adapter.authorize("t1", Decimal("100.00"), "BRL", "cust_123", "idem_1")
        )
        assert result.success is True
        assert result.status == GatewayStatus.AUTHORIZED
        assert result.gateway_ref.startswith("fake_")

    def test_capture_success(self) -> None:
        adapter = FakeGatewayAdapter()
        loop = asyncio.get_event_loop()
        auth = loop.run_until_complete(
            adapter.authorize("t1", Decimal("50.00"), "USD", "cust_1", "idem_1")
        )
        cap = loop.run_until_complete(
            adapter.capture(auth.gateway_ref, Decimal("50.00"), "idem_2")
        )
        assert cap.success is True
        assert cap.status == GatewayStatus.CAPTURED

    def test_refund_full(self) -> None:
        adapter = FakeGatewayAdapter()
        loop = asyncio.get_event_loop()
        auth = loop.run_until_complete(
            adapter.authorize("t1", Decimal("100.00"), "BRL", "cust_1", "idem_1")
        )
        loop.run_until_complete(
            adapter.capture(auth.gateway_ref, Decimal("100.00"), "idem_2")
        )
        ref = loop.run_until_complete(
            adapter.refund(auth.gateway_ref, Decimal("100.00"), "idem_3")
        )
        assert ref.success is True
        assert ref.status == GatewayStatus.REFUNDED

    def test_refund_partial(self) -> None:
        adapter = FakeGatewayAdapter()
        loop = asyncio.get_event_loop()
        auth = loop.run_until_complete(
            adapter.authorize("t1", Decimal("100.00"), "BRL", "cust_1", "idem_1")
        )
        loop.run_until_complete(
            adapter.capture(auth.gateway_ref, Decimal("100.00"), "idem_2")
        )
        ref = loop.run_until_complete(
            adapter.refund(auth.gateway_ref, Decimal("50.00"), "idem_3")
        )
        assert ref.success is True
        assert ref.status == GatewayStatus.PARTIALLY_REFUNDED

    def test_capture_not_found(self) -> None:
        adapter = FakeGatewayAdapter()
        result = asyncio.get_event_loop().run_until_complete(
            adapter.capture("nonexistent", Decimal("10.00"), "idem_1")
        )
        assert result.success is False
        assert result.status == GatewayStatus.NOT_FOUND

    def test_get_status(self) -> None:
        adapter = FakeGatewayAdapter()
        loop = asyncio.get_event_loop()
        auth = loop.run_until_complete(
            adapter.authorize("t1", Decimal("10.00"), "BRL", "c", "i")
        )
        status = loop.run_until_complete(adapter.get_status(auth.gateway_ref))
        assert status.success is True
        assert status.status == GatewayStatus.AUTHORIZED


class TestCircuitBreaker:
    def test_closed_by_default(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.is_open is False

    def test_opens_after_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open is True

    def test_resets_on_success(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        assert cb.is_open is False

    def test_recovers_after_timeout(self) -> None:
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        assert cb.is_open is True
        time.sleep(0.02)
        assert cb.is_open is False


class TestGatewayResult:
    def test_success_result(self) -> None:
        r = GatewayResult(success=True, gateway_ref="pi_123", status=GatewayStatus.AUTHORIZED)
        assert r.success is True
        assert r.error_code == ""

    def test_failure_result(self) -> None:
        r = GatewayResult(
            success=False, gateway_ref="", status=GatewayStatus.FAILED,
            error_code="card_declined", error_message="Declined", is_retryable=False,
        )
        assert r.success is False
        assert r.is_retryable is False
