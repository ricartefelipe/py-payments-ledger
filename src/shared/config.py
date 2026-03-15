from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from dataclasses import dataclass
from functools import lru_cache

# Carrega .env e depois .env.local (override para rodar na máquina); alinhado a fluxe-b2b-suite/config/env
_project_root = Path(__file__).resolve().parent.parent.parent
load_dotenv(_project_root / ".env")
load_dotenv(_project_root / ".env.local")


def _getenv(name: str, default: str | None = None) -> str:
    val = os.getenv(name)
    if val is None:
        if default is None:
            raise RuntimeError(f"Missing env var: {name}")
        return default
    return val


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise ValueError(f"Required environment variable {name} is not set")
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
    jwt_secret_previous: str
    jwt_issuer: str
    jwt_algorithm: str
    jwt_public_key: str
    jwks_uri: str
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
    stripe_webhook_secret: str
    pagseguro_token: str
    pagseguro_api_url: str
    mercadopago_access_token: str
    mercadopago_api_url: str
    pagseguro_webhook_secret: str
    mercadopago_webhook_secret: str
    gateway_max_retries: int
    gateway_retry_base_delay: float
    gateway_retry_max_delay: float
    circuit_breaker_failure_threshold: int
    circuit_breaker_recovery_timeout: float

    saas_integration_enabled: bool
    saas_exchange: str
    saas_queue: str
    saas_routing_keys: list[str]

    webhook_delivery_enabled: bool
    reconciliation_interval_minutes: int
    reconciliation_enabled: bool
    report_refresh_interval_minutes: int
    audit_retention_days: int

    charge_request_max_retries: int

    encryption_key: str


def load_settings() -> Settings:
    settings = Settings(
        app_env=_getenv("APP_ENV", "local"),
        app_name=_getenv("APP_NAME", "py-payments-ledger"),
        http_host=_getenv("HTTP_HOST", "0.0.0.0"),
        http_port=int(_getenv("HTTP_PORT", "8000")),
        database_url=_getenv("DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/app"),
        redis_url=_getenv("REDIS_URL", "redis://localhost:6379/0"),
        rabbitmq_url=_getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"),
        jwt_secret=_getenv("JWT_SECRET", ""),
        jwt_secret_previous=_getenv("JWT_SECRET_PREVIOUS", ""),
        jwt_issuer=_getenv("JWT_ISSUER", "local-auth"),
        jwt_algorithm=_getenv("JWT_ALGORITHM", "HS256"),
        jwt_public_key=_getenv("JWT_PUBLIC_KEY", ""),
        jwks_uri=_getenv("JWKS_URI", ""),
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
        cors_origins=[o.strip() for o in _getenv("CORS_ORIGINS", "").split(",") if o.strip()],
        gateway_provider=_getenv("GATEWAY_PROVIDER", "fake"),
        stripe_api_key=_getenv("STRIPE_API_KEY", ""),
        stripe_webhook_secret=_getenv("STRIPE_WEBHOOK_SECRET", ""),
        pagseguro_token=_getenv("PAGSEGURO_TOKEN", ""),
        pagseguro_api_url=_getenv("PAGSEGURO_API_URL", "https://api.pagseguro.com"),
        mercadopago_access_token=_getenv("MERCADOPAGO_ACCESS_TOKEN", ""),
        mercadopago_api_url=_getenv("MERCADOPAGO_API_URL", "https://api.mercadopago.com"),
        pagseguro_webhook_secret=_getenv("PAGSEGURO_WEBHOOK_SECRET", ""),
        mercadopago_webhook_secret=_getenv("MERCADOPAGO_WEBHOOK_SECRET", ""),
        gateway_max_retries=int(_getenv("GATEWAY_MAX_RETRIES", "3")),
        gateway_retry_base_delay=float(_getenv("GATEWAY_RETRY_BASE_DELAY", "1.0")),
        gateway_retry_max_delay=float(_getenv("GATEWAY_RETRY_MAX_DELAY", "30.0")),
        circuit_breaker_failure_threshold=int(_getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5")),
        circuit_breaker_recovery_timeout=float(_getenv("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "30")),
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
        reconciliation_enabled=_getenv("RECONCILIATION_ENABLED", "false").lower() == "true",
        report_refresh_interval_minutes=int(_getenv("REPORT_REFRESH_INTERVAL_MINUTES", "15")),
        audit_retention_days=int(_getenv("AUDIT_RETENTION_DAYS", "90")),
        charge_request_max_retries=int(_getenv("CHARGE_REQUEST_MAX_RETRIES", "3")),
        encryption_key=_getenv("ENCRYPTION_KEY", ""),
    )

    has_rs256 = settings.jwks_uri or (
        settings.jwt_algorithm.upper() == "RS256" and settings.jwt_public_key
    )
    has_hs256 = settings.jwt_secret
    if not has_rs256 and not has_hs256:
        raise ValueError("Either JWKS_URI/JWT_PUBLIC_KEY (RS256) or JWT_SECRET (HS256) must be set")

    if settings.gateway_provider == "stripe" and not settings.stripe_api_key:
        raise ValueError("STRIPE_API_KEY must be set when GATEWAY_PROVIDER is 'stripe'")
    if settings.gateway_provider == "pagseguro" and not settings.pagseguro_token:
        raise ValueError("PAGSEGURO_TOKEN must be set when GATEWAY_PROVIDER is 'pagseguro'")
    if settings.gateway_provider == "mercadopago" and not settings.mercadopago_access_token:
        raise ValueError(
            "MERCADOPAGO_ACCESS_TOKEN must be set when GATEWAY_PROVIDER is 'mercadopago'"
        )

    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
