from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.shared.config import load_settings
from src.shared.encryption import is_encryption_available
from src.shared.logging import configure_logging, get_logger
from src.api.middlewares import CorrelationIdMiddleware, RateLimitMiddleware, ChaosMiddleware
from src.api.routers import (
    admin,
    ai_docs,
    analytics,
    audit,
    auth,
    disputes,
    exchange_rates,
    gateway_configs,
    health,
    invoices,
    ledger,
    payment_links,
    payments,
    payouts,
    metrics,
    recurring,
    refunds,
    splits,
    webhooks,
    accounts,
    reconciliation,
    reports,
)
from src.api.routers.stripe_webhooks import router as stripe_webhooks_router

log = get_logger(__name__)


def create_app() -> FastAPI:
    settings = load_settings()
    configure_logging("INFO")
    if not is_encryption_available(settings.encryption_key):
        log.warning(
            "ENCRYPTION_KEY not set - sensitive payment data will be stored in plaintext (dev only)"
        )

    app = FastAPI(
        title="py-payments-ledger",
        version="1.0.0",
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = settings

    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(ChaosMiddleware)
    app.add_middleware(RateLimitMiddleware)

    cors_origins = settings.cors_origins
    if not cors_origins and settings.app_env == "local":
        cors_origins = ["*"]
    elif not cors_origins:
        cors_origins = []  # Production: deny all unless CORS_ORIGINS set
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(audit.router)
    app.include_router(payments.router)
    app.include_router(invoices.router)
    app.include_router(ledger.router)
    app.include_router(admin.router)
    app.include_router(gateway_configs.router)
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(recurring.router)
    app.include_router(refunds.router)
    app.include_router(webhooks.router)
    app.include_router(accounts.router)
    app.include_router(reconciliation.router)
    app.include_router(reports.router)
    app.include_router(payment_links.router)
    app.include_router(payouts.router)
    app.include_router(disputes.router)
    app.include_router(exchange_rates.router)
    app.include_router(splits.router)
    app.include_router(analytics.router)
    app.include_router(ai_docs.router)
    app.include_router(stripe_webhooks_router)

    @app.on_event("startup")
    def _startup() -> None:
        from src.infrastructure.db.session import init_db
        from src.infrastructure.redis.client import init_redis

        init_db(settings)
        init_redis(settings)
        log.info("startup complete")

    @app.on_event("shutdown")
    def _shutdown() -> None:
        from src.infrastructure.db.session import get_engine
        from src.infrastructure.redis.client import get_redis

        try:
            get_engine().dispose()
        except Exception:
            pass
        try:
            get_redis().close()
        except Exception:
            pass
        log.info("shutdown complete")

    return app


app = create_app()
