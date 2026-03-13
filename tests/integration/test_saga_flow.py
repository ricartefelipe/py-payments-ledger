"""Integration tests for the payment saga flow across services.

Validates that py-payments-ledger correctly handles events from
node-b2b-orders (order.confirmed, payment.charge_requested) and
tenant sync events from spring-saas-core.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch


from src.worker.handlers.charge_request import parse_charge_payload
from src.worker.handlers.payments import handle_charge_request, handle_event
from src.worker.handlers.tenants import handle_tenant_event


class TestChargeRequestSagaFlow:
    """Phase 1: order service publishes charge_requested → payments creates intent."""

    def _session_with_no_existing(self) -> MagicMock:
        session = MagicMock()
        ctx = MagicMock()
        session.begin.return_value = ctx
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=None)
        session.execute.return_value.scalar_one_or_none.return_value = None
        session.flush = MagicMock()
        return session

    def _session_with_existing(self) -> MagicMock:
        session = MagicMock()
        ctx = MagicMock()
        session.begin.return_value = ctx
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=None)
        existing = MagicMock()
        existing.id = uuid.uuid4()
        session.execute.return_value.scalar_one_or_none.return_value = existing
        session.flush = MagicMock()
        return session

    @patch("src.worker.handlers.payments.set_correlation_id")
    def test_charge_requested_creates_payment_intent_and_outbox(self, mock_cid: MagicMock) -> None:
        session = self._session_with_no_existing()

        payload = {
            "orderId": "order-saga-1",
            "tenantId": "tenant_demo",
            "totalAmount": "250.00",
            "currency": "BRL",
            "customerId": "CUST-1",
            "correlationId": "corr-saga",
            "items": [{"sku": "SKU-A", "qty": 2, "price": 125}],
        }

        handle_charge_request(session, payload)

        assert session.add.call_count == 2
        pi_call, outbox_call = session.add.call_args_list
        pi_obj = pi_call[0][0]
        outbox_obj = outbox_call[0][0]

        assert pi_obj.tenant_id == "tenant_demo"
        assert pi_obj.amount == Decimal("250.00")
        assert pi_obj.currency == "BRL"
        assert pi_obj.status == "AUTHORIZED"
        assert pi_obj.customer_ref == "order:order-saga-1"

        assert outbox_obj.event_type == "payment.authorized"
        assert outbox_obj.payload["order_id"] == "order-saga-1"

    @patch("src.worker.handlers.payments.set_correlation_id")
    def test_order_confirmed_triggers_same_charge_handler(self, mock_cid: MagicMock) -> None:
        session = self._session_with_no_existing()
        payload = {
            "orderId": "order-confirmed-1",
            "tenantId": "tenant_demo",
            "totalAmount": "100.00",
            "currency": "BRL",
            "customerId": "CUST-2",
            "correlationId": "corr-oc",
            "items": [{"sku": "SKU-B", "qty": 1, "price": 100}],
        }

        handle_event(session, "order.confirmed", payload)

        assert session.add.call_count == 2

    @patch("src.worker.handlers.payments.set_correlation_id")
    def test_idempotency_skips_duplicate_order(self, mock_cid: MagicMock) -> None:
        session = self._session_with_existing()
        payload = {
            "order_id": "order-dupe",
            "tenant_id": "tenant_demo",
            "total_amount": "100",
            "currency": "BRL",
            "correlation_id": "corr-dupe",
        }

        handle_charge_request(session, payload)

        session.flush.assert_not_called()

    def test_missing_order_id_returns_early(self) -> None:
        session = MagicMock()
        handle_charge_request(session, {"tenant_id": "t1", "total_amount": "10"})
        session.begin.assert_not_called()

    def test_missing_tenant_id_returns_early(self) -> None:
        session = MagicMock()
        handle_charge_request(session, {"order_id": "o1", "total_amount": "10"})
        session.begin.assert_not_called()


class TestPayloadNormalization:
    """Verifies resilient parsing of camelCase (from node-b2b-orders)
    and snake_case (internal) payloads."""

    def test_camel_case_from_node_orders(self) -> None:
        payload = {
            "orderId": "o1",
            "tenantId": "t1",
            "totalAmount": "350.00",
            "currency": "BRL",
            "customerId": "cust-1",
            "correlationId": "corr-1",
        }
        parsed = parse_charge_payload(payload)

        assert parsed["order_id"] == "o1"
        assert parsed["tenant_id"] == "t1"
        assert parsed["total_amount"] == "350.00"
        assert parsed["currency"] == "BRL"

    def test_snake_case_internal(self) -> None:
        payload = {
            "order_id": "o2",
            "tenant_id": "t2",
            "total_amount": "100.50",
            "currency": "USD",
            "customer_ref": "CUST-2",
            "correlation_id": "corr-2",
        }
        parsed = parse_charge_payload(payload)

        assert parsed["order_id"] == "o2"
        assert parsed["tenant_id"] == "t2"
        assert parsed["total_amount"] == "100.50"

    def test_defaults_for_missing_fields(self) -> None:
        parsed = parse_charge_payload({})
        assert parsed["order_id"] == ""
        assert parsed["tenant_id"] == ""
        assert parsed["total_amount"] == "0"
        assert parsed["currency"] == "BRL"


class TestEventDispatchRouting:
    """Verifies handle_event routes to correct handler by routing key."""

    @patch("src.worker.handlers.payments.handle_charge_request")
    def test_payment_charge_requested_routes_to_charge_handler(
        self, mock_handler: MagicMock
    ) -> None:
        session = MagicMock()
        payload = {"order_id": "o1", "tenant_id": "t1"}
        handle_event(session, "payment.charge_requested", payload)
        mock_handler.assert_called_once_with(session, payload)

    @patch("src.worker.handlers.payments.handle_charge_request")
    def test_order_confirmed_routes_to_charge_handler(self, mock_handler: MagicMock) -> None:
        session = MagicMock()
        payload = {"orderId": "o1", "tenantId": "t1"}
        handle_event(session, "order.confirmed", payload)
        mock_handler.assert_called_once_with(session, payload)

    @patch("src.worker.handlers.payments.post_ledger_for_authorized_payment")
    def test_payment_authorized_routes_to_ledger(self, mock_ledger: MagicMock) -> None:
        session = MagicMock()
        pi_id = str(uuid.uuid4())
        handle_event(
            session,
            "payment.authorized",
            {
                "payment_intent_id": pi_id,
                "tenant_id": "t1",
            },
        )
        mock_ledger.assert_called_once()

    def test_unknown_event_is_ignored(self) -> None:
        session = MagicMock()
        handle_event(session, "unknown.event", {"foo": "bar"})


class TestTenantSyncSaga:
    """Validates tenant lifecycle sync from spring-saas-core."""

    def _make_session(self, existing: object | None = None) -> MagicMock:
        session = MagicMock()
        session.get.return_value = existing
        session.begin.return_value.__enter__ = MagicMock(return_value=None)
        session.begin.return_value.__exit__ = MagicMock(return_value=False)
        session.flush = MagicMock()
        return session

    @patch("src.worker.handlers.tenants.seed_default_accounts")
    def test_tenant_created_provisions_accounts(self, mock_seed: MagicMock) -> None:
        session = self._make_session(existing=None)
        handle_tenant_event(
            session,
            "tenant.created",
            {
                "tenant_id": "t_new",
                "name": "Test Tenant",
                "plan": "pro",
                "region": "region-a",
            },
        )
        session.add.assert_called_once()
        mock_seed.assert_called_once_with(session, "t_new")

    def test_tenant_created_idempotent(self) -> None:
        existing = MagicMock()
        session = self._make_session(existing=existing)
        handle_tenant_event(session, "tenant.created", {"tenant_id": "t_existing"})
        session.add.assert_not_called()

    def test_tenant_updated_modifies_fields(self) -> None:
        tenant = MagicMock()
        tenant.name = "Old"
        tenant.plan = "basic"
        session = self._make_session(existing=tenant)
        handle_tenant_event(
            session,
            "tenant.updated",
            {
                "tenant_id": "t1",
                "name": "Updated",
                "plan": "enterprise",
            },
        )
        assert tenant.name == "Updated"
        assert tenant.plan == "enterprise"

    def test_tenant_deleted_soft_deletes(self) -> None:
        tenant = MagicMock()
        tenant.name = "Active Tenant"
        session = self._make_session(existing=tenant)
        handle_tenant_event(session, "tenant.deleted", {"tenant_id": "t1"})
        assert tenant.name == "[DELETED] Active Tenant"

    def test_missing_tenant_id_is_rejected(self) -> None:
        session = self._make_session()
        handle_tenant_event(session, "tenant.created", {})
        session.add.assert_not_called()


class TestFullSagaRoundTrip:
    """End-to-end: order.confirmed → payment intent → payment.authorized outbox."""

    @patch("src.worker.handlers.payments.set_correlation_id")
    def test_order_confirmed_produces_authorized_outbox_event(self, mock_cid: MagicMock) -> None:
        session = MagicMock()
        ctx = MagicMock()
        session.begin.return_value = ctx
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=None)
        session.execute.return_value.scalar_one_or_none.return_value = None
        session.flush = MagicMock()

        payload = {
            "orderId": "saga-order-full",
            "tenantId": "tenant_demo",
            "totalAmount": "500.00",
            "currency": "BRL",
            "customerId": "CUST-SAGA",
            "correlationId": "corr-full-saga",
            "items": [
                {"sku": "SKU-A", "qty": 5, "price": 50},
                {"sku": "SKU-B", "qty": 5, "price": 50},
            ],
        }

        handle_event(session, "order.confirmed", payload)

        assert session.add.call_count == 2

        pi_obj = session.add.call_args_list[0][0][0]
        outbox_obj = session.add.call_args_list[1][0][0]

        assert pi_obj.amount == Decimal("500.00")
        assert pi_obj.status == "AUTHORIZED"

        assert outbox_obj.event_type == "payment.authorized"
        assert outbox_obj.aggregate_type == "PaymentIntent"
        assert outbox_obj.payload["order_id"] == "saga-order-full"
        assert outbox_obj.payload["correlation_id"] == "corr-full-saga"
