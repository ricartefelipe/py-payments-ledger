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
    VOIDED = "VOIDED"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class GatewayResult:
    success: bool
    gateway_ref: str
    status: GatewayStatus
    error_code: str = ""
    error_message: str = ""
    is_retryable: bool = False


@dataclass(frozen=True)
class TokenResult:
    success: bool
    gateway_token: str
    card_last4: str = ""
    card_brand: str = ""
    card_exp_month: int = 0
    card_exp_year: int = 0
    error_code: str = ""
    error_message: str = ""


class PaymentGatewayPort(Protocol):
    async def authorize(
        self,
        tenant_id: str,
        amount: Decimal,
        currency: str,
        customer_ref: str,
        idempotency_key: str,
        payment_method_token: str = "",
    ) -> GatewayResult: ...

    async def capture(
        self, gateway_ref: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> GatewayResult: ...

    async def refund(
        self, gateway_ref: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> GatewayResult: ...

    async def void(self, gateway_ref: str) -> GatewayResult:
        """Cancel/void an authorized payment before capture."""
        ...

    async def get_status(self, gateway_ref: str) -> GatewayResult: ...

    async def list_payment_intents(self, created_after: int, limit: int = 100) -> list[dict]: ...

    async def save_payment_method(
        self, customer_ref: str, payment_token: str
    ) -> TokenResult:
        """Exchange a single-use token for a reusable payment method reference."""
        ...

    async def delete_payment_method(self, gateway_token: str) -> bool:
        """Detach/remove a saved payment method from the gateway."""
        ...
