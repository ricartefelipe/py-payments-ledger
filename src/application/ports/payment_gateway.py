from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class GatewayStatus(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    VOIDED = "VOIDED"
    NOT_FOUND = "NOT_FOUND"
    #: O provedor pode ter aceitado a operacao, mas a resposta nao chegou
    #: (timeout depois do envio, 5xx, conexao cortada no meio). Nao e recusa:
    #: o desfecho so pode ser determinado por consulta.
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class GatewayResult:
    """Resultado de uma operacao remota.

    Os tres desfechos possiveis sao distintos e nao devem ser confundidos:

    * ``success=True`` — o provedor confirmou.
    * ``success=False, is_uncertain=False`` — o provedor **recusou**. Definitivo.
    * ``success=False, is_uncertain=True`` — nao se sabe. Pode ter sido aceito.
      Tratar isto como recusa e o caminho para a cobranca duplicada: o chamador
      precisa consultar com a mesma chave de idempotencia, nunca emitir uma
      operacao nova.
    """

    success: bool
    gateway_ref: str
    status: GatewayStatus
    error_code: str = ""
    error_message: str = ""
    is_retryable: bool = False
    is_uncertain: bool = False


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

    async def void(self, gateway_ref: str, idempotency_key: str = "") -> GatewayResult:
        """Cancel/void an authorized payment before capture.

        ``idempotency_key`` deve ser estavel por obrigacao: um reenvio apos
        timeout precisa alcancar a mesma operacao no provedor.
        """
        ...

    async def get_status(self, gateway_ref: str) -> GatewayResult: ...

    async def list_payment_intents(self, created_after: int, limit: int = 100) -> list[dict]: ...

    async def save_payment_method(self, customer_ref: str, payment_token: str) -> TokenResult:
        """Exchange a single-use token for a reusable payment method reference."""
        ...

    async def delete_payment_method(self, gateway_token: str) -> bool:
        """Detach/remove a saved payment method from the gateway."""
        ...
