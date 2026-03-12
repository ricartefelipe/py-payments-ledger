"""Consumer-driven contract tests — event schema compatibility between services."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from src.worker.handlers.charge_request import parse_charge_payload

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / ".." / "node-b2b-orders" / "docs" / "contracts" / "schemas"

PAYMENT_AUTHORIZED_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["payment_intent_id", "amount", "currency", "correlation_id"],
    "properties": {
        "payment_intent_id": {"type": "string"},
        "amount": {"type": "string"},
        "currency": {"type": "string"},
        "gateway_ref": {"type": "string"},
        "correlation_id": {"type": "string"},
    },
}

PAYMENT_SETTLED_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["order_id", "tenant_id", "correlation_id", "payment_intent_id", "status", "amount", "currency"],
    "properties": {
        "order_id": {"type": "string"},
        "tenant_id": {"type": "string"},
        "correlation_id": {"type": "string"},
        "payment_intent_id": {"type": "string"},
        "status": {"type": "string", "enum": ["SETTLED"]},
        "amount": {"type": "string"},
        "currency": {"type": "string"},
    },
}

PAYMENT_FAILED_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["payment_intent_id", "correlation_id"],
    "properties": {
        "payment_intent_id": {"type": "string"},
        "error_code": {"type": "string"},
        "error_message": {"type": "string"},
        "correlation_id": {"type": "string"},
    },
}

PAYMENT_CAPTURED_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["payment_intent_id", "amount", "currency", "correlation_id"],
    "properties": {
        "payment_intent_id": {"type": "string"},
        "amount": {"type": "string"},
        "currency": {"type": "string"},
        "gateway_ref": {"type": "string"},
        "correlation_id": {"type": "string"},
    },
}


def _load_orders_schema(filename: str) -> dict[str, Any]:
    schema_path = SCHEMAS_DIR / filename
    if not schema_path.exists():
        pytest.skip(f"Schema file not found: {schema_path}")
    return json.loads(schema_path.read_text())


class TestPaymentAuthorizedContract:
    """Verify payment.authorized events match the schema consumed by node-b2b-orders."""

    def _make_payload(self, **overrides: Any) -> dict[str, Any]:
        base = {
            "payment_intent_id": "550e8400-e29b-41d4-a716-446655440000",
            "amount": "100.50",
            "currency": "BRL",
            "gateway_ref": "gw_ref_123",
            "correlation_id": "corr-auth-01",
        }
        base.update(overrides)
        return base

    def test_valid_payload_matches_schema(self) -> None:
        jsonschema.validate(self._make_payload(), PAYMENT_AUTHORIZED_SCHEMA)

    def test_missing_payment_intent_id_rejected(self) -> None:
        payload = self._make_payload()
        del payload["payment_intent_id"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, PAYMENT_AUTHORIZED_SCHEMA)

    def test_missing_correlation_id_rejected(self) -> None:
        payload = self._make_payload()
        del payload["correlation_id"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, PAYMENT_AUTHORIZED_SCHEMA)

    def test_empty_gateway_ref_accepted(self) -> None:
        jsonschema.validate(self._make_payload(gateway_ref=""), PAYMENT_AUTHORIZED_SCHEMA)


class TestPaymentSettledContract:
    """Verify payment.settled events match the schema consumed by node-b2b-orders."""

    def _make_payload(self, **overrides: Any) -> dict[str, Any]:
        base = {
            "order_id": "ord_123",
            "tenant_id": "tenant_demo",
            "correlation_id": "corr-settle-01",
            "payment_intent_id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "SETTLED",
            "amount": "100.00",
            "currency": "BRL",
        }
        base.update(overrides)
        return base

    def test_valid_payload_matches_schema(self) -> None:
        jsonschema.validate(self._make_payload(), PAYMENT_SETTLED_SCHEMA)

    def test_status_must_be_settled(self) -> None:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(self._make_payload(status="AUTHORIZED"), PAYMENT_SETTLED_SCHEMA)

    def test_missing_order_id_rejected(self) -> None:
        payload = self._make_payload()
        del payload["order_id"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, PAYMENT_SETTLED_SCHEMA)

    def test_missing_tenant_id_rejected(self) -> None:
        payload = self._make_payload()
        del payload["tenant_id"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, PAYMENT_SETTLED_SCHEMA)

    def test_amount_is_string(self) -> None:
        payload = self._make_payload()
        assert isinstance(payload["amount"], str)
        assert "." in payload["amount"]


class TestPaymentCapturedContract:
    """Verify payment.captured events match the expected schema."""

    def test_valid_payload(self) -> None:
        payload = {
            "payment_intent_id": "550e8400-e29b-41d4-a716-446655440000",
            "amount": "200.00",
            "currency": "USD",
            "gateway_ref": "gw_cap_456",
            "correlation_id": "corr-cap-01",
        }
        jsonschema.validate(payload, PAYMENT_CAPTURED_SCHEMA)

    def test_missing_amount_rejected(self) -> None:
        payload = {
            "payment_intent_id": "550e8400-e29b-41d4-a716-446655440000",
            "currency": "USD",
            "correlation_id": "corr-cap-02",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, PAYMENT_CAPTURED_SCHEMA)


class TestPaymentFailedContract:
    """Verify payment.failed events match the expected schema."""

    def test_valid_payload(self) -> None:
        payload = {
            "payment_intent_id": "550e8400-e29b-41d4-a716-446655440000",
            "error_code": "INSUFFICIENT_FUNDS",
            "error_message": "Card declined",
            "correlation_id": "corr-fail-01",
        }
        jsonschema.validate(payload, PAYMENT_FAILED_SCHEMA)

    def test_minimal_payload(self) -> None:
        payload = {
            "payment_intent_id": "550e8400-e29b-41d4-a716-446655440000",
            "correlation_id": "corr-fail-02",
        }
        jsonschema.validate(payload, PAYMENT_FAILED_SCHEMA)


class TestConsumedOrderConfirmedContract:
    """Verify order.confirmed events from node-b2b-orders are handled correctly."""

    @pytest.fixture()
    def order_confirmed_schema(self) -> dict[str, Any]:
        return _load_orders_schema("order.confirmed.json")

    def test_snake_case_payload_parsed(self) -> None:
        payload = {
            "order_id": "ord_100",
            "tenant_id": "tenant_demo",
            "total_amount": "350.00",
            "currency": "BRL",
            "correlation_id": "corr-oc-01",
            "customer_ref": "cust_abc",
        }
        parsed = parse_charge_payload(payload)
        assert parsed["order_id"] == "ord_100"
        assert parsed["tenant_id"] == "tenant_demo"
        assert parsed["total_amount"] == "350.00"
        assert parsed["currency"] == "BRL"

    def test_camel_case_payload_parsed(self) -> None:
        payload = {
            "orderId": "ord_200",
            "tenantId": "tenant_demo",
            "totalAmount": "500.00",
            "currency": "USD",
            "correlationId": "corr-oc-02",
            "customerRef": "cust_def",
        }
        parsed = parse_charge_payload(payload)
        assert parsed["order_id"] == "ord_200"
        assert parsed["tenant_id"] == "tenant_demo"
        assert parsed["total_amount"] == "500.00"

    def test_valid_payload_matches_orders_schema(self, order_confirmed_schema: dict[str, Any]) -> None:
        payload = {
            "orderId": "550e8400-e29b-41d4-a716-446655440000",
            "tenantId": "tenant_demo",
            "customerId": "cust-001",
            "items": [{"sku": "SKU-A", "qty": 2, "price": 49.9}],
            "totalAmount": 99.8,
            "currency": "BRL",
        }
        jsonschema.validate(payload, order_confirmed_schema)


class TestConsumedChargeRequestedContract:
    """Verify charge_requested events from node-b2b-orders are handled correctly."""

    @pytest.fixture()
    def charge_requested_schema(self) -> dict[str, Any]:
        return _load_orders_schema("charge_requested.json")

    def test_valid_charge_payload_matches_schema(self, charge_requested_schema: dict[str, Any]) -> None:
        payload = {
            "orderId": "550e8400-e29b-41d4-a716-446655440000",
            "tenantId": "tenant_demo",
            "customerId": "cust-001",
            "items": [{"sku": "SKU-A", "qty": 1, "price": 100}],
            "totalAmount": 100,
            "currency": "BRL",
            "correlationId": "corr-cr-01",
        }
        jsonschema.validate(payload, charge_requested_schema)

    def test_charge_payload_missing_items_rejected(self, charge_requested_schema: dict[str, Any]) -> None:
        payload = {
            "orderId": "550e8400-e29b-41d4-a716-446655440000",
            "tenantId": "tenant_demo",
            "customerId": "cust-001",
            "totalAmount": 100,
            "currency": "BRL",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, charge_requested_schema)

    def test_charge_payload_parsed_by_handler(self) -> None:
        payload = {
            "orderId": "550e8400-e29b-41d4-a716-446655440000",
            "tenantId": "tenant_demo",
            "totalAmount": "100.00",
            "currency": "BRL",
            "correlationId": "corr-cr-02",
            "customerRef": "cust-001",
        }
        parsed = parse_charge_payload(payload)
        assert parsed["order_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert parsed["tenant_id"] == "tenant_demo"
        assert parsed["total_amount"] == "100.00"
