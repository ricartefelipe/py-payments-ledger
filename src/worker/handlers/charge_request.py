"""Resilient parsing for charge/order events — accepts camelCase and snake_case."""

from __future__ import annotations

from typing import Any


def _get(payload: dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        if k in payload and payload[k] is not None:
            return str(payload[k])
    return default


def _get_decimal(payload: dict[str, Any], *keys: str) -> str:
    for k in keys:
        if k in payload and payload[k] is not None:
            return str(payload[k])
    return "0"


def parse_charge_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Normaliza payload de payment.charge_requested ou order.confirmed.
    Formato canônico de saída: snake_case.
    """
    order_id = _get(payload, "order_id", "orderId")
    tenant_id = _get(payload, "tenant_id", "tenantId")
    total_amount = _get_decimal(payload, "total_amount", "totalAmount")
    currency = _get(payload, "currency") or "BRL"
    customer_ref = _get(payload, "customer_ref", "customerRef")
    correlation_id = _get(payload, "correlation_id", "correlationId")

    return {
        "order_id": order_id,
        "tenant_id": tenant_id,
        "total_amount": total_amount,
        "currency": currency,
        "customer_ref": customer_ref or f"order:{order_id}" if order_id else "",
        "correlation_id": correlation_id,
    }
