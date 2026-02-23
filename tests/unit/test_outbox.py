"""Unit tests for outbox claim and backoff."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.application.outbox import claim_events, mark_failed, mark_sent
from src.infrastructure.db.models import OutboxEvent


def _mock_event(
    id: str = "e1",
    status: str = "PENDING",
    locked_at: datetime | None = None,
    attempts: int = 0,
) -> MagicMock:
    e = MagicMock(spec=OutboxEvent)
    e.id = id
    e.tenant_id = "tenant_demo"
    e.event_type = "payment.authorized"
    e.aggregate_type = "PaymentIntent"
    e.aggregate_id = "pi-1"
    e.payload = {"payment_intent_id": "pi-1", "tenant_id": "tenant_demo"}
    e.status = status
    e.attempts = attempts
    e.available_at = datetime.now(timezone.utc)
    e.locked_at = locked_at
    e.locked_by = None
    return e


def test_claim_events_returns_claimed_list() -> None:
    mock_event = _mock_event()
    mock_session = MagicMock()
    chain = MagicMock()
    chain.scalars.return_value.all.return_value = [mock_event]
    mock_session.execute.return_value = chain
    mock_session.begin.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_session.begin.return_value.__exit__ = MagicMock(return_value=None)

    result = claim_events(mock_session, "worker-1", limit=10)

    assert len(result) == 1
    assert result[0].id == "e1"
    assert result[0].event_type == "payment.authorized"
    assert result[0].attempts == 0


def test_mark_sent_clears_lock() -> None:
    mock_session = MagicMock()
    mock_event = MagicMock()
    mock_session.get.return_value = mock_event
    mock_session.begin.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_session.begin.return_value.__exit__ = MagicMock(return_value=None)

    mark_sent(mock_session, "e1")

    mock_session.begin.assert_called()
    assert mock_event.status == "SENT"
    assert mock_event.locked_at is None
    assert mock_event.locked_by is None


def test_mark_failed_increments_attempts() -> None:
    mock_session = MagicMock()
    mock_event = MagicMock()
    mock_event.attempts = 2
    mock_session.get.return_value = mock_event
    mock_session.begin.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_session.begin.return_value.__exit__ = MagicMock(return_value=None)

    mark_failed(mock_session, "e1", max_attempts=7)

    assert mock_event.attempts == 3
    assert mock_event.locked_at is None


def test_mark_failed_sets_dead_at_max_attempts() -> None:
    mock_session = MagicMock()
    mock_event = MagicMock()
    mock_event.attempts = 6
    mock_session.get.return_value = mock_event
    mock_session.begin.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_session.begin.return_value.__exit__ = MagicMock(return_value=None)

    mark_failed(mock_session, "e1", max_attempts=7)

    assert mock_event.status == "DEAD"

