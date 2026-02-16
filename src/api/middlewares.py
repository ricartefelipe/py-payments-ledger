from __future__ import annotations

import json
import random
import time
from typing import Callable, Optional

import jwt
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.infrastructure.redis.client import get_redis
from src.infrastructure.redis.rate_limit import RedisRateLimiter
from src.shared.correlation import new_correlation_id, set_correlation_id, set_subject, set_tenant_id
from src.shared.logging import get_logger
from src.shared.metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL

log = get_logger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        cid = request.headers.get("X-Correlation-Id") or new_correlation_id()
        set_correlation_id(cid)
        tenant_id = request.headers.get("X-Tenant-Id") or ""
        set_tenant_id(tenant_id)

        start = time.time()
        try:
            response = await call_next(request)
        finally:
            elapsed = max(0.0, time.time() - start)
            path = request.url.path
            HTTP_REQUEST_DURATION_SECONDS.labels(request.method, path).observe(elapsed)
        response.headers["X-Correlation-Id"] = cid
        HTTP_REQUESTS_TOTAL.labels(request.method, request.url.path, str(response.status_code)).inc()
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path.startswith(("/healthz", "/readyz", "/metrics", "/docs", "/openapi.json")):
            return await call_next(request)

        settings = request.app.state.settings
        method = request.method.upper()
        group = "write" if method in ("POST", "PUT", "PATCH", "DELETE") else "read"
        limit = settings.rate_limit_write_per_min if group == "write" else settings.rate_limit_read_per_min

        tenant_id = request.headers.get("X-Tenant-Id", "public")
        user_sub = _try_decode_sub(request, settings.jwt_secret, settings.jwt_issuer) or "anonymous"
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
                        {"title": "Too Many Requests", "status": 429, "detail": "rate limit exceeded"}
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
        if request.url.path.startswith(("/healthz", "/readyz", "/metrics", "/docs", "/openapi.json")):
            return await call_next(request)

        settings = request.app.state.settings
        tenant_id = request.headers.get("X-Tenant-Id") or "public"
        chaos = _get_chaos_config(tenant_id, settings)
        if chaos["enabled"]:
            latency_ms = int(chaos.get("latency_ms", 0))
            fail_percent = int(chaos.get("fail_percent", 0))
            if latency_ms > 0:
                time.sleep(latency_ms / 1000.0)
            if fail_percent > 0 and random.randint(1, 100) <= fail_percent:
                return Response(
                    content=json.dumps({"title": "Service Unavailable", "status": 503, "detail": "chaos failure injected"}),
                    status_code=503,
                    media_type="application/json",
                )

        return await call_next(request)


def _try_decode_sub(request: Request, jwt_secret: str, issuer: str) -> Optional[str]:
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return None
    token = auth.removeprefix("Bearer ").strip()
    try:
        claims = jwt.decode(token, jwt_secret, algorithms=["HS256"], issuer=issuer, options={"verify_exp": False})
        sub = str(claims.get("sub") or "")
        if sub:
            set_subject(sub)
        return sub or None
    except Exception:
        return None


def _get_chaos_config(tenant_id: str, settings) -> dict:
    cfg = {"enabled": settings.chaos_enabled, "fail_percent": settings.chaos_fail_percent, "latency_ms": settings.chaos_latency_ms}
    try:
        r = get_redis()
        raw = r.get(f"chaos:{tenant_id}")
        if raw:
            data = json.loads(raw)
            cfg.update({k: data.get(k, cfg.get(k)) for k in ("enabled", "fail_percent", "latency_ms")})
    except Exception:
        pass
    return cfg
