from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import sentry_sdk

from src.application.exceptions import DomainError
from src.shared.config import load_settings
from src.shared.encryption import is_encryption_available
from src.shared.logging import configure_logging, get_logger
from src.shared.sentry_setup import init_sentry
from src.shared.tracing import setup_tracing
from src.api.middlewares import CorrelationIdMiddleware, RateLimitMiddleware, ChaosMiddleware
from src.api.routers import (
    admin,
    ai_docs,
    analytics,
    audit,
    auth,
    disputes,
    events,
    exchange_rates,
    gateway_configs,
    health,
    invoices,
    ledger,
    payment_links,
    payment_methods,
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
from src.api.routers.pagseguro_webhooks import router as pagseguro_webhooks_router
from src.api.routers.mercadopago_webhooks import router as mercadopago_webhooks_router

log = get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = app.state.settings
    from src.infrastructure.db.session import init_db, get_engine
    from src.infrastructure.redis.client import init_redis, get_redis

    init_db(settings)
    init_redis(settings)
    try:
        setup_tracing(app, engine=get_engine())
    except Exception:
        log.warning("tracing setup failed — continuing without tracing", exc_info=True)
    log.info("startup complete")
    yield
    try:
        get_engine().dispose()
    except Exception:
        pass
    try:
        get_redis().close()
    except Exception:
        pass
    log.info("shutdown complete")


def create_app() -> FastAPI:
    settings = load_settings()
    configure_logging("INFO")
    init_sentry(component="api")
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
        lifespan=_lifespan,
    )
    app.state.settings = settings

    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(ChaosMiddleware)
    app.add_middleware(RateLimitMiddleware)

    cors_origins = settings.cors_origins
    if not cors_origins and settings.app_env == "local":
        # Origens explícitas em dev (evita wildcard; ver config/env/portas-local.md no monorepo)
        cors_origins = [
            "http://127.0.0.1:4200",
            "http://localhost:4200",
            "http://127.0.0.1:4201",
            "http://localhost:4201",
            "http://127.0.0.1:4300",
            "http://localhost:4300",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    elif not cors_origins:
        cors_origins = []  # Production: deny all unless CORS_ORIGINS set
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None) or request.headers.get(
            "x-correlation-id", ""
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": "about:blank",
                "title": exc.message,
                "status": exc.status_code,
                "detail": exc.message,
                "instance": str(request.url.path),
                "correlation_id": correlation_id,
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None) or request.headers.get(
            "x-correlation-id", ""
        )
        detail = exc.detail
        if isinstance(detail, dict):
            detail.setdefault("type", "about:blank")
            detail.setdefault("instance", str(request.url.path))
            detail.setdefault("correlation_id", correlation_id)
            return JSONResponse(
                status_code=exc.status_code,
                content=detail,
                media_type="application/problem+json",
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": "about:blank",
                "title": "Error",
                "status": exc.status_code,
                "detail": str(detail),
                "instance": str(request.url.path),
                "correlation_id": correlation_id,
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None) or request.headers.get(
            "x-correlation-id", ""
        )
        return JSONResponse(
            status_code=422,
            content={
                "type": "about:blank",
                "title": "Validation Error",
                "status": 422,
                "detail": exc.errors(),
                "instance": str(request.url.path),
                "correlation_id": correlation_id,
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None) or request.headers.get(
            "x-correlation-id", ""
        )
        log.error("unhandled exception", exc_info=exc, extra={"correlation_id": correlation_id})
        sentry_sdk.capture_exception(exc)
        is_debug = getattr(settings, "app_env", "production") == "local"
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": str(exc) if is_debug else "An unexpected error occurred",
                "instance": str(request.url.path),
                "correlation_id": correlation_id,
            },
            media_type="application/problem+json",
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
    app.include_router(payment_methods.router)
    app.include_router(payouts.router)
    app.include_router(disputes.router)
    app.include_router(exchange_rates.router)
    app.include_router(splits.router)
    app.include_router(analytics.router)
    app.include_router(ai_docs.router)
    app.include_router(events.router)
    app.include_router(stripe_webhooks_router)
    app.include_router(pagseguro_webhooks_router)
    app.include_router(mercadopago_webhooks_router)

    return app


app = create_app()
