from __future__ import annotations

import os
import logging

log = logging.getLogger(__name__)

_TRACING_INITIALIZED = False


def setup_tracing(app, engine=None) -> None:
    global _TRACING_INITIALIZED
    if _TRACING_INITIALIZED:
        return

    explicit = os.getenv("OTEL_ENABLED")
    if explicit is not None:
        otel_enabled = explicit.lower() != "false"
    elif os.getenv("APP_ENV", "").lower() == "staging":
        # Staging no Railway costuma não ter collector OTLP — evita ruído UNAVAILABLE nos logs
        otel_enabled = False
    else:
        otel_enabled = True
    if not otel_enabled:
        log.info("OpenTelemetry disabled (staging default or OTEL_ENABLED=false)")
        _TRACING_INITIALIZED = True
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.semconv.resource import ResourceAttributes
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    except ImportError:
        log.warning("OpenTelemetry packages not installed — tracing disabled")
        _TRACING_INITIALIZED = True
        return

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    resource = Resource.create(
        {
            ResourceAttributes.SERVICE_NAME: "py-payments-ledger",
            ResourceAttributes.SERVICE_VERSION: os.getenv("APP_VERSION", "1.0.0"),
        }
    )

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()

    if engine is not None:
        try:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

            SQLAlchemyInstrumentor().instrument(engine=engine)
        except Exception:
            log.warning("SQLAlchemy instrumentation failed — skipping", exc_info=True)

    _TRACING_INITIALIZED = True
    log.info("OpenTelemetry started — exporting to %s", endpoint)
