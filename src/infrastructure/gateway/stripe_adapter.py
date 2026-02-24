from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from src.application.ports.payment_gateway import GatewayResult, GatewayStatus
from src.shared.logging import get_logger

log = get_logger(__name__)

RETRYABLE_ERRORS = {"rate_limit", "api_connection_error", "api_error", "timeout"}
CURRENCY_MULTIPLIERS: dict[str, int] = {
    "BRL": 100, "USD": 100, "EUR": 100, "JPY": 1,
}


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._is_open = False

    @property
    def is_open(self) -> bool:
        if self._is_open and (time.monotonic() - self._last_failure_time) > self._recovery_timeout:
            self._is_open = False
            self._failure_count = 0
        return self._is_open

    def record_success(self) -> None:
        self._failure_count = 0
        self._is_open = False

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self._failure_threshold:
            self._is_open = True


class StripeAdapter:
    """Stripe payment gateway adapter with retry and circuit breaker."""

    def __init__(
        self,
        api_key: str,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        circuit_failure_threshold: int = 5,
        circuit_recovery_timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._circuit = CircuitBreaker(circuit_failure_threshold, circuit_recovery_timeout)

    def _to_minor_units(self, amount: Decimal, currency: str) -> int:
        multiplier = CURRENCY_MULTIPLIERS.get(currency.upper(), 100)
        return int(amount * multiplier)

    async def _call_with_retry(self, operation: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        import asyncio
        import random

        if self._circuit.is_open:
            return GatewayResult(
                success=False, gateway_ref="", status=GatewayStatus.FAILED,
                error_code="circuit_open",
                error_message="Circuit breaker is open, gateway temporarily unavailable",
                is_retryable=True,
            )

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                result = await func(*args, **kwargs)
                self._circuit.record_success()
                return result
            except Exception as exc:
                last_error = exc
                error_type = getattr(exc, "code", "") or type(exc).__name__.lower()
                if error_type not in RETRYABLE_ERRORS and attempt == 0:
                    self._circuit.record_failure()
                    raise
                if attempt < self._max_retries:
                    delay = min(self._base_delay * (2 ** attempt) + random.uniform(0, 1), self._max_delay)
                    log.warning(
                        "gateway retry",
                        extra={"operation": operation, "attempt": attempt + 1, "delay": delay},
                    )
                    await asyncio.sleep(delay)
                else:
                    self._circuit.record_failure()

        error_msg = str(last_error) if last_error else "max retries exceeded"
        return GatewayResult(
            success=False, gateway_ref="", status=GatewayStatus.FAILED,
            error_code="max_retries", error_message=error_msg, is_retryable=True,
        )

    async def authorize(
        self, tenant_id: str, amount: Decimal, currency: str, customer_ref: str, idempotency_key: str
    ) -> GatewayResult:
        try:
            import stripe
        except ImportError:
            log.error("stripe package not installed")
            return GatewayResult(
                success=False, gateway_ref="", status=GatewayStatus.FAILED,
                error_code="configuration_error", error_message="stripe SDK not installed",
            )

        stripe.api_key = self._api_key

        async def _do_authorize() -> GatewayResult:
            pi = stripe.PaymentIntent.create(
                amount=self._to_minor_units(amount, currency),
                currency=currency.lower(),
                capture_method="manual",
                metadata={"tenant_id": tenant_id, "customer_ref": customer_ref},
                idempotency_key=idempotency_key,
            )
            return GatewayResult(
                success=True, gateway_ref=pi["id"], status=GatewayStatus.AUTHORIZED,
            )

        return await self._call_with_retry("authorize", _do_authorize)

    async def capture(
        self, gateway_ref: str, amount: Decimal, idempotency_key: str
    ) -> GatewayResult:
        try:
            import stripe
        except ImportError:
            return GatewayResult(
                success=False, gateway_ref=gateway_ref, status=GatewayStatus.FAILED,
                error_code="configuration_error", error_message="stripe SDK not installed",
            )

        stripe.api_key = self._api_key

        async def _do_capture() -> GatewayResult:
            pi = stripe.PaymentIntent.capture(
                gateway_ref,
                amount_to_capture=self._to_minor_units(amount, gateway_ref[:3]),
                idempotency_key=idempotency_key,
            )
            return GatewayResult(
                success=True, gateway_ref=pi["id"], status=GatewayStatus.CAPTURED,
            )

        return await self._call_with_retry("capture", _do_capture)

    async def refund(
        self, gateway_ref: str, amount: Decimal, idempotency_key: str
    ) -> GatewayResult:
        try:
            import stripe
        except ImportError:
            return GatewayResult(
                success=False, gateway_ref=gateway_ref, status=GatewayStatus.FAILED,
                error_code="configuration_error", error_message="stripe SDK not installed",
            )

        stripe.api_key = self._api_key

        async def _do_refund() -> GatewayResult:
            refund = stripe.Refund.create(
                payment_intent=gateway_ref,
                amount=self._to_minor_units(amount, "BRL"),
                idempotency_key=idempotency_key,
            )
            status = GatewayStatus.REFUNDED if refund["status"] == "succeeded" else GatewayStatus.FAILED
            return GatewayResult(
                success=refund["status"] == "succeeded",
                gateway_ref=refund["id"],
                status=status,
            )

        return await self._call_with_retry("refund", _do_refund)

    async def get_status(self, gateway_ref: str) -> GatewayResult:
        try:
            import stripe
        except ImportError:
            return GatewayResult(
                success=False, gateway_ref=gateway_ref, status=GatewayStatus.FAILED,
                error_code="configuration_error", error_message="stripe SDK not installed",
            )

        stripe.api_key = self._api_key
        try:
            pi = stripe.PaymentIntent.retrieve(gateway_ref)
        except stripe.error.InvalidRequestError:
            return GatewayResult(
                success=False, gateway_ref=gateway_ref, status=GatewayStatus.NOT_FOUND,
                error_code="not_found", error_message="PaymentIntent not found in Stripe",
            )

        status_map = {
            "requires_capture": GatewayStatus.AUTHORIZED,
            "succeeded": GatewayStatus.CAPTURED,
            "canceled": GatewayStatus.FAILED,
        }
        gw_status = status_map.get(pi["status"], GatewayStatus.FAILED)
        return GatewayResult(success=True, gateway_ref=gateway_ref, status=gw_status)
