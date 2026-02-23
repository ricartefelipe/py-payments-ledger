from __future__ import annotations

import uuid
from decimal import Decimal

from src.application.ports.payment_gateway import GatewayResult, GatewayStatus
from src.shared.logging import get_logger

log = get_logger(__name__)


class FakeGatewayAdapter:
    """Simulates a payment gateway for local development and testing."""

    def __init__(self, fail_rate: float = 0.0) -> None:
        self._fail_rate = fail_rate
        self._store: dict[str, dict] = {}

    async def authorize(
        self, tenant_id: str, amount: Decimal, currency: str, customer_ref: str, idempotency_key: str
    ) -> GatewayResult:
        import random
        if random.random() < self._fail_rate:
            return GatewayResult(
                success=False,
                gateway_ref="",
                status=GatewayStatus.FAILED,
                error_code="card_declined",
                error_message="Simulated decline",
                is_retryable=False,
            )

        ref = f"fake_{uuid.uuid4().hex[:16]}"
        self._store[ref] = {
            "status": GatewayStatus.AUTHORIZED,
            "amount": amount,
            "currency": currency,
            "captured_amount": Decimal(0),
            "refunded_amount": Decimal(0),
        }
        log.info("fake authorize", extra={"gateway_ref": ref, "amount": str(amount)})
        return GatewayResult(success=True, gateway_ref=ref, status=GatewayStatus.AUTHORIZED)

    async def capture(
        self, gateway_ref: str, amount: Decimal, idempotency_key: str
    ) -> GatewayResult:
        entry = self._store.get(gateway_ref)
        if not entry:
            return GatewayResult(
                success=False, gateway_ref=gateway_ref, status=GatewayStatus.NOT_FOUND,
                error_code="not_found", error_message="Gateway ref not found",
            )
        entry["status"] = GatewayStatus.CAPTURED
        entry["captured_amount"] = amount
        log.info("fake capture", extra={"gateway_ref": gateway_ref, "amount": str(amount)})
        return GatewayResult(success=True, gateway_ref=gateway_ref, status=GatewayStatus.CAPTURED)

    async def refund(
        self, gateway_ref: str, amount: Decimal, idempotency_key: str
    ) -> GatewayResult:
        entry = self._store.get(gateway_ref)
        if not entry:
            return GatewayResult(
                success=False, gateway_ref=gateway_ref, status=GatewayStatus.NOT_FOUND,
                error_code="not_found", error_message="Gateway ref not found",
            )
        entry["refunded_amount"] = entry.get("refunded_amount", Decimal(0)) + amount
        if entry["refunded_amount"] >= entry["captured_amount"]:
            entry["status"] = GatewayStatus.REFUNDED
        else:
            entry["status"] = GatewayStatus.PARTIALLY_REFUNDED
        log.info("fake refund", extra={"gateway_ref": gateway_ref, "amount": str(amount)})
        return GatewayResult(success=True, gateway_ref=gateway_ref, status=entry["status"])

    async def get_status(self, gateway_ref: str) -> GatewayResult:
        entry = self._store.get(gateway_ref)
        if not entry:
            return GatewayResult(
                success=False, gateway_ref=gateway_ref, status=GatewayStatus.NOT_FOUND,
                error_code="not_found", error_message="Gateway ref not found",
            )
        return GatewayResult(success=True, gateway_ref=gateway_ref, status=entry["status"])
