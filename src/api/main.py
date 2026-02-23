from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.shared.config import load_settings
from src.shared.logging import configure_logging, get_logger
from src.api.middlewares import CorrelationIdMiddleware, RateLimitMiddleware, ChaosMiddleware
from src.api.routers import admin, auth, health, ledger, payments, metrics

log = get_logger(__name__)


def create_app() -> FastAPI:
    settings = load_settings()
    configure_logging("INFO")

    app = FastAPI(
        title="py-payments-ledger",
        version="0.1.0",
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
    app.include_router(payments.router)
    app.include_router(ledger.router)
    app.include_router(admin.router)
    app.include_router(health.router)
    app.include_router(metrics.router)

    @app.on_event("startup")
    def _startup() -> None:
        from src.infrastructure.db.session import init_db
        from src.infrastructure.redis.client import init_redis

        init_db(settings)
        init_redis(settings)
        log.info("startup complete")

    return app


app = create_app()
