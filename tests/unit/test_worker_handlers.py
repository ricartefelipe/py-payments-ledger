"""Unit tests for worker message handlers."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from src.application.ports.payment_gateway import GatewayResult, GatewayStatus
from src.infrastructure.db.models import OutboxEvent
from src.worker.handlers.charge_request import parse_charge_payload
from src.worker.handlers import payments as payment_handlers
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
    @patch("src.worker.handlers.payments.set_correlation_id")
    def test_creates_payment_intent_from_order_event(self, mock_set_cid: MagicMock) -> None:
        session = MagicMock()
        ctx_mgr = MagicMock()
        session.begin.return_value = ctx_mgr
        ctx_mgr.__enter__ = MagicMock(return_value=session)
        ctx_mgr.__exit__ = MagicMock(return_value=None)

        session.execute.return_value.scalar_one_or_none.return_value = None
        session.flush = MagicMock()

        payload = {
            "order_id": "order-new",
            "tenant_id": "00000000-0000-0000-0000-000000000002",
            "total_amount": "350.00",
            "currency": "BRL",
            "customer_ref": "CUST-X",
            "correlation_id": "corr-xyz",
        }

        handle_charge_request(session, payload)

        session.add.assert_called()
        assert session.add.call_count == 2

    @patch("src.worker.handlers.payments.set_correlation_id")
    def test_skips_if_order_already_processed(self, mock_set_cid: MagicMock) -> None:
        session = MagicMock()
        ctx_mgr = MagicMock()
        session.begin.return_value = ctx_mgr
        ctx_mgr.__enter__ = MagicMock(return_value=session)
        ctx_mgr.__exit__ = MagicMock(return_value=None)

        existing_pi = MagicMock()
        existing_pi.id = uuid.uuid4()
        session.execute.return_value.scalar_one_or_none.return_value = existing_pi

        payload = {
            "order_id": "order-dupe",
            "tenant_id": "00000000-0000-0000-0000-000000000002",
            "total_amount": "100",
            "currency": "BRL",
            "correlation_id": "corr-dupe",
        }

        handle_charge_request(session, payload)

        session.flush.assert_not_called()

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

    @patch("src.shared.metrics.PAYMENT_RETRY_EXHAUSTED_TOTAL")
    @patch("src.application.webhooks.enqueue_webhook_deliveries")
    @patch("src.infrastructure.gateway.factory.get_gateway_for_tenant")
    @patch.object(payment_handlers, "_settings")
    @patch("src.worker.handlers.payments.set_correlation_id")
    def test_gateway_authorize_failure_emits_retry_exhausted(
        self,
        _mock_set_cid: MagicMock,
        mock_settings: MagicMock,
        mock_get_gateway: MagicMock,
        _mock_enqueue: MagicMock,
        _mock_metric: MagicMock,
    ) -> None:
        """With max_retries=0, one failed authorize → payment.retry_exhausted outbox."""
        mock_settings.charge_request_max_retries = 0
        gw = MagicMock()
        gw.authorize = AsyncMock(
            return_value=GatewayResult(
                success=False,
                gateway_ref="",
                status=GatewayStatus.FAILED,
                error_code="card_declined",
                error_message="declined",
                is_retryable=True,
            )
        )
        mock_get_gateway.return_value = gw

        session = MagicMock()
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None
        session.execute.return_value = exec_result
        session.flush = MagicMock()

        handle_charge_request(
            session,
            {
                "order_id": "o-retry",
                "tenant_id": "00000000-0000-0000-0000-000000000002",
                "total_amount": "99.00",
                "currency": "BRL",
                "correlation_id": "corr-retry",
                "gateway": "fake",
            },
        )

        gw.authorize.assert_called_once()
        session.flush.assert_not_called()
        retry_events = [
            c.args[0]
            for c in session.add.call_args_list
            if isinstance(c.args[0], OutboxEvent)
            and getattr(c.args[0], "event_type", None) == "payment.retry_exhausted"
        ]
        assert len(retry_events) == 1
        assert retry_events[0].payload["error_code"] == "card_declined"
        assert retry_events[0].aggregate_id == "o-retry"

    @patch("src.infrastructure.gateway.factory.get_gateway_for_tenant")
    @patch.object(payment_handlers, "_settings")
    @patch("src.worker.handlers.payments.set_correlation_id")
    def test_gateway_authorize_succeeds_after_one_retry(
        self,
        _mock_set_cid: MagicMock,
        mock_settings: MagicMock,
        mock_get_gateway: MagicMock,
    ) -> None:
        mock_settings.charge_request_max_retries = 1
        gw = MagicMock()
        gw.authorize = AsyncMock(
            side_effect=[
                GatewayResult(
                    success=False,
                    gateway_ref="",
                    status=GatewayStatus.FAILED,
                    error_code="timeout",
                    error_message="temporary",
                    is_retryable=True,
                ),
                GatewayResult(
                    success=True,
                    gateway_ref="gw-ref-ok",
                    status=GatewayStatus.AUTHORIZED,
                ),
            ]
        )
        mock_get_gateway.return_value = gw

        session = MagicMock()
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None
        session.execute.return_value = exec_result
        session.flush = MagicMock()

        with patch("src.worker.handlers.payments.time.sleep"):
            handle_charge_request(
                session,
                {
                    "order_id": "o-retry-ok",
                    "tenant_id": "00000000-0000-0000-0000-000000000002",
                    "total_amount": "50.00",
                    "currency": "BRL",
                    "gateway": "fake",
                },
            )

        assert gw.authorize.call_count == 2
        auth_events = [
            c.args[0]
            for c in session.add.call_args_list
            if isinstance(c.args[0], OutboxEvent)
            and getattr(c.args[0], "event_type", None) == "payment.authorized"
        ]
        assert len(auth_events) == 1
        session.flush.assert_called()
