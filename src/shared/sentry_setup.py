"""Inicialização opcional do Sentry (SENTRY_DSN vazio = sem envio)."""

from __future__ import annotations

import os


def init_sentry(*, component: str) -> None:
    dsn = (os.getenv("SENTRY_DSN") or "").strip()
    if not dsn:
        return
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    environment = os.getenv("SENTRY_ENVIRONMENT") or os.getenv("APP_ENV") or "local"
    traces = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE") or "0")

    integrations: list = []
    if component == "api":
        integrations = [
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
        ]

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=min(1.0, max(0.0, traces)),
        integrations=integrations,
        send_default_pii=False,
    )
