from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from src.application.ports.payment_gateway import GatewayResult, GatewayStatus, TokenResult
from src.shared.logging import get_logger

log = get_logger(__name__)


class _SharedStore:
    """Estado do gateway simulado, compartilhado entre processos quando possivel.

    O adaptador guardava o estado em um dicionario do processo. A API autoriza e
    o **worker** captura: sao containers diferentes, com memorias diferentes, e a
    captura encontrava `not_found` para uma autorizacao que existia. O singleton
    do factory nao resolve isso — compartilha a instancia dentro de um processo,
    nao entre processos.

    Um gateway de verdade mantem o estado do lado dele, fora dos nossos
    processos. Aqui o Redis cumpre esse papel quando esta disponivel; sem ele
    (testes unitarios, por exemplo) o estado fica em memoria. Referencias
    genuinamente desconhecidas continuam desconhecidas nos dois modos, portanto o
    comportamento `not_found` e preservado.
    """

    PREFIX = "fakegw:ref:"
    TTL_SECONDS = 24 * 3600

    def __init__(self) -> None:
        self._mem: dict[str, dict] = {}
        self._redis: Any = None
        self._resolved = False

    def _client(self) -> Any:
        if self._resolved:
            return self._redis
        self._resolved = True
        try:
            from src.infrastructure.redis.client import get_redis

            client = get_redis()
            client.ping()
            self._redis = client
            log.info("fake gateway state is shared via Redis")
        except Exception:  # noqa: BLE001 - sem Redis, memoria do processo basta
            self._redis = None
            log.info("fake gateway state is process-local (Redis unavailable)")
        return self._redis

    @staticmethod
    def _encode(entry: dict) -> str:
        return json.dumps({k: str(v) for k, v in entry.items()})

    @staticmethod
    def _decode(raw: str) -> dict:
        data = json.loads(raw)
        out: dict = {}
        for key, value in data.items():
            if key in ("amount", "captured_amount", "refunded_amount"):
                out[key] = Decimal(value)
            elif key == "status":
                out[key] = GatewayStatus(value)
            else:
                out[key] = value
        return out

    def get(self, ref: str) -> dict | None:
        client = self._client()
        if client is None:
            return self._mem.get(ref)
        raw = client.get(self.PREFIX + ref)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return self._decode(raw)
        except (json.JSONDecodeError, ValueError, KeyError):
            log.warning("fake gateway entry is unreadable", extra={"gateway_ref": ref})
            return None

    def put(self, ref: str, entry: dict) -> None:
        client = self._client()
        if client is None:
            self._mem[ref] = entry
            return
        client.set(self.PREFIX + ref, self._encode(entry), ex=self.TTL_SECONDS)

    def setdefault(self, ref: str, default: dict) -> dict:
        current = self.get(ref)
        if current is not None:
            return current
        self.put(ref, default)
        return default

    def items(self, limit: int = 100) -> list[tuple[str, dict]]:
        client = self._client()
        if client is None:
            return list(self._mem.items())[:limit]
        out: list[tuple[str, dict]] = []
        for key in client.scan_iter(match=self.PREFIX + "*", count=limit):
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            ref = key[len(self.PREFIX) :]
            entry = self.get(ref)
            if entry is not None:
                out.append((ref, entry))
            if len(out) >= limit:
                break
        return out


