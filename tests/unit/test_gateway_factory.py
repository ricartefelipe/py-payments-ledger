"""Tests for gateway factory - provider selection and fallback to fake."""

from __future__ import annotations

import os
from unittest.mock import patch

from src.infrastructure.gateway.fake import FakeGatewayAdapter
from src.infrastructure.gateway.factory import (
    create_gateway,
    create_gateway_by_provider,
)
from src.shared.config import load_settings
from tests.test_constants import TEST_JWT_HS256_SECRET


class TestCreateGateway:
    def test_fake_provider_returns_fake_adapter(self) -> None:
        env = {
            "APP_ENV": "test",
            "DATABASE_URL": "postgresql+psycopg://a:a@localhost/a",
            "REDIS_URL": "redis://localhost",
            "RABBITMQ_URL": "amqp://localhost",
            "JWT_SECRET": TEST_JWT_HS256_SECRET,
            "GATEWAY_PROVIDER": "fake",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        adapter = create_gateway(settings)
        assert isinstance(adapter, FakeGatewayAdapter)

    def test_default_provider_is_fake(self) -> None:
        """When GATEWAY_PROVIDER not set or fake, returns FakeGatewayAdapter."""
        env = {
            "APP_ENV": "test",
            "DATABASE_URL": "postgresql+psycopg://a:a@localhost/a",
            "REDIS_URL": "redis://localhost",
            "RABBITMQ_URL": "amqp://localhost",
            "JWT_SECRET": TEST_JWT_HS256_SECRET,
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        adapter = create_gateway(settings)
        assert isinstance(adapter, FakeGatewayAdapter)


class TestCreateGatewayByProvider:
    def test_stripe_provider_without_key_returns_fake(self) -> None:
        env = {
            "APP_ENV": "test",
            "DATABASE_URL": "postgresql+psycopg://a:a@localhost/a",
            "REDIS_URL": "redis://localhost",
            "RABBITMQ_URL": "amqp://localhost",
            "JWT_SECRET": TEST_JWT_HS256_SECRET,
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        adapter = create_gateway_by_provider(settings, "stripe")
        assert isinstance(adapter, FakeGatewayAdapter)

    def test_fake_provider_returns_fake(self) -> None:
        env = {
            "APP_ENV": "test",
            "DATABASE_URL": "postgresql+psycopg://a:a@localhost/a",
            "REDIS_URL": "redis://localhost",
            "RABBITMQ_URL": "amqp://localhost",
            "JWT_SECRET": TEST_JWT_HS256_SECRET,
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        adapter = create_gateway_by_provider(settings, "fake")
        assert isinstance(adapter, FakeGatewayAdapter)
