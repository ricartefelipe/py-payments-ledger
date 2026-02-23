"""Tests for charge request parsing (payment.charge_requested / order.confirmed)."""

from __future__ import annotations

from src.worker.handlers.charge_request import parse_charge_payload


def test_parse_charge_payload_snake_case() -> None:
    payload = {
        "order_id": "ord-123",
        "tenant_id": "tenant_demo",
        "total_amount": "150.50",
        "currency": "BRL",
        "customer_ref": "CUST-001",
        "correlation_id": "corr-xyz",
    }
    got = parse_charge_payload(payload)
    assert got["order_id"] == "ord-123"
    assert got["tenant_id"] == "tenant_demo"
    assert got["total_amount"] == "150.50"
    assert got["currency"] == "BRL"
    assert got["customer_ref"] == "CUST-001"
    assert got["correlation_id"] == "corr-xyz"


def test_parse_charge_payload_camel_case() -> None:
    payload = {
        "orderId": "ord-456",
        "tenantId": "tenant_acme",
        "totalAmount": "99.99",
        "currency": "USD",
        "customerRef": "REF-2",
        "correlationId": "corr-abc",
    }
    got = parse_charge_payload(payload)
    assert got["order_id"] == "ord-456"
    assert got["tenant_id"] == "tenant_acme"
    assert got["total_amount"] == "99.99"
    assert got["currency"] == "USD"
    assert got["customer_ref"] == "REF-2"
    assert got["correlation_id"] == "corr-abc"


def test_parse_charge_payload_minimal() -> None:
    payload = {"order_id": "o1", "tenant_id": "t1", "total_amount": "10"}
    got = parse_charge_payload(payload)
    assert got["order_id"] == "o1"
    assert got["tenant_id"] == "t1"
    assert got["total_amount"] == "10"
    assert got["currency"] == "BRL"
    assert got["customer_ref"] == "order:o1"
    assert got["correlation_id"] == ""

