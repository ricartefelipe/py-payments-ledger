from __future__ import annotations

import time
from dataclasses import dataclass

from redis import Redis


_LUA_TOKEN_BUCKET = r"""
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])

if tokens == nil then tokens = capacity end
if ts == nil then ts = now end

local delta = math.max(0, now - ts)
local refill = delta * refill_rate
tokens = math.min(capacity, tokens + refill)

local allowed = 0
if tokens >= requested then
  allowed = 1
  tokens = tokens - requested
end

redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
local ttl = math.ceil(capacity / refill_rate)
redis.call('EXPIRE', key, ttl)

return {allowed, tokens, ttl}
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int
    limit: int


class RedisRateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._sha = redis.script_load(_LUA_TOKEN_BUCKET)

    def consume(self, key: str, limit_per_minute: int) -> RateLimitResult:
        capacity = int(limit_per_minute)
        refill_rate = capacity / 60.0  # tokens/sec
        now = time.time()
        allowed, tokens, ttl = self._redis.evalsha(self._sha, 1, key, capacity, refill_rate, now, 1)
        remaining = int(float(tokens))
        retry_after = 0 if int(allowed) == 1 else 1
        return RateLimitResult(
            allowed=int(allowed) == 1,
            remaining=max(0, remaining),
            retry_after_seconds=retry_after,
            limit=capacity,
        )
