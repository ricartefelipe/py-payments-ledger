"""Integration tests for the payment saga flow across services.

Validates that py-payments-ledger correctly handles events from
node-b2b-orders (order.confirmed, payment.charge_requested) and
tenant sync events from spring-saas-core.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.infrastructure.db.models import OutboxEvent, PaymentIntent
from src.worker.handlers import payments as payment_handlers
from src.worker.handlers.charge_request import parse_charge_payload
from src.worker.handlers.payments import handle_charge_request, handle_event
from src.worker.handlers.tenants import handle_tenant_event
from tests.integration.test_charge_request_flow import RecordingGateway


@pytest.fixture
def saga_gateway(monkeypatch: pytest.MonkeyPatch) -> RecordingGateway:
    """Gateway de teste instalado no modulo do worker."""
    gw = RecordingGateway()
    monkeypatch.setattr(payment_handlers, "_gateway", gw, raising=False)
    monkeypatch.setattr(payment_handlers, "_settings", None, raising=False)
    return gw


def _intents(session: Session, tenant_id: str) -> list[PaymentIntent]:
    session.expire_all()
    return list(
        session.execute(select(PaymentIntent).where(PaymentIntent.tenant_id == tenant_id))
        .scalars()
        .all()
    )


def _events(session: Session, tenant_id: str, event_type: str) -> list[OutboxEvent]:
    session.expire_all()
    return list(
        session.execute(
            select(OutboxEvent).where(
                OutboxEvent.tenant_id == tenant_id, OutboxEvent.event_type == event_type
            )
        )
        .scalars()
        .all()
    )


class TestChargeRequestSagaFlow:
    """Fase 1: Orders publica charge_requested -> payments cria a intencao.

    Estes testes rodam contra PostgreSQL real. O handler passou a persistir a
    intencao, commitar, falar com o provedor fora da transacao e finalizar com
    compare-and-set: uma ``MagicMock`` de sessao nao exercita esse caminho e
    aprovaria um fluxo que nao funciona.
    """

    def test_charge_requested_creates_payment_intent_and_outbox(
        self, pg_session: Session, tenant_id: str, encryption_on: None, saga_gateway
    ) -> None:
        payload = {
            "orderId": "order-saga-1",
            "tenantId": tenant_id,
            "totalAmount": "250.00",
            "currency": "BRL",
            "customerId": "CUST-1",
            "correlationId": "corr-saga",
            "items": [{"sku": "SKU-A", "qty": 2, "price": 125}],
        }

        handle_charge_request(pg_session, payload)
        pg_session.expire_all()

        intents = _intents(pg_session, tenant_id)
        assert len(intents) == 1
        pi_obj = intents[0]
        assert pi_obj.tenant_id == tenant_id
        assert Decimal(str(pi_obj.amount)) == Decimal("250.00")
        assert pi_obj.currency == "BRL"
        assert pi_obj.status == "AUTHORIZED"
        assert pi_obj.customer_ref == "order:order-saga-1"

        events = _events(pg_session, tenant_id, "payment.authorized")
        assert len(events) == 1
        assert events[0].payload["order_id"] == "order-saga-1"
        assert events[0].payload["correlation_id"] == "corr-saga"

    def test_order_confirmed_triggers_same_charge_handler(
        self, pg_session: Session, tenant_id: str, encryption_on: None, saga_gateway
    ) -> None:
        """``order.confirmed`` e ``payment.charge_requested`` convergem."""
        payload = {
            "orderId": "order-saga-2",
            "tenantId": tenant_id,
            "totalAmount": "80.00",
            "currency": "BRL",
            "correlationId": "corr-saga-2",
        }

        handle_event(pg_session, "order.confirmed", payload)
        pg_session.expire_all()

        intents = _intents(pg_session, tenant_id)
        assert len(intents) == 1
        assert intents[0].customer_ref == "order:order-saga-2"
        assert intents[0].status == "AUTHORIZED"

    def test_idempotency_skips_duplicate_order(
        self, pg_session: Session, tenant_id: str, encryption_on: None, saga_gateway
    ) -> None:
        """Reentrega do mesmo pedido nao cria segundo pagamento nem recobra."""
        payload = {
            "orderId": "order-saga-dup",
            "tenantId": tenant_id,
            "totalAmount": "40.00",
            "currency": "BRL",
            "correlationId": "corr-dup",
        }

        handle_charge_request(pg_session, payload)
        handle_charge_request(pg_session, payload)
        pg_session.expire_all()

        assert len(_intents(pg_session, tenant_id)) == 1
        assert len(saga_gateway.calls) == 1

    def test_missing_order_id_returns_early(self, pg_session: Session, tenant_id: str) -> None:
        handle_charge_request(pg_session, {"tenantId": tenant_id, "totalAmount": "10"})
        assert _intents(pg_session, tenant_id) == []

    def test_missing_tenant_id_returns_early(self, pg_session: Session, tenant_id: str) -> None:
        handle_charge_request(pg_session, {"orderId": "o-x", "totalAmount": "10"})
        assert _intents(pg_session, tenant_id) == []


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
    """Ponta a ponta: order.confirmed -> intencao -> evento payment.authorized."""

    def test_order_confirmed_produces_authorized_outbox_event(
        self, pg_session: Session, tenant_id: str, encryption_on: None, saga_gateway
    ) -> None:
        payload = {
            "orderId": "saga-order-full",
            "tenantId": tenant_id,
            "totalAmount": "500.00",
            "currency": "BRL",
            "customerId": "CUST-SAGA",
            "correlationId": "corr-full-saga",
            "items": [
                {"sku": "SKU-A", "qty": 5, "price": 50},
                {"sku": "SKU-B", "qty": 5, "price": 50},
            ],
        }

        handle_event(pg_session, "order.confirmed", payload)
        pg_session.expire_all()

        intents = _intents(pg_session, tenant_id)
        assert len(intents) == 1
        assert Decimal(str(intents[0].amount)) == Decimal("500.00")
        assert intents[0].status == "AUTHORIZED"

        events = _events(pg_session, tenant_id, "payment.authorized")
        assert len(events) == 1
        assert events[0].aggregate_type == "PaymentIntent"
        assert events[0].payload["order_id"] == "saga-order-full"
        assert events[0].payload["correlation_id"] == "corr-full-saga"
        # O contrato com Orders inclui a identidade do pagamento no evento.
        assert events[0].payload["payment_intent_id"] == str(intents[0].id)
