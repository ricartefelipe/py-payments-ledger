from __future__ import annotations

import time

from src.application.security import build_principal, decode_token
from src.shared.config import Settings


def test_decode_and_build_principal() -> None:
    settings = Settings(
        app_env="test",
        app_name="x",
        http_host="0.0.0.0",
        http_port=8000,
        database_url="sqlite://",
        redis_url="redis://localhost:6379/0",
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        jwt_secret="secret",
        jwt_issuer="local-auth",
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
    )

    import jwt

    now = int(time.time())
    claims = {
        "iss": settings.jwt_issuer,
        "sub": "ops@demo",
        "tid": "tenant_demo",
        "roles": ["ops"],
        "perms": ["payments:read"],
        "plan": "pro",
        "region": "region-a",
        "iat": now,
        "exp": now + 60,
        "jti": "jti-1",
        "ctx": {"k": "v"},
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
    decoded = decode_token(settings, token)
    principal = build_principal(decoded)

    assert principal.sub == "ops@demo"
    assert principal.tid == "tenant_demo"
    assert "payments:read" in principal.perms
