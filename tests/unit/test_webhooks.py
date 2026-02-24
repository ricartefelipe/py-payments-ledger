from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.application.webhooks import (
    WebhookEndpointDTO,
    compute_signature,
    create_webhook_endpoint,
    delete_webhook_endpoint,
)


class TestCreateWebhookEndpoint:
    def test_creates_endpoint(self) -> None:
        session = MagicMock()
        session.begin.return_value.__enter__ = MagicMock(return_value=None)
        session.begin.return_value.__exit__ = MagicMock(return_value=False)
        session.flush = MagicMock()

        result = create_webhook_endpoint(
            session, "t1", "https://example.com/hook", ["payment.settled"]
        )
        assert result.url == "https://example.com/hook"
        assert result.is_active is True

    def test_delete_not_found_raises_404(self) -> None:
        import uuid
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None
        session.begin.return_value.__enter__ = MagicMock(return_value=None)
        session.begin.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(Exception) as exc_info:
            delete_webhook_endpoint(session, "t1", uuid.uuid4())
        assert exc_info.value.status_code == 404


class TestComputeSignature:
    def test_signature_is_deterministic(self) -> None:
        sig1 = compute_signature("secret", b'{"test": true}')
        sig2 = compute_signature("secret", b'{"test": true}')
        assert sig1 == sig2

    def test_different_secrets_produce_different_sigs(self) -> None:
        sig1 = compute_signature("secret1", b'{"test": true}')
        sig2 = compute_signature("secret2", b'{"test": true}')
        assert sig1 != sig2
