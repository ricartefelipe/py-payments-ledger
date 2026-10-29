"""Unit tests for worker message handlers."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from src.worker.handlers.charge_request import parse_charge_payload
from src.worker.handlers.payments import handle_charge_request, handle_event


class TestParseChargePayload:
    def test_parses_snake_case_payload(self) -> None:
        payload = {
            "order_id": "order-123",
            "tenant_id": "00000000-0000-0000-0000-000000000002",
            "total_amount": "250.00",
            "currency": "BRL",
            "customer_ref": "CUST-1",
            "correlation_id": "corr-abc",
        }
        result = parse_charge_payload(payload)

        assert result["order_id"] == "order-123"
        assert result["tenant_id"] == "00000000-0000-0000-0000-000000000002"
        assert result["total_amount"] == "250.00"
        assert result["currency"] == "BRL"
        assert result["customer_ref"] == "CUST-1"
        assert result["correlation_id"] == "corr-abc"

    def test_parses_camel_case_payload(self) -> None:
        payload = {
            "orderId": "order-456",
            "tenantId": "tenant_other",
            "totalAmount": "100.50",
            "currency": "USD",
            "customerRef": "CUST-2",
            "correlationId": "corr-def",
        }
        result = parse_charge_payload(payload)

        assert result["order_id"] == "order-456"
        assert result["tenant_id"] == "tenant_other"
        assert result["total_amount"] == "100.50"
        assert result["currency"] == "USD"

    def test_defaults_currency_to_brl(self) -> None:
        payload = {"order_id": "o1", "tenant_id": "t1", "total_amount": "10"}
        result = parse_charge_payload(payload)

        assert result["currency"] == "BRL"

    def test_defaults_total_amount_to_zero(self) -> None:
        payload = {"order_id": "o1", "tenant_id": "t1"}
        result = parse_charge_payload(payload)

        assert result["total_amount"] == "0"

    def test_handles_empty_payload_gracefully(self) -> None:
        result = parse_charge_payload({})

        assert result["order_id"] == ""
        assert result["tenant_id"] == ""
        assert result["total_amount"] == "0"
        assert result["currency"] == "BRL"


class TestHandleEvent:
    @patch("src.worker.handlers.payments.handle_charge_request")
    def test_dispatches_charge_requested_to_handler(self, mock_handler: MagicMock) -> None:
        session = MagicMock()
        payload = {"order_id": "o1", "tenant_id": "t1", "total_amount": "100"}

        handle_event(session, "payment.charge_requested", payload)

        mock_handler.assert_called_once_with(session, payload)

    @patch("src.worker.handlers.payments.handle_charge_request")
    def test_dispatches_order_confirmed_to_handler(self, mock_handler: MagicMock) -> None:
        session = MagicMock()
        payload = {"order_id": "o2", "tenant_id": "t1", "total_amount": "200"}

        handle_event(session, "order.confirmed", payload)

        mock_handler.assert_called_once_with(session, payload)

    @patch("src.worker.handlers.payments.post_ledger_for_authorized_payment")
    def test_dispatches_payment_authorized_to_ledger_post(
        self, mock_post_ledger: MagicMock
    ) -> None:
        session = MagicMock()
        pi_id = str(uuid.uuid4())
        payload = {"payment_intent_id": pi_id, "tenant_id": "00000000-0000-0000-0000-000000000002"}

        handle_event(session, "payment.authorized", payload)

        mock_post_ledger.assert_called_once_with(
            session, "00000000-0000-0000-0000-000000000002", uuid.UUID(pi_id), gateway=None
        )

    @patch("src.worker.handlers.payments.post_ledger_for_authorized_payment")
    def test_payment_authorized_accepts_camel_case_ids(self, mock_post_ledger: MagicMock) -> None:
        session = MagicMock()
        pi_id = str(uuid.uuid4())
        payload = {"paymentIntentId": pi_id, "tenantId": "tenant_camel"}

        handle_event(session, "payment.authorized", payload)

        mock_post_ledger.assert_called_once_with(
            session, "tenant_camel", uuid.UUID(pi_id), gateway=None
        )

    def test_ignores_unknown_routing_key(self) -> None:
        session = MagicMock()
        handle_event(session, "unknown.event", {"foo": "bar"})

    @patch("src.worker.handlers.payments.handle_charge_request")
    @patch("src.worker.handlers.payments.post_ledger_for_authorized_payment")
    def test_payment_settled_is_noop_on_payments_worker(
        self, mock_post_ledger: MagicMock, mock_charge: MagicMock
    ) -> None:
        session = MagicMock()
        handle_event(session, "payment.settled", {"order_id": "o1", "tenant_id": "t1"})
        mock_post_ledger.assert_not_called()
        mock_charge.assert_not_called()


class TestHandleChargeRequest:
    def test_missing_order_id_logs_warning_and_returns(self) -> None:
        session = MagicMock()
        payload = {"tenant_id": "t1", "total_amount": "10"}

        handle_charge_request(session, payload)

        session.begin.assert_not_called()

    def test_missing_tenant_id_logs_warning_and_returns(self) -> None:
        session = MagicMock()
        payload = {"order_id": "o1", "total_amount": "10"}

        handle_charge_request(session, payload)

        session.begin.assert_not_called()


# Os casos de `handle_charge_request` que criam pagamento vivem agora em
# tests/integration/test_charge_request_flow.py. O handler persiste a intencao,
# commita, fala com o provedor fora da transacao e finaliza com
# compare-and-set — com sessao mockada esses testes aprovavam um fluxo que nao
# funciona. A versao de integracao afirma o mesmo e mais: reentrega nao cobra
# duas vezes, chave de idempotencia ancorada no pedido, e resultado incerto nao
# e reenviado.
