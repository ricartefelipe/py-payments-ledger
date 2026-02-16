from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from redis import Redis


@dataclass(frozen=True)
class IdempotencyHit:
    hit: bool
    value: Optional[dict[str, Any]]


class IdempotencyStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 24 * 3600) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    def get(self, key: str) -> IdempotencyHit:
        raw = self._redis.get(key)
        if not raw:
            return IdempotencyHit(hit=False, value=None)
        try:
            return IdempotencyHit(hit=True, value=json.loads(raw))
        except Exception:
            return IdempotencyHit(hit=True, value=None)

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._redis.setex(key, self._ttl, json.dumps(value, ensure_ascii=False))
