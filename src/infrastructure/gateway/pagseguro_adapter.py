from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

import httpx

from src.application.ports.payment_gateway import GatewayResult, GatewayStatus, TokenResult
from src.shared.logging import get_logger
from src.shared.metrics import CIRCUIT_BREAKER_STATE

log = get_logger(__name__)

RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
CURRENCY_MULTIPLIERS: dict[str, int] = {
    "BRL": 100,
    "USD": 100,
    "EUR": 100,
    "JPY": 1,
}

PAGSEGURO_STATUS_MAP: dict[str, GatewayStatus] = {
    "AUTHORIZED": GatewayStatus.AUTHORIZED,
    "PAID": GatewayStatus.CAPTURED,
    "CANCELLED": GatewayStatus.VOIDED,
    "DECLINED": GatewayStatus.FAILED,
    "IN_ANALYSIS": GatewayStatus.AUTHORIZED,
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

    @property
    def state(self) -> str:
        return "open" if self.is_open else "closed"

    def record_success(self) -> None:
        self._failure_count = 0
        self._is_open = False

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self._failure_threshold:
            self._is_open = True


class PagSeguroAdapter:
    """PagSeguro payment gateway adapter with retry and circuit breaker."""

    def __init__(
        self,
        token: str,
        api_url: str = "https://api.pagseguro.com",
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        circuit_failure_threshold: int = 5,
        circuit_recovery_timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._circuit = CircuitBreaker(circuit_failure_threshold, circuit_recovery_timeout)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _to_minor_units(self, amount: Decimal, currency: str) -> int:
        multiplier = CURRENCY_MULTIPLIERS.get(currency.upper(), 100)
        return int(amount * multiplier)

    def _from_minor_units(self, amount_cents: int, currency: str) -> Decimal:
        multiplier = CURRENCY_MULTIPLIERS.get(currency.upper(), 100)
        return Decimal(str(amount_cents)) / Decimal(str(multiplier))

    async def _call_with_retry(self, operation: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        import random

        current_state = self._circuit.state
        CIRCUIT_BREAKER_STATE.labels(state="closed").set(1 if current_state == "closed" else 0)
        CIRCUIT_BREAKER_STATE.labels(state="open").set(1 if current_state == "open" else 0)

        if self._circuit.is_open:
            return GatewayResult(
                success=False,
                gateway_ref="",
                status=GatewayStatus.FAILED,
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
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code not in RETRYABLE_HTTP_CODES:
                    self._circuit.record_failure()
                    raise
                if attempt < self._max_retries:
                    delay = min(
                        self._base_delay * (2**attempt) + random.uniform(0, 1), self._max_delay
                    )
                    log.warning(
                        "gateway retry",
                        extra={"operation": operation, "attempt": attempt + 1, "delay": delay},
                    )
                    await asyncio.sleep(delay)
                else:
                    self._circuit.record_failure()
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    delay = min(
                        self._base_delay * (2**attempt) + random.uniform(0, 1), self._max_delay
                    )
                    log.warning(
                        "gateway retry",
                        extra={"operation": operation, "attempt": attempt + 1, "delay": delay},
                    )
                    await asyncio.sleep(delay)
                else:
                    self._circuit.record_failure()
            except Exception as exc:
                last_error = exc
                self._circuit.record_failure()
                raise

        error_msg = str(last_error) if last_error else "max retries exceeded"
        return GatewayResult(
            success=False,
            gateway_ref="",
            status=GatewayStatus.FAILED,
            error_code="max_retries",
            error_message=error_msg,
            is_retryable=True,
        )

    async def authorize(
        self,
        tenant_id: str,
        amount: Decimal,
        currency: str,
        customer_ref: str,
        idempotency_key: str,
        payment_method_token: str = "",
    ) -> GatewayResult:
        async def _do_authorize() -> GatewayResult:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload: dict = {
                    "reference_id": idempotency_key,
                    "description": f"Charge for tenant {tenant_id}",
                    "amount": {
                        "value": self._to_minor_units(amount, currency),
                        "currency": currency.upper(),
                    },
                    "payment_method": {
                        "type": "CREDIT_CARD",
                        "capture": False,
                    },
                    "metadata": {
                        "tenant_id": tenant_id,
                        "customer_ref": customer_ref,
                    },
                }
                if payment_method_token:
                    payload["payment_method"]["card"] = {"id": payment_method_token}
                resp = await client.post(
                    f"{self._api_url}/charges",
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return GatewayResult(
                    success=True,
                    gateway_ref=data["id"],
                    status=GatewayStatus.AUTHORIZED,
                )

        try:
            return await self._call_with_retry("authorize", _do_authorize)
        except Exception as exc:
            log.error("pagseguro authorize error", extra={"error": str(exc)})
            return GatewayResult(
                success=False,
                gateway_ref="",
                status=GatewayStatus.FAILED,
                error_code="gateway_error",
                error_message=str(exc),
            )

    async def capture(
        self, gateway_ref: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> GatewayResult:
        async def _do_capture() -> GatewayResult:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {
                    "amount": {
                        "value": self._to_minor_units(amount, currency),
                    },
                }
                resp = await client.post(
                    f"{self._api_url}/charges/{gateway_ref}/capture",
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return GatewayResult(
                    success=True,
                    gateway_ref=data.get("id", gateway_ref),
                    status=GatewayStatus.CAPTURED,
                )

        try:
            return await self._call_with_retry("capture", _do_capture)
        except Exception as exc:
            log.error("pagseguro capture error", extra={"error": str(exc)})
            return GatewayResult(
                success=False,
                gateway_ref=gateway_ref,
                status=GatewayStatus.FAILED,
                error_code="gateway_error",
                error_message=str(exc),
            )

    async def refund(
        self, gateway_ref: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> GatewayResult:
        async def _do_refund() -> GatewayResult:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {
                    "amount": {
                        "value": self._to_minor_units(amount, currency),
                    },
                }
                resp = await client.post(
                    f"{self._api_url}/charges/{gateway_ref}/cancel",
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                ps_status = data.get("status", "")
                if ps_status == "CANCELLED":
                    status = GatewayStatus.REFUNDED
                else:
                    status = PAGSEGURO_STATUS_MAP.get(ps_status, GatewayStatus.FAILED)
                return GatewayResult(
                    success=True,
                    gateway_ref=data.get("id", gateway_ref),
                    status=status,
                )

        try:
            return await self._call_with_retry("refund", _do_refund)
        except Exception as exc:
            log.error("pagseguro refund error", extra={"error": str(exc)})
            return GatewayResult(
                success=False,
                gateway_ref=gateway_ref,
                status=GatewayStatus.FAILED,
                error_code="gateway_error",
                error_message=str(exc),
            )

    async def void(self, gateway_ref: str) -> GatewayResult:
        async def _do_void() -> GatewayResult:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._api_url}/charges/{gateway_ref}/cancel",
                    json={},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return GatewayResult(
                    success=True,
                    gateway_ref=data.get("id", gateway_ref),
                    status=GatewayStatus.VOIDED,
                )

        try:
            return await self._call_with_retry("void", _do_void)
        except Exception as exc:
            log.error("pagseguro void error", extra={"error": str(exc)})
            return GatewayResult(
                success=False,
                gateway_ref=gateway_ref,
                status=GatewayStatus.FAILED,
                error_code="gateway_error",
                error_message=str(exc),
            )

    async def save_payment_method(self, customer_ref: str, payment_token: str) -> TokenResult:
        return TokenResult(success=True, gateway_token=payment_token)

    async def delete_payment_method(self, gateway_token: str) -> bool:
        return True

    async def get_status(self, gateway_ref: str) -> GatewayResult:
        async def _do_get_status() -> GatewayResult:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{self._api_url}/charges/{gateway_ref}",
                    headers=self._headers(),
                )
                if resp.status_code == 404:
                    return GatewayResult(
                        success=False,
                        gateway_ref=gateway_ref,
                        status=GatewayStatus.NOT_FOUND,
                        error_code="not_found",
                        error_message="Charge not found in PagSeguro",
                    )
                resp.raise_for_status()
                data = resp.json()
                ps_status = data.get("status", "")
                gw_status = PAGSEGURO_STATUS_MAP.get(ps_status, GatewayStatus.FAILED)
                return GatewayResult(success=True, gateway_ref=gateway_ref, status=gw_status)

        try:
            return await self._call_with_retry("get_status", _do_get_status)
        except Exception as exc:
            log.error("pagseguro get_status error", extra={"error": str(exc)})
            return GatewayResult(
                success=False,
                gateway_ref=gateway_ref,
                status=GatewayStatus.FAILED,
                error_code="gateway_error",
                error_message=str(exc),
            )

    async def list_payment_intents(self, created_after: int, limit: int = 100) -> list[dict]:
        """Fetch recent charges from PagSeguro for reconciliation."""

        async def _do_list() -> list[dict]:
            from datetime import datetime, timezone

            dt = datetime.fromtimestamp(created_after, tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{self._api_url}/charges",
                    params={
                        "created_at_start": date_str,
                        "limit": limit,
                    },
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()

            results: list[dict] = []
            for charge in data.get("charges", []):
                amount_info = charge.get("amount", {})
                currency = (amount_info.get("currency") or "BRL").upper()
                results.append({
                    "gateway_ref": charge["id"],
                    "amount": self._from_minor_units(amount_info.get("value", 0), currency),
                    "currency": currency,
                    "status": charge.get("status", ""),
                    "metadata": charge.get("metadata", {}),
                })
            return results

        try:
            result = await self._call_with_retry("list_payment_intents", _do_list)
            if isinstance(result, GatewayResult):
                log.warning(
                    "list_payment_intents failed", extra={"error": result.error_message}
                )
                return []
            return result
        except Exception as exc:
            log.error("pagseguro list_payment_intents error", extra={"error": str(exc)})
            return []
