from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.application.ports.payment_gateway import PaymentGatewayPort
from src.infrastructure.gateway.fake import FakeGatewayAdapter
from src.shared.config import Settings
from src.shared.logging import get_logger

log = get_logger(__name__)


def _create_stripe_adapter(
    settings: Settings,
    api_key: Optional[str] = None,
) -> PaymentGatewayPort:
    from src.infrastructure.gateway.stripe_adapter import StripeAdapter

    key = api_key or getattr(settings, "stripe_api_key", "")
    if not key:
        log.warning("stripe_api_key not set, falling back to fake gateway")
        return FakeGatewayAdapter()
    return StripeAdapter(
        api_key=key,
        max_retries=getattr(settings, "gateway_max_retries", 3),
        base_delay=getattr(settings, "gateway_retry_base_delay", 1.0),
        max_delay=getattr(settings, "gateway_retry_max_delay", 30.0),
        circuit_failure_threshold=getattr(settings, "circuit_breaker_failure_threshold", 5),
        circuit_recovery_timeout=getattr(settings, "circuit_breaker_recovery_timeout", 30.0),
    )


def _create_pagseguro_adapter(
    settings: Settings,
    api_key: Optional[str] = None,
) -> PaymentGatewayPort:
    from src.infrastructure.gateway.pagseguro_adapter import PagSeguroAdapter

    token = api_key or getattr(settings, "pagseguro_token", "")
    if not token:
        log.warning("pagseguro_token not set, falling back to fake gateway")
        return FakeGatewayAdapter()
    return PagSeguroAdapter(
        token=token,
        api_url=getattr(settings, "pagseguro_api_url", "https://api.pagseguro.com"),
        max_retries=getattr(settings, "gateway_max_retries", 3),
        base_delay=getattr(settings, "gateway_retry_base_delay", 1.0),
        max_delay=getattr(settings, "gateway_retry_max_delay", 30.0),
        circuit_failure_threshold=getattr(settings, "circuit_breaker_failure_threshold", 5),
        circuit_recovery_timeout=getattr(settings, "circuit_breaker_recovery_timeout", 30.0),
    )


def _create_mercadopago_adapter(
    settings: Settings,
    api_key: Optional[str] = None,
) -> PaymentGatewayPort:
    from src.infrastructure.gateway.mercadopago_adapter import MercadoPagoAdapter

    token = api_key or getattr(settings, "mercadopago_access_token", "")
    if not token:
        log.warning("mercadopago_access_token not set, falling back to fake gateway")
        return FakeGatewayAdapter()
    return MercadoPagoAdapter(
        access_token=token,
        api_url=getattr(settings, "mercadopago_api_url", "https://api.mercadopago.com"),
        max_retries=getattr(settings, "gateway_max_retries", 3),
        base_delay=getattr(settings, "gateway_retry_base_delay", 1.0),
        max_delay=getattr(settings, "gateway_retry_max_delay", 30.0),
        circuit_failure_threshold=getattr(settings, "circuit_breaker_failure_threshold", 5),
        circuit_recovery_timeout=getattr(settings, "circuit_breaker_recovery_timeout", 30.0),
    )


def create_gateway(settings: Settings) -> PaymentGatewayPort:
    """Factory that returns the appropriate gateway adapter based on settings."""
    gateway_provider = getattr(settings, "gateway_provider", "fake")

    if gateway_provider == "stripe":
        return _create_stripe_adapter(settings)
    if gateway_provider == "pagseguro":
        return _create_pagseguro_adapter(settings)
    if gateway_provider == "mercadopago":
        return _create_mercadopago_adapter(settings)

    log.info("using fake gateway adapter")
    return FakeGatewayAdapter()


def create_gateway_by_provider(
    settings: Settings,
    provider: str,
    api_key: Optional[str] = None,
) -> PaymentGatewayPort:
    """Create a gateway for a specific provider. Used for multi-gateway per tenant."""
    if provider == "stripe":
        key = api_key or getattr(settings, "stripe_api_key", "")
        return _create_stripe_adapter(settings, api_key=key)
    if provider == "pagseguro":
        token = api_key or getattr(settings, "pagseguro_token", "")
        return _create_pagseguro_adapter(settings, api_key=token)
    if provider == "mercadopago":
        token = api_key or getattr(settings, "mercadopago_access_token", "")
        return _create_mercadopago_adapter(settings, api_key=token)
    if provider == "fake":
        return FakeGatewayAdapter()
    log.warning("unknown provider %s, falling back to fake", provider)
    return FakeGatewayAdapter()


def get_gateway_for_tenant(
    session: Session,
    tenant_id: str,
    settings: Settings,
    provider: Optional[str] = None,
    currency: Optional[str] = None,
    payment_type: Optional[str] = None,
) -> PaymentGatewayPort:
    """
    Select and create gateway for a tenant. Supports multi-gateway config per tenant.
    - If provider is specified, use that config (or create from settings if no config).
    - Else use default gateway from tenant config.
    - If no tenant config exists, use global settings (STRIPE_API_KEY, gateway_provider).
    """
    from src.infrastructure.db.models import GatewayConfig

    q = select(GatewayConfig).where(GatewayConfig.tenant_id == tenant_id)
    if provider:
        q = q.where(GatewayConfig.provider == provider)
    else:
        q = q.where(GatewayConfig.is_default.is_(True))

    config = session.execute(q).scalar_one_or_none()

    if config:
        if currency and config.supported_currencies and currency not in config.supported_currencies:
            alts = [
                c
                for c in session.execute(
                    select(GatewayConfig).where(GatewayConfig.tenant_id == tenant_id)
                )
                .scalars()
                .all()
                if not c.supported_currencies or currency in c.supported_currencies
            ]
            if alts:
                config = alts[0]
        if payment_type and config.payment_types and payment_type not in config.payment_types:
            alts = [
                c
                for c in session.execute(
                    select(GatewayConfig).where(GatewayConfig.tenant_id == tenant_id)
                )
                .scalars()
                .all()
                if not c.payment_types or payment_type in c.payment_types
            ]
            if alts:
                config = alts[0]

        api_key = None
        if config.api_key_ref:
            api_key = os.getenv(config.api_key_ref) or getattr(settings, "stripe_api_key", "")

        return create_gateway_by_provider(settings, config.provider, api_key=api_key)

    if provider:
        return create_gateway_by_provider(settings, provider)

    return create_gateway(settings)
