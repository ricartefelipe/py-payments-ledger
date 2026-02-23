from __future__ import annotations

from src.infrastructure.gateway.fake import FakeGatewayAdapter
from src.shared.config import Settings
from src.shared.logging import get_logger

log = get_logger(__name__)


def create_gateway(settings: Settings) -> FakeGatewayAdapter:
    """Factory that returns the appropriate gateway adapter based on settings."""
    gateway_provider = getattr(settings, "gateway_provider", "fake")

    if gateway_provider == "stripe":
        from src.infrastructure.gateway.stripe_adapter import StripeAdapter
        api_key = getattr(settings, "stripe_api_key", "")
        if not api_key:
            log.warning("stripe_api_key not set, falling back to fake gateway")
            return FakeGatewayAdapter()
        return StripeAdapter(
            api_key=api_key,
            max_retries=getattr(settings, "gateway_max_retries", 3),
            base_delay=getattr(settings, "gateway_retry_base_delay", 1.0),
            max_delay=getattr(settings, "gateway_retry_max_delay", 30.0),
        )

    log.info("using fake gateway adapter")
    return FakeGatewayAdapter()
