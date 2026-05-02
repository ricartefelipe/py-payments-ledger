"""Unit tests for configuration validation."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.shared.config import Settings, load_settings
from tests.test_constants import TEST_JWT_HS256_SECRET


class TestLoadSettings:
    def test_missing_jwt_secret_raises_value_error(self) -> None:
        env = {
            "APP_ENV": "test",
            "DATABASE_URL": "postgresql+psycopg://a:a@localhost/a",
            "REDIS_URL": "redis://localhost",
            "RABBITMQ_URL": "amqp://localhost",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="JWT_SECRET"):
                load_settings()

    def test_stripe_provider_without_api_key_raises_value_error(self) -> None:
        env = {
            "APP_ENV": "test",
            "DATABASE_URL": "postgresql+psycopg://a:a@localhost/a",
            "REDIS_URL": "redis://localhost",
            "RABBITMQ_URL": "amqp://localhost",
            "JWT_SECRET": TEST_JWT_HS256_SECRET,
            "GATEWAY_PROVIDER": "stripe",
            "STRIPE_API_KEY": "",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="STRIPE_API_KEY"):
                load_settings()

    def test_valid_config_loads_successfully(self) -> None:
        env = {
            "APP_ENV": "test",
            "DATABASE_URL": "postgresql+psycopg://a:a@localhost/a",
            "REDIS_URL": "redis://localhost",
            "RABBITMQ_URL": "amqp://localhost",
            "JWT_SECRET": TEST_JWT_HS256_SECRET,
            "JWT_ISSUER": "test-issuer",
            "GATEWAY_PROVIDER": "fake",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()

        assert isinstance(settings, Settings)
        assert settings.app_env == "test"
        assert settings.jwt_secret == TEST_JWT_HS256_SECRET
        assert settings.gateway_provider == "fake"

    def test_postgresql_url_normalized_for_psycopg(self) -> None:
        env = {
            "APP_ENV": "test",
            "DATABASE_URL": "postgresql://user:pass@host:5432/db",
            "REDIS_URL": "redis://localhost",
            "RABBITMQ_URL": "amqp://localhost",
            "JWT_SECRET": TEST_JWT_HS256_SECRET,
            "GATEWAY_PROVIDER": "fake",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        assert settings.database_url == "postgresql+psycopg://user:pass@host:5432/db"

    def test_stripe_provider_with_valid_key_loads(self) -> None:
        env = {
            "APP_ENV": "test",
            "DATABASE_URL": "postgresql+psycopg://a:a@localhost/a",
            "REDIS_URL": "redis://localhost",
            "RABBITMQ_URL": "amqp://localhost",
            "JWT_SECRET": TEST_JWT_HS256_SECRET,
            "GATEWAY_PROVIDER": "stripe",
            "STRIPE_API_KEY": "sk_test_valid_key",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()

        assert settings.gateway_provider == "stripe"
        assert settings.stripe_api_key == "sk_test_valid_key"

    def test_default_values_are_applied(self) -> None:
        env = {
            "APP_ENV": "local",
            "JWT_SECRET": TEST_JWT_HS256_SECRET,
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()

        assert settings.app_name == "py-payments-ledger"
        assert settings.http_host == "0.0.0.0"
        assert settings.http_port == 8000
        assert settings.gateway_provider == "fake"
        assert settings.chaos_enabled is False
        assert settings.idempotency_ttl_seconds == 86400
        assert settings.circuit_breaker_failure_threshold == 5
        assert settings.circuit_breaker_recovery_timeout == 30.0
        assert settings.readiness_require_rabbit is True

    def test_boolean_env_vars_parse_correctly(self) -> None:
        env = {
            "JWT_SECRET": TEST_JWT_HS256_SECRET,
            "CHAOS_ENABLED": "true",
            "ORDERS_INTEGRATION_ENABLED": "True",
            "SAAS_INTEGRATION_ENABLED": "FALSE",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()

        assert settings.chaos_enabled is True
        assert settings.orders_integration_enabled is True
        assert settings.saas_integration_enabled is False

    def test_list_env_vars_parse_correctly(self) -> None:
        env = {
            "JWT_SECRET": TEST_JWT_HS256_SECRET,
            "ORDERS_ROUTING_KEYS": "payment.charge_requested, order.confirmed",
            "CORS_ORIGINS": "http://localhost:4200, https://app.example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()

        assert settings.orders_routing_keys == ["payment.charge_requested", "order.confirmed"]
        assert settings.cors_origins == ["http://localhost:4200", "https://app.example.com"]
