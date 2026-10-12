from functools import lru_cache
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    from redis import Redis


@lru_cache
def get_redis_client() -> "Redis":
    from redis import Redis

    return Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.redis_connect_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
        decode_responses=True,
    )
