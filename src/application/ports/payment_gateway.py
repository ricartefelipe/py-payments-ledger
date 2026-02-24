from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol


class GatewayStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class GatewayResult:
    success: bool
    gateway_ref: str
    status: GatewayStatus
    error_code: str = ""
    error_message: str = ""
    is_retryable: bool = False


class PaymentGatewayPort(Protocol):
    async def authorize(
        self, tenant_id: str, amount: Decimal, currency: str, customer_ref: str, idempotency_key: str
    ) -> GatewayResult: ...

    async def capture(
        self, gateway_ref: str, amount: Decimal, idempotency_key: str
    ) -> GatewayResult: ...

    async def refund(
        self, gateway_ref: str, amount: Decimal, idempotency_key: str
    ) -> GatewayResult: ...

    async def get_status(self, gateway_ref: str) -> GatewayResult: ...
