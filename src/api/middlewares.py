from __future__ import annotations

import json
import random
import time
from typing import Callable, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.application.security import try_decode_sub
from src.infrastructure.redis.client import get_redis
from src.infrastructure.redis.rate_limit import RedisRateLimiter
from src.shared.correlation import (
    new_correlation_id,
    set_correlation_id,
    set_subject,
    set_tenant_id,
)
from src.shared.logging import get_logger
from src.shared.metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL

log = get_logger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        cid = request.headers.get("X-Correlation-Id") or new_correlation_id()
        set_correlation_id(cid)
        tenant_id = request.headers.get("X-Tenant-Id") or ""
        set_tenant_id(tenant_id)

        _enrich_active_span(cid, tenant_id)

        start = time.time()
        try:
            response = await call_next(request)
        finally:
            elapsed = max(0.0, time.time() - start)
            path = request.url.path
            HTTP_REQUEST_DURATION_SECONDS.labels(request.method, path).observe(elapsed)
        response.headers["X-Correlation-Id"] = cid
        _set_trace_header(response)
        HTTP_REQUESTS_TOTAL.labels(
            request.method, request.url.path, str(response.status_code)
        ).inc()
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path.startswith(
            ("/healthz", "/readyz", "/metrics", "/docs", "/openapi.json")
        ):
            return await call_next(request)

        settings = request.app.state.settings
        method = request.method.upper()
        group = "write" if method in ("POST", "PUT", "PATCH", "DELETE") else "read"
        limit = (
            settings.rate_limit_write_per_min
            if group == "write"
            else settings.rate_limit_read_per_min
        )

        tenant_id = request.headers.get("X-Tenant-Id", "public")
        token = _extract_bearer_token(request)
        user_sub = (try_decode_sub(settings, token) if token else None) or "anonymous"
        if user_sub != "anonymous":
            set_subject(user_sub)
        key = f"ratelimit:{tenant_id}:{user_sub}:{group}"

        try:
            rl = RedisRateLimiter(get_redis())
            res = rl.consume(key, limit)
            if not res.allowed:
                headers = {
                    "X-RateLimit-Limit": str(res.limit),
                    "X-RateLimit-Remaining": str(res.remaining),
                    "Retry-After": str(res.retry_after_seconds),
                }
                return Response(
                    content=json.dumps(
                        {
                            "title": "Too Many Requests",
                            "status": 429,
                            "detail": "rate limit exceeded",
                        }
                    ),
                    status_code=429,
                    media_type="application/json",
                    headers=headers,
                )
        except Exception:
            log.exception("rate limit failure; allowing request")
        return await call_next(request)


class ChaosMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path.startswith(
            ("/healthz", "/readyz", "/metrics", "/docs", "/openapi.json")
        ):
            return await call_next(request)

        settings = request.app.state.settings
        if settings.app_env != "local":
            return await call_next(request)
        tenant_id = request.headers.get("X-Tenant-Id") or "public"
        chaos = _get_chaos_config(tenant_id, settings)
        if chaos["enabled"]:
            latency_ms = int(chaos.get("latency_ms", 0))
            fail_percent = int(chaos.get("fail_percent", 0))
            if latency_ms > 0:
                time.sleep(latency_ms / 1000.0)
            if fail_percent > 0 and random.randint(1, 100) <= fail_percent:
                return Response(
                    content=json.dumps(
                        {
                            "title": "Service Unavailable",
                            "status": 503,
                            "detail": "chaos failure injected",
                        }
                    ),
                    status_code=503,
                    media_type="application/json",
                )

        return await call_next(request)


def _extract_bearer_token(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return None
    return auth.removeprefix("Bearer ").strip() or None


def _enrich_active_span(correlation_id: str, tenant_id: str) -> None:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.is_recording():
            span.set_attribute("correlation.id", correlation_id)
            if tenant_id:
                span.set_attribute("tenant.id", tenant_id)
    except Exception:
        pass


def _set_trace_header(response: Response) -> None:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            response.headers["X-Trace-Id"] = format(ctx.trace_id, "032x")
    except Exception:
        pass


def _get_chaos_config(tenant_id: str, settings) -> dict:
    cfg = {
        "enabled": settings.chaos_enabled,
        "fail_percent": settings.chaos_fail_percent,
        "latency_ms": settings.chaos_latency_ms,
    }
    try:
        r = get_redis()
        raw = r.get(f"chaos:{tenant_id}")
        if raw:
            data = json.loads(raw)
            cfg.update(
                {k: data.get(k, cfg.get(k)) for k in ("enabled", "fail_percent", "latency_ms")}
            )
    except Exception:
        pass
    return cfg
