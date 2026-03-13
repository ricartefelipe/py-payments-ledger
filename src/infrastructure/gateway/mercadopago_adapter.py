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

MP_STATUS_MAP: dict[str, GatewayStatus] = {
    "approved": GatewayStatus.CAPTURED,
    "authorized": GatewayStatus.AUTHORIZED,
    "in_process": GatewayStatus.AUTHORIZED,
    "pending": GatewayStatus.AUTHORIZED,
    "rejected": GatewayStatus.FAILED,
    "cancelled": GatewayStatus.VOIDED,
    "refunded": GatewayStatus.REFUNDED,
    "charged_back": GatewayStatus.REFUNDED,
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


class MercadoPagoAdapter:
    """Mercado Pago payment gateway adapter with retry and circuit breaker."""

    def __init__(
        self,
        access_token: str,
        api_url: str = "https://api.mercadopago.com",
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        circuit_failure_threshold: int = 5,
        circuit_recovery_timeout: float = 30.0,
    ) -> None:
        self._access_token = access_token
        self._api_url = api_url.rstrip("/")
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._circuit = CircuitBreaker(circuit_failure_threshold, circuit_recovery_timeout)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": "",
        }

    def _headers_with_idempotency(self, idempotency_key: str) -> dict[str, str]:
        h = self._headers()
        h["X-Idempotency-Key"] = idempotency_key
        return h

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
                    "transaction_amount": float(amount),
                    "currency_id": currency.upper(),
                    "description": f"Payment for tenant {tenant_id}",
                    "capture": False,
                    "external_reference": customer_ref,
                    "metadata": {
                        "tenant_id": tenant_id,
                        "customer_ref": customer_ref,
                        "idempotency_key": idempotency_key,
                    },
                }
                if payment_method_token:
                    payload["token"] = payment_method_token
                resp = await client.post(
                    f"{self._api_url}/v1/payments",
                    json=payload,
                    headers=self._headers_with_idempotency(idempotency_key),
                )
                resp.raise_for_status()
                data = resp.json()
                mp_status = data.get("status", "")
                gw_status = MP_STATUS_MAP.get(mp_status, GatewayStatus.AUTHORIZED)
                return GatewayResult(
                    success=True,
                    gateway_ref=str(data["id"]),
                    status=gw_status,
                )

        try:
            return await self._call_with_retry("authorize", _do_authorize)
        except Exception as exc:
            log.error("mercadopago authorize error", extra={"error": str(exc)})
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
                    "transaction_amount": float(amount),
                    "capture": True,
                }
                resp = await client.put(
                    f"{self._api_url}/v1/payments/{gateway_ref}",
                    json=payload,
                    headers=self._headers_with_idempotency(idempotency_key),
                )
                resp.raise_for_status()
                data = resp.json()
                return GatewayResult(
                    success=True,
                    gateway_ref=str(data.get("id", gateway_ref)),
                    status=GatewayStatus.CAPTURED,
                )

        try:
            return await self._call_with_retry("capture", _do_capture)
        except Exception as exc:
            log.error("mercadopago capture error", extra={"error": str(exc)})
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
                payload = {"amount": float(amount)}
                resp = await client.post(
                    f"{self._api_url}/v1/payments/{gateway_ref}/refunds",
                    json=payload,
                    headers=self._headers_with_idempotency(idempotency_key),
                )
                resp.raise_for_status()
                data = resp.json()
                refund_status = data.get("status", "")
                if refund_status == "approved":
                    status = GatewayStatus.REFUNDED
                else:
                    status = GatewayStatus.FAILED
                return GatewayResult(
                    success=refund_status == "approved",
                    gateway_ref=str(data.get("id", gateway_ref)),
                    status=status,
                )

        try:
            return await self._call_with_retry("refund", _do_refund)
        except Exception as exc:
            log.error("mercadopago refund error", extra={"error": str(exc)})
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
                payload = {"status": "cancelled"}
                resp = await client.put(
                    f"{self._api_url}/v1/payments/{gateway_ref}",
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return GatewayResult(
                    success=True,
                    gateway_ref=str(data.get("id", gateway_ref)),
                    status=GatewayStatus.VOIDED,
                )

        try:
            return await self._call_with_retry("void", _do_void)
        except Exception as exc:
            log.error("mercadopago void error", extra={"error": str(exc)})
            return GatewayResult(
                success=False,
                gateway_ref=gateway_ref,
                status=GatewayStatus.FAILED,
                error_code="gateway_error",
                error_message=str(exc),
            )

    async def save_payment_method(self, customer_ref: str, payment_token: str) -> TokenResult:
        async def _do_save() -> TokenResult:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._api_url}/v1/customers/{customer_ref}/cards",
                    json={"token": payment_token},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return TokenResult(
                    success=True,
                    gateway_token=data.get("id", payment_token),
                    card_last4=data.get("last_four_digits", ""),
                    card_brand=data.get("payment_method", {}).get("name", ""),
                    card_exp_month=data.get("expiration_month", 0),
                    card_exp_year=data.get("expiration_year", 0),
                )

        try:
            result = await self._call_with_retry("save_payment_method", _do_save)
            if isinstance(result, GatewayResult):
                return TokenResult(
                    success=False,
                    gateway_token="",
                    error_code=result.error_code,
                    error_message=result.error_message,
                )
            return result
        except Exception as exc:
            return TokenResult(
                success=False,
                gateway_token="",
                error_code="gateway_error",
                error_message=str(exc),
            )

    async def delete_payment_method(self, gateway_token: str) -> bool:
        return True

    async def get_status(self, gateway_ref: str) -> GatewayResult:
        async def _do_get_status() -> GatewayResult:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{self._api_url}/v1/payments/{gateway_ref}",
                    headers=self._headers(),
                )
                if resp.status_code == 404:
                    return GatewayResult(
                        success=False,
                        gateway_ref=gateway_ref,
                        status=GatewayStatus.NOT_FOUND,
                        error_code="not_found",
                        error_message="Payment not found in Mercado Pago",
                    )
                resp.raise_for_status()
                data = resp.json()
                mp_status = data.get("status", "")
                gw_status = MP_STATUS_MAP.get(mp_status, GatewayStatus.FAILED)
                return GatewayResult(success=True, gateway_ref=gateway_ref, status=gw_status)

        try:
            return await self._call_with_retry("get_status", _do_get_status)
        except Exception as exc:
            log.error("mercadopago get_status error", extra={"error": str(exc)})
            return GatewayResult(
                success=False,
                gateway_ref=gateway_ref,
                status=GatewayStatus.FAILED,
                error_code="gateway_error",
                error_message=str(exc),
            )

    async def list_payment_intents(self, created_after: int, limit: int = 100) -> list[dict]:
        """Fetch recent payments from Mercado Pago for reconciliation."""

        async def _do_list() -> list[dict]:
            from datetime import datetime, timezone

            dt = datetime.fromtimestamp(created_after, tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000-04:00")

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{self._api_url}/v1/payments/search",
                    params={
                        "begin_date": date_str,
                        "limit": limit,
                        "sort": "date_created",
                        "criteria": "desc",
                    },
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()

            results: list[dict] = []
            for payment in data.get("results", []):
                currency = (payment.get("currency_id") or "BRL").upper()
                results.append(
                    {
                        "gateway_ref": str(payment["id"]),
                        "amount": Decimal(str(payment.get("transaction_amount", 0))),
                        "currency": currency,
                        "status": payment.get("status", ""),
                        "metadata": payment.get("metadata", {}),
                    }
                )
            return results

        try:
            result = await self._call_with_retry("list_payment_intents", _do_list)
            if isinstance(result, GatewayResult):
                log.warning("list_payment_intents failed", extra={"error": result.error_message})
                return []
            return result
        except Exception as exc:
            log.error("mercadopago list_payment_intents error", extra={"error": str(exc)})
            return []
