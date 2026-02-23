from __future__ import annotations

import os
from dataclasses import dataclass


def _getenv(name: str, default: str | None = None) -> str:
    val = os.getenv(name)
    if val is None:
        if default is None:
            raise RuntimeError(f"Missing env var: {name}")
        return default
    return val


@dataclass(frozen=True)
class Settings:
    app_env: str
    app_name: str
    http_host: str
    http_port: int

    database_url: str
    redis_url: str
    rabbitmq_url: str

    jwt_secret: str
    jwt_issuer: str
    token_expires_seconds: int

    rate_limit_write_per_min: int
    rate_limit_read_per_min: int

    chaos_enabled: bool
    chaos_fail_percent: int
    chaos_latency_ms: int

    idempotency_ttl_seconds: int

    orders_integration_enabled: bool
    orders_exchange: str
    orders_queue: str
    orders_routing_keys: list[str]

    cors_origins: list[str]

    gateway_provider: str
    stripe_api_key: str
    gateway_max_retries: int
    gateway_retry_base_delay: float
    gateway_retry_max_delay: float

    saas_integration_enabled: bool
    saas_exchange: str
    saas_queue: str
    saas_routing_keys: list[str]

    webhook_delivery_enabled: bool
    reconciliation_interval_minutes: int
    report_refresh_interval_minutes: int


def load_settings() -> Settings:
    return Settings(
        app_env=_getenv("APP_ENV", "local"),
        app_name=_getenv("APP_NAME", "py-payments-ledger"),
        http_host=_getenv("HTTP_HOST", "0.0.0.0"),
        http_port=int(_getenv("HTTP_PORT", "8000")),
        database_url=_getenv("DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/app"),
        redis_url=_getenv("REDIS_URL", "redis://localhost:6379/0"),
        rabbitmq_url=_getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"),
        jwt_secret=_getenv("JWT_SECRET", "change-me"),
        jwt_issuer=_getenv("JWT_ISSUER", "local-auth"),
        token_expires_seconds=int(_getenv("TOKEN_EXPIRES_SECONDS", "3600")),
        rate_limit_write_per_min=int(_getenv("RATE_LIMIT_WRITE_PER_MIN", "60")),
        rate_limit_read_per_min=int(_getenv("RATE_LIMIT_READ_PER_MIN", "240")),
        chaos_enabled=_getenv("CHAOS_ENABLED", "false").lower() == "true",
        chaos_fail_percent=int(_getenv("CHAOS_FAIL_PERCENT", "0")),
        chaos_latency_ms=int(_getenv("CHAOS_LATENCY_MS", "0")),
        idempotency_ttl_seconds=int(_getenv("IDEMPOTENCY_TTL_SECONDS", "86400")),
        orders_integration_enabled=_getenv("ORDERS_INTEGRATION_ENABLED", "false").lower() == "true",
        orders_exchange=_getenv("ORDERS_EXCHANGE", "orders.x"),
        orders_queue=_getenv("ORDERS_QUEUE", "payments.orders.events"),
        orders_routing_keys=[
            k.strip()
            for k in _getenv(
                "ORDERS_ROUTING_KEYS", "payment.charge_requested,order.confirmed"
            ).split(",")
            if k.strip()
        ],
        cors_origins=[
            o.strip()
            for o in _getenv("CORS_ORIGINS", "").split(",")
            if o.strip()
        ],
        gateway_provider=_getenv("GATEWAY_PROVIDER", "fake"),
        stripe_api_key=_getenv("STRIPE_API_KEY", ""),
        gateway_max_retries=int(_getenv("GATEWAY_MAX_RETRIES", "3")),
        gateway_retry_base_delay=float(_getenv("GATEWAY_RETRY_BASE_DELAY", "1.0")),
        gateway_retry_max_delay=float(_getenv("GATEWAY_RETRY_MAX_DELAY", "30.0")),
        saas_integration_enabled=_getenv("SAAS_INTEGRATION_ENABLED", "false").lower() == "true",
        saas_exchange=_getenv("SAAS_EXCHANGE", "saas.x"),
        saas_queue=_getenv("SAAS_QUEUE", "payments.saas.events"),
        saas_routing_keys=[
            k.strip()
            for k in _getenv(
                "SAAS_ROUTING_KEYS", "tenant.created,tenant.updated,tenant.deleted"
            ).split(",")
            if k.strip()
        ],
        webhook_delivery_enabled=_getenv("WEBHOOK_DELIVERY_ENABLED", "false").lower() == "true",
        reconciliation_interval_minutes=int(_getenv("RECONCILIATION_INTERVAL_MINUTES", "60")),
        report_refresh_interval_minutes=int(_getenv("REPORT_REFRESH_INTERVAL_MINUTES", "15")),
    )
