"""Atomic, distributed fixed-window rate limits backed by Redis."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, Sequence

from app.core.config import settings
from app.core.redis import get_redis_client


class RedisRateLimitBackend(Protocol):
    def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object: ...


class RateLimitBackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class RateLimitScope:
    namespace: str
    identity: str
    limit: int


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


# All scopes are incremented and checked in one Redis transaction. A rejected
# request still counts in every applicable bucket, preventing repeated denied
# requests from probing a less restrictive tenant bucket for free.
_CHECK_SCOPES_LUA = """
local ttl = tonumber(ARGV[1])
local allowed = 1
local retry_ms = 0

for i, key in ipairs(KEYS) do
  local count = redis.call('INCR', key)
  if count == 1 then
    redis.call('PEXPIRE', key, ttl)
  end
  local remaining = redis.call('PTTL', key)
  local limit = tonumber(ARGV[i + 1])
  if count > limit then
    allowed = 0
    if remaining > retry_ms then retry_ms = remaining end
  end
end

return {allowed, retry_ms}
"""


class RateLimitService:
    def __init__(self, backend: RedisRateLimitBackend, window_seconds: int = 60):
        self._backend = backend
        self._window_seconds = window_seconds

    @staticmethod
    def _key(scope: RateLimitScope) -> str:
        identity_hash = hashlib.sha256(scope.identity.encode("utf-8")).hexdigest()[:32]
        return f"rate-limit:{scope.namespace}:{identity_hash}"

    def check(self, scopes: Sequence[RateLimitScope]) -> RateLimitDecision:
        active = [scope for scope in scopes if scope.limit > 0]
        if not active:
            return RateLimitDecision(allowed=True)

        keys = [self._key(scope) for scope in active]
        limits = [scope.limit for scope in active]
        try:
            raw = self._backend.eval(
                _CHECK_SCOPES_LUA,
                len(keys),
                *keys,
                self._window_seconds * 1000,
                *limits,
            )
            allowed, retry_ms = int(raw[0]), int(raw[1])  # type: ignore[index]
        except Exception as exc:
            raise RateLimitBackendError("Rate-limit store is unavailable.") from exc

        return RateLimitDecision(
            allowed=allowed == 1,
            retry_after_seconds=max(1, math.ceil(retry_ms / 1000)) if allowed == 0 else 0,
        )


@lru_cache
def get_rate_limit_service() -> RateLimitService:
    return RateLimitService(get_redis_client(), settings.rate_limit_window_seconds)
