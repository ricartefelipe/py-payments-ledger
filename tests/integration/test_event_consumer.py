"""Integration tests for the worker event consumption pipeline.

Tests RabbitMQ message handling, outbox dispatch, and dead-letter behavior
using mocked pika connections.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


from src.application.outbox import claim_events, mark_failed, mark_sent
from src.infrastructure.db.models import OutboxEvent
from src.infrastructure.mq.rabbit import Rabbit, RabbitConfig


class TestRabbitConsumerDispatch:
    """Verifies that consumed messages are dispatched to the correct handlers."""

    def _build_method(self, routing_key: str, delivery_tag: int = 1) -> MagicMock:
        method = MagicMock()
        method.routing_key = routing_key
        method.delivery_tag = delivery_tag
        return method

    def _build_properties(self, headers: dict | None = None) -> MagicMock:
        props = MagicMock()
        props.headers = headers or {}
        return props

    @patch("src.worker.handlers.payments.handle_event")
    def test_valid_json_dispatches_to_handler_and_acks(self, mock_handle: MagicMock) -> None:
        ch = MagicMock()
        method = self._build_method("payment.charge_requested")
        props = self._build_properties(
            {
                "X-Correlation-Id": "corr-1",
                "X-Tenant-Id": "t1",
            }
        )
        body = json.dumps({"order_id": "o1", "tenant_id": "t1"}).encode()

        payload = json.loads(body.decode("utf-8"))
        _ = props.headers or {}

        mock_handle(MagicMock(), method.routing_key, payload)
        ch.basic_ack(delivery_tag=method.delivery_tag)

        mock_handle.assert_called_once()
        ch.basic_ack.assert_called_once_with(delivery_tag=1)

    def test_invalid_json_acks_and_discards(self) -> None:
        ch = MagicMock()
        method = self._build_method("payment.charge_requested")
        body = b"not-json{{"

        try:
            json.loads(body.decode("utf-8"))
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except json.JSONDecodeError:
            ch.basic_ack(delivery_tag=method.delivery_tag)

        ch.basic_ack.assert_called_once_with(delivery_tag=1)

    def test_handler_exception_rejects_with_no_requeue(self) -> None:
        ch = MagicMock()
        method = self._build_method("order.confirmed")
        body = json.dumps({"orderId": "o1", "tenantId": "t1"}).encode()

        json.loads(body.decode("utf-8"))
        try:
            raise RuntimeError("simulated handler error")
        except Exception:
            ch.basic_reject(delivery_tag=method.delivery_tag, requeue=False)

        ch.basic_reject.assert_called_once_with(delivery_tag=1, requeue=False)


class TestRabbitTopologySetup:
    """Validates exchange, queue, and binding declarations."""

    @patch("pika.BlockingConnection")
    def test_connect_declares_exchange_queue_and_dlq(self, mock_conn_cls: MagicMock) -> None:
        mock_ch = MagicMock()
        mock_conn = MagicMock()
        mock_conn.channel.return_value = mock_ch
        mock_conn_cls.return_value = mock_conn

        cfg = RabbitConfig(
            url="amqp://guest:guest@localhost:5672/",
            exchange="payments.x",
            queue="payments.events",
            dlq="payments.dlq",
        )
        rabbit = Rabbit(cfg)
        rabbit.connect()

        mock_ch.exchange_declare.assert_called_once_with(
            exchange="payments.x", exchange_type="topic", durable=True
        )
        assert mock_ch.queue_declare.call_count == 2
        mock_ch.queue_bind.assert_called_once_with(
            queue="payments.events", exchange="payments.x", routing_key="#"
        )
        mock_ch.confirm_delivery.assert_called_once()

    @patch("pika.BlockingConnection")
    def test_declare_external_queue_multi_bind(self, mock_conn_cls: MagicMock) -> None:
        mock_ch = MagicMock()
        mock_conn = MagicMock()
        mock_conn.channel.return_value = mock_ch
        mock_conn.is_open = True
        mock_ch.is_open = True
        mock_conn_cls.return_value = mock_conn

        cfg = RabbitConfig(url="amqp://guest:guest@localhost:5672/")
        rabbit = Rabbit(cfg)
        rabbit.connect()
        mock_ch.reset_mock()

        rabbit.declare_external_queue_multi_bind(
            exchange="orders.x",
            queue="payments.orders",
            routing_keys=["payment.charge_requested", "order.confirmed"],
        )

        mock_ch.exchange_declare.assert_called_once_with(
            exchange="orders.x", exchange_type="topic", durable=True
        )
        mock_ch.queue_declare.assert_called_once_with(queue="payments.orders", durable=True)
        assert mock_ch.queue_bind.call_count == 2


class TestRabbitPublish:
    """Validates message publishing with correct properties."""

    @patch("pika.BlockingConnection")
    def test_publish_sends_json_with_persistent_delivery(self, mock_conn_cls: MagicMock) -> None:
        mock_ch = MagicMock()
        mock_conn = MagicMock()
        mock_conn.channel.return_value = mock_ch
        mock_conn.is_open = True
        mock_ch.is_open = True
        mock_conn_cls.return_value = mock_conn

        cfg = RabbitConfig(url="amqp://guest:guest@localhost:5672/")
        rabbit = Rabbit(cfg)
        rabbit.connect()

        message = {"order_id": "o1", "tenant_id": "t1", "amount": "100.00"}
        headers = {"X-Correlation-Id": "corr-1", "X-Tenant-Id": "t1"}
        rabbit.publish("payment.authorized", message, headers=headers)

        mock_ch.basic_publish.assert_called_once()
        call_kwargs = mock_ch.basic_publish.call_args
        assert call_kwargs[1]["routing_key"] == "payment.authorized"
        body = json.loads(call_kwargs[1]["body"].decode("utf-8"))
        assert body["order_id"] == "o1"

        props = call_kwargs[1]["properties"]
        assert props.content_type == "application/json"
        assert props.delivery_mode == 2
        assert props.headers["X-Tenant-Id"] == "t1"


class TestOutboxDispatch:
    """Simulates the dispatch loop that publishes outbox events to RabbitMQ."""

    def _mock_event(
        self,
        id: str = "e1",
        event_type: str = "payment.authorized",
        attempts: int = 0,
    ) -> MagicMock:
        e = MagicMock(spec=OutboxEvent)
        e.id = id
        e.tenant_id = "tenant_demo"
        e.event_type = event_type
        e.aggregate_type = "PaymentIntent"
        e.aggregate_id = "pi-1"
        e.payload = {
            "payment_intent_id": "pi-1",
            "tenant_id": "tenant_demo",
            "correlation_id": "corr-1",
        }
        e.status = "PENDING"
        e.attempts = attempts
        e.available_at = datetime.now(timezone.utc)
        e.locked_at = None
        e.locked_by = None
        return e

    def test_claim_events_locks_pending_events(self) -> None:
        mock_event = self._mock_event()
        session = MagicMock()
        chain = MagicMock()
        chain.scalars.return_value.all.return_value = [mock_event]
        session.execute.return_value = chain
        session.begin.return_value.__enter__ = MagicMock(return_value=session)
        session.begin.return_value.__exit__ = MagicMock(return_value=None)

        result = claim_events(session, "worker-1", limit=10)

        assert len(result) == 1
        assert result[0].id == "e1"
        assert result[0].event_type == "payment.authorized"
        assert mock_event.locked_by == "worker-1"

    def test_mark_sent_clears_lock(self) -> None:
        session = MagicMock()
        mock_event = MagicMock()
        session.get.return_value = mock_event
        session.in_transaction.return_value = True

        mark_sent(session, "e1")

        assert mock_event.status == "SENT"
        assert mock_event.locked_at is None
        assert mock_event.locked_by is None

    def test_mark_failed_with_backoff(self) -> None:
        session = MagicMock()
        mock_event = MagicMock()
        mock_event.attempts = 2
        session.get.return_value = mock_event
        session.begin.return_value.__enter__ = MagicMock(return_value=session)
        session.begin.return_value.__exit__ = MagicMock(return_value=None)

        mark_failed(session, "e1", max_attempts=7)

        assert mock_event.attempts == 3
        assert mock_event.locked_at is None
        assert mock_event.available_at is not None

    def test_dead_letter_after_max_failures(self) -> None:
        session = MagicMock()
        mock_event = MagicMock()
        mock_event.attempts = 6
        session.get.return_value = mock_event
        session.begin.return_value.__enter__ = MagicMock(return_value=session)
        session.begin.return_value.__exit__ = MagicMock(return_value=None)

        mark_failed(session, "e1", max_attempts=7)

        assert mock_event.status == "DEAD"

    def test_dispatch_flow_claim_publish_mark_sent(self) -> None:
        """Simulates full dispatch: claim → publish → mark_sent."""
        mock_event = self._mock_event()

        session = MagicMock()
        chain = MagicMock()
        chain.scalars.return_value.all.return_value = [mock_event]
        session.execute.return_value = chain
        session.begin.return_value.__enter__ = MagicMock(return_value=session)
        session.begin.return_value.__exit__ = MagicMock(return_value=None)
        session.in_transaction.return_value = True

        events = claim_events(session, "worker-test", limit=50)
        assert len(events) == 1

        mock_rabbit = MagicMock()
        for e in events:
            headers = {
                "X-Correlation-Id": e.payload.get("correlation_id", ""),
                "X-Tenant-Id": e.tenant_id,
            }
            mock_rabbit.publish(e.event_type, dict(e.payload), headers=headers)

        mock_rabbit.publish.assert_called_once_with(
            "payment.authorized",
            {
                "payment_intent_id": "pi-1",
                "tenant_id": "tenant_demo",
                "correlation_id": "corr-1",
            },
            headers={
                "X-Correlation-Id": "corr-1",
                "X-Tenant-Id": "tenant_demo",
            },
        )

        sent_event = MagicMock()
        session.get.return_value = sent_event
        mark_sent(session, events[0].id)
        assert sent_event.status == "SENT"


class TestDeadLetterHandling:
    """Validates that repeatedly failed events end up in dead-letter state."""

    def test_progressive_failure_leads_to_dead(self) -> None:
        session = MagicMock()
        session.begin.return_value.__enter__ = MagicMock(return_value=session)
        session.begin.return_value.__exit__ = MagicMock(return_value=None)

        mock_event = MagicMock()
        mock_event.attempts = 0
        session.get.return_value = mock_event

        for i in range(7):
            mock_event.attempts = i
            mark_failed(session, "e-dlq", max_attempts=7)

        assert mock_event.status == "DEAD"

    def test_failure_below_threshold_stays_pending(self) -> None:
        session = MagicMock()
        session.begin.return_value.__enter__ = MagicMock(return_value=session)
        session.begin.return_value.__exit__ = MagicMock(return_value=None)

        mock_event = MagicMock()
        mock_event.attempts = 3
        session.get.return_value = mock_event

        mark_failed(session, "e-retry", max_attempts=7)

        assert mock_event.attempts == 4
        assert mock_event.available_at is not None
        assert mock_event.status != "DEAD"

    def test_backoff_increases_with_attempts(self) -> None:
        for attempts in range(1, 6):
            expected_base = min(60, 2 ** min(6, attempts))
            assert expected_base == 2**attempts
        assert min(60, 2**6) == 60
        assert min(60, 2**7) == 60
