"""Shared fixtures for API / router tests.

Provides a FastAPI TestClient with all external dependencies (DB, Redis,
RabbitMQ, ABAC authorize) mocked so that router-level behaviour can be
tested in isolation.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from tests.test_constants import TEST_JWT_HS256_SECRET

TENANT_ID = "tenant_test"
JWT_SECRET = TEST_JWT_HS256_SECRET
JWT_ISSUER = "test-issuer"


def _make_token(
    sub: str = "user@test.com",
    tid: str = TENANT_ID,
    roles: list[str] | None = None,
    perms: list[str] | None = None,
    plan: str = "pro",
    region: str = "region-a",
) -> str:
    now = int(time.time())
    claims = {
        "iss": JWT_ISSUER,
        "sub": sub,
        "tid": tid,
        "roles": roles or ["operator"],
        "perms": perms or ["payments:read", "payments:write", "audit:read"],
        "plan": plan,
        "region": region,
        "iat": now,
        "exp": now + 3600,
        "jti": f"test.{now}",
        "ctx": {"email": sub},
    }
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


@pytest.fixture()
def mock_db() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def mock_redis() -> MagicMock:
    r = MagicMock()
    r.ping.return_value = True
    r.get.return_value = None
    return r


def _test_settings() -> "Settings":
    from src.shared.config import Settings

    return Settings(
        app_env="test",
        app_name="py-payments-ledger",
        http_host="0.0.0.0",
        http_port=8000,
        database_url="postgresql+psycopg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/1",
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        jwt_secret=JWT_SECRET,
        jwt_secret_previous="",
        jwt_issuer=JWT_ISSUER,
        jwt_algorithm="HS256",
        jwt_public_key="",
        jwks_uri="",
        token_expires_seconds=3600,
        rate_limit_write_per_min=60,
        rate_limit_read_per_min=240,
        chaos_enabled=False,
        chaos_fail_percent=0,
        chaos_latency_ms=0,
        idempotency_ttl_seconds=86400,
        orders_integration_enabled=False,
        orders_exchange="orders.x",
        orders_queue="payments.orders.events",
        orders_routing_keys=["payment.charge_requested", "order.confirmed"],
        cors_origins=[],
        gateway_provider="fake",
        stripe_api_key="",
        stripe_webhook_secret="",
        pagseguro_token="",
        pagseguro_api_url="https://api.pagseguro.com",
        mercadopago_access_token="",
        mercadopago_api_url="https://api.mercadopago.com",
        pagseguro_webhook_secret="",
        mercadopago_webhook_secret="",
        gateway_max_retries=3,
        gateway_retry_base_delay=1.0,
        gateway_retry_max_delay=30.0,
        circuit_breaker_failure_threshold=5,
        circuit_breaker_recovery_timeout=30.0,
        saas_integration_enabled=False,
        saas_exchange="saas.x",
        saas_queue="payments.saas.events",
        saas_routing_keys=["tenant.created", "tenant.updated", "tenant.deleted"],
        webhook_delivery_enabled=False,
        reconciliation_interval_minutes=60,
        reconciliation_enabled=False,
        report_refresh_interval_minutes=15,
        audit_retention_days=90,
        charge_request_max_retries=3,
        encryption_key="test-32-bytes-encryption-key!!",
    )


@pytest.fixture()
def client(mock_db: MagicMock, mock_redis: MagicMock) -> Generator[TestClient, None, None]:
    with (
        patch("src.infrastructure.db.session.init_db"),
        patch("src.infrastructure.redis.client.init_redis"),
        patch("src.infrastructure.redis.client._client", mock_redis),
        patch("src.api.deps.auth.authorize"),
        patch("src.api.main.load_settings", return_value=_test_settings()),
    ):
        from src.api.deps.db import get_db
        from src.api.main import create_app

        app = create_app()

        def _override_db() -> Generator[MagicMock, None, None]:
            yield mock_db

        app.dependency_overrides[get_db] = _override_db

        with TestClient(app, raise_server_exceptions=False) as tc:
            yield tc


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_make_token()}",
        "X-Tenant-Id": TENANT_ID,
    }


@pytest.fixture()
def admin_headers() -> dict[str, str]:
    token = _make_token(
        sub="admin@test.com",
        tid="*",
        roles=["admin"],
        perms=["payments:read", "payments:write", "audit:read", "admin:all"],
        plan="enterprise",
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT_ID}


@pytest.fixture()
def token_factory():
    """Return callable ``_make_token`` so tests can mint arbitrary JWTs."""
    return _make_token