class FakeGatewayAdapter:
    """Simulates a payment gateway for local development and testing."""

    def __init__(self, fail_rate: float = 0.0) -> None:
        self._fail_rate = fail_rate
        self._store = _SharedStore()

    async def authorize(
        self,
        tenant_id: str,
        amount: Decimal,
        currency: str,
        customer_ref: str,
        idempotency_key: str,
        payment_method_token: str = "",
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
        self._store.put(
            ref,
            {
                "status": GatewayStatus.AUTHORIZED,
                "amount": amount,
                "currency": currency,
                "captured_amount": Decimal(0),
                "refunded_amount": Decimal(0),
            },
        )
        log.info("fake authorize", extra={"gateway_ref": ref, "amount": str(amount)})
        return GatewayResult(success=True, gateway_ref=ref, status=GatewayStatus.AUTHORIZED)

    async def capture(
        self, gateway_ref: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> GatewayResult:
        entry = self._store.get(gateway_ref)
        if not entry:
            return GatewayResult(
                success=False,
                gateway_ref=gateway_ref,
                status=GatewayStatus.NOT_FOUND,
                error_code="not_found",
                error_message="Gateway ref not found",
            )
        entry["status"] = GatewayStatus.CAPTURED
        entry["captured_amount"] = amount
        # `get` devolve uma copia do estado compartilhado: a mutacao precisa ser
        # gravada de volta para o outro processo enxergar.
        self._store.put(gateway_ref, entry)
        log.info("fake capture", extra={"gateway_ref": gateway_ref, "amount": str(amount)})
        return GatewayResult(success=True, gateway_ref=gateway_ref, status=GatewayStatus.CAPTURED)

    async def refund(
        self, gateway_ref: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> GatewayResult:
        entry = self._store.setdefault(
            gateway_ref,
            {
                "status": GatewayStatus.AUTHORIZED,
                "amount": amount,
                "currency": currency,
                "captured_amount": Decimal(0),
                "refunded_amount": Decimal(0),
            },
        )
        entry["refunded_amount"] = entry.get("refunded_amount", Decimal(0)) + amount
        if entry["refunded_amount"] >= entry["captured_amount"]:
            entry["status"] = GatewayStatus.REFUNDED
        else:
            entry["status"] = GatewayStatus.PARTIALLY_REFUNDED
        self._store.put(gateway_ref, entry)
        log.info("fake refund", extra={"gateway_ref": gateway_ref, "amount": str(amount)})
        return GatewayResult(success=True, gateway_ref=gateway_ref, status=entry["status"])

    async def void(self, gateway_ref: str, idempotency_key: str = "") -> GatewayResult:
        entry = self._store.setdefault(
            gateway_ref,
            {
                "status": GatewayStatus.AUTHORIZED,
                "amount": Decimal(0),
                "currency": "",
                "captured_amount": Decimal(0),
                "refunded_amount": Decimal(0),
            },
        )
        entry["status"] = GatewayStatus.VOIDED
        self._store.put(gateway_ref, entry)
        log.info("fake void", extra={"gateway_ref": gateway_ref})
        return GatewayResult(success=True, gateway_ref=gateway_ref, status=GatewayStatus.VOIDED)

    async def list_payment_intents(self, created_after: int, limit: int = 100) -> list[dict]:
        """Return stored fake payment intents for reconciliation."""
        results: list[dict] = []
        for ref, entry in self._store.items(limit=limit):
            status_to_stripe = {
                GatewayStatus.AUTHORIZED: "requires_capture",
                GatewayStatus.CAPTURED: "succeeded",
                GatewayStatus.FAILED: "canceled",
                GatewayStatus.REFUNDED: "succeeded",
                GatewayStatus.PARTIALLY_REFUNDED: "succeeded",
            }
            results.append(
                {
                    "gateway_ref": ref,
                    "amount": entry["amount"],
                    "currency": entry["currency"].upper(),
                    "status": status_to_stripe.get(entry["status"], "canceled"),
                    "metadata": {},
                }
            )
        return results

    async def save_payment_method(self, customer_ref: str, payment_token: str) -> TokenResult:
        token = f"fake_pm_{uuid.uuid4().hex[:12]}"
        log.info("fake save_payment_method", extra={"token": token})
        return TokenResult(
            success=True,
            gateway_token=token,
            card_last4="4242",
            card_brand="visa",
            card_exp_month=12,
            card_exp_year=2030,
        )

    async def delete_payment_method(self, gateway_token: str) -> bool:
        log.info("fake delete_payment_method", extra={"gateway_token": gateway_token})
        return True

    async def get_status(self, gateway_ref: str) -> GatewayResult:
        entry = self._store.get(gateway_ref)
        if not entry:
            return GatewayResult(
                success=False,
                gateway_ref=gateway_ref,
                status=GatewayStatus.NOT_FOUND,
                error_code="not_found",
                error_message="Gateway ref not found",
            )
        return GatewayResult(success=True, gateway_ref=gateway_ref, status=entry["status"])
