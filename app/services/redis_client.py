"""Shared Redis client for ephemeral voice capture storage."""
from __future__ import annotations

import redis

from app.config import get_settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        settings = get_settings()
        _client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=False,
        )
    return _client
