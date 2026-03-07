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

TENANT_ID = "tenant_test"
JWT_SECRET = "test-secret"
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


@pytest.fixture()
def client(mock_db: MagicMock, mock_redis: MagicMock) -> Generator[TestClient, None, None]:
    with (
        patch("src.infrastructure.db.session.init_db"),
        patch("src.infrastructure.redis.client.init_redis"),
        patch("src.infrastructure.redis.client._client", mock_redis),
        patch("src.api.deps.auth.authorize"),
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
