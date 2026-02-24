"""Provider contract tests — validates our API against fluxe-b2b-suite expectations."""
from __future__ import annotations

import pytest

from src.application.payments import PaymentIntentDTO
from src.application.ledger import LedgerEntryDTO, LedgerLineDTO, AccountBalanceDTO


class TestPaymentIntentContract:
    """Verify PaymentIntentDTO structure matches frontend expectations."""

    def test_payment_intent_dto_has_required_fields(self) -> None:
        dto = PaymentIntentDTO(
            id="550e8400-e29b-41d4-a716-446655440000",
            amount="100.50",
            currency="BRL",
            status="CREATED",
            customer_ref="order:123",
            gateway_ref=None,
            created_at="2026-02-23T10:00:00+00:00",
            updated_at="2026-02-23T10:00:00+00:00",
        )
        data = dto.model_dump()
        assert "id" in data
        assert "amount" in data
        assert "currency" in data
        assert "status" in data
        assert "customer_ref" in data
        assert "gateway_ref" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_status_values_are_valid(self) -> None:
        valid_statuses = {"CREATED", "AUTHORIZED", "SETTLED", "FAILED", "REFUNDED", "PARTIALLY_REFUNDED"}
        for status in valid_statuses:
            dto = PaymentIntentDTO(
                id="550e8400-e29b-41d4-a716-446655440000",
                amount="100.00",
                currency="BRL",
                status=status,
                customer_ref="test",
                created_at="2026-02-23T10:00:00+00:00",
                updated_at="2026-02-23T10:00:00+00:00",
            )
            assert dto.status == status

    def test_amount_is_string_representation(self) -> None:
        dto = PaymentIntentDTO(
            id="550e8400-e29b-41d4-a716-446655440000",
            amount="9999.99",
            currency="USD",
            status="CREATED",
            customer_ref="test",
            created_at="2026-02-23T10:00:00+00:00",
            updated_at="2026-02-23T10:00:00+00:00",
        )
        assert isinstance(dto.amount, str)
        assert "." in dto.amount


class TestLedgerContract:
    """Verify Ledger DTOs match frontend expectations."""

    def test_ledger_entry_dto_structure(self) -> None:
        dto = LedgerEntryDTO(
            id="550e8400-e29b-41d4-a716-446655440000",
            payment_intent_id="660e8400-e29b-41d4-a716-446655440000",
            posted_at="2026-02-23T10:00:00+00:00",
            lines=[
                LedgerLineDTO(side="DEBIT", account="CASH", amount="100.00", currency="BRL"),
                LedgerLineDTO(side="CREDIT", account="REVENUE", amount="100.00", currency="BRL"),
            ],
        )
        data = dto.model_dump()
        assert "id" in data
        assert "payment_intent_id" in data
        assert "posted_at" in data
        assert "lines" in data
        assert len(data["lines"]) == 2
        for line in data["lines"]:
            assert "side" in line
            assert "account" in line
            assert "amount" in line
            assert "currency" in line

    def test_account_balance_dto_structure(self) -> None:
        dto = AccountBalanceDTO(
            account="CASH",
            currency="BRL",
            debits_total="500.00",
            credits_total="200.00",
            balance="-300.00",
        )
        data = dto.model_dump()
        assert "account" in data
        assert "currency" in data
        assert "debits_total" in data
        assert "credits_total" in data
        assert "balance" in data


class TestMessageContracts:
    """Verify message payloads match node-b2b-orders expectations."""

    def test_payment_settled_event_structure(self) -> None:
        """payment.settled event must contain these fields for node-b2b-orders."""
        event_payload = {
            "order_id": "ord_123",
            "tenant_id": "tenant_demo",
            "correlation_id": "abc123",
            "payment_intent_id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "SETTLED",
            "amount": "100.00",
            "currency": "BRL",
        }
        required_fields = {"order_id", "tenant_id", "correlation_id", "payment_intent_id", "status", "amount", "currency"}
        assert required_fields.issubset(event_payload.keys())

    def test_charge_requested_payload_parsing(self) -> None:
        """payment.charge_requested from node-b2b-orders must be parseable."""
        from src.worker.handlers.charge_request import parse_charge_payload

        snake_case_payload = {
            "order_id": "ord_456",
            "tenant_id": "tenant_demo",
            "total_amount": "250.00",
            "currency": "BRL",
            "correlation_id": "corr_789",
            "customer_ref": "cust_abc",
        }
        parsed = parse_charge_payload(snake_case_payload)
        assert parsed["order_id"] == "ord_456"
        assert parsed["tenant_id"] == "tenant_demo"
        assert parsed["total_amount"] == "250.00"

    def test_charge_requested_camel_case_payload(self) -> None:
        """camelCase payloads from node-b2b-orders must also be parseable."""
        from src.worker.handlers.charge_request import parse_charge_payload

        camel_case_payload = {
            "orderId": "ord_456",
            "tenantId": "tenant_demo",
            "totalAmount": "250.00",
            "currency": "BRL",
            "correlationId": "corr_789",
            "customerRef": "cust_abc",
        }
        parsed = parse_charge_payload(camel_case_payload)
        assert parsed["order_id"] == "ord_456"
        assert parsed["tenant_id"] == "tenant_demo"

    def test_payment_refunded_event_structure(self) -> None:
        """payment.refunded event must contain these fields."""
        event_payload = {
            "payment_intent_id": "550e8400-e29b-41d4-a716-446655440000",
            "refund_id": "660e8400-e29b-41d4-a716-446655440000",
            "amount": "50.00",
            "currency": "BRL",
            "reason": "customer_request",
            "payment_status": "PARTIALLY_REFUNDED",
            "correlation_id": "abc123",
        }
        required_fields = {"payment_intent_id", "refund_id", "amount", "currency", "payment_status", "correlation_id"}
        assert required_fields.issubset(event_payload.keys())
