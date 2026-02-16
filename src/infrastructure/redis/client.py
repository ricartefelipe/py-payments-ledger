from __future__ import annotations

from redis import Redis

from src.shared.config import Settings

_client: Redis | None = None


def init_redis(settings: Settings) -> None:
    global _client
    _client = Redis.from_url(settings.redis_url, decode_responses=True)


def get_redis() -> Redis:
    if _client is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return _client
