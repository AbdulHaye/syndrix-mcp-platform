from __future__ import annotations

import json
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_NS = "syndrix"


class CacheService:
    """
    Redis-backed cache with key namespacing and JSON helpers.
    Degrades gracefully when Redis is unavailable.
    """

    def __init__(self) -> None:
        self._redis: Any = None

    def set_redis(self, redis_client: Any) -> None:
        self._redis = redis_client

    def _key(self, namespace: str, key: str) -> str:
        return f"{_NS}:{namespace}:{key}"

    # ── Raw get/set/delete ───────────────────────────────────────────────────

    async def get(self, namespace: str, key: str) -> str | None:
        if not self._redis:
            return None
        try:
            return await self._redis.get(self._key(namespace, key))
        except Exception as exc:
            logger.warning("cache_get_failed", key=key, error=str(exc))
            return None

    async def set(self, namespace: str, key: str, value: str, ttl: int = 300) -> None:
        if not self._redis:
            return
        try:
            await self._redis.setex(self._key(namespace, key), ttl, value)
        except Exception as exc:
            logger.warning("cache_set_failed", key=key, error=str(exc))

    async def delete(self, namespace: str, key: str) -> None:
        if not self._redis:
            return
        try:
            await self._redis.delete(self._key(namespace, key))
        except Exception as exc:
            logger.warning("cache_delete_failed", key=key, error=str(exc))

    async def invalidate_prefix(self, namespace: str, prefix: str) -> int:
        """Delete all keys matching syndrix:{namespace}:{prefix}*"""
        if not self._redis:
            return 0
        try:
            pattern = f"{_NS}:{namespace}:{prefix}*"
            keys = await self._redis.keys(pattern)
            if keys:
                await self._redis.delete(*keys)
            return len(keys)
        except Exception as exc:
            logger.warning("cache_invalidate_failed", prefix=prefix, error=str(exc))
            return 0

    # ── JSON helpers ─────────────────────────────────────────────────────────

    async def get_json(self, namespace: str, key: str) -> Any | None:
        raw = await self.get(namespace, key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set_json(self, namespace: str, key: str, value: Any, ttl: int = 300) -> None:
        await self.set(namespace, key, json.dumps(value, default=str), ttl)

    # ── Convenience TTL constants ─────────────────────────────────────────────

    TTL_SHORT  = 60      # health checks, live data
    TTL_MED    = 300     # tool results, prompt outputs
    TTL_LONG   = 3_600   # embeddings, rarely-changed data
    TTL_DAY    = 86_400  # daily reports


cache_service = CacheService()
