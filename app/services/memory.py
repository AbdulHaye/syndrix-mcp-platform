from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog

logger = structlog.get_logger(__name__)

_MEMORY_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


class MemoryService:
    """
    Manages conversational memory:
      - Short-term: stored in Redis (per-team, recent history)
      - Long-term: embedded and stored in pgvector for semantic retrieval
    """

    def __init__(self) -> None:
        self._redis: Any = None  # Injected at startup

    def set_redis(self, redis_client: Any) -> None:
        self._redis = redis_client

    def _team_key(self, team: str) -> str:
        return f"memory:team:{team}"

    async def store_conversation(
        self,
        team: str,
        summary: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Store a conversation summary.

        1. Generates an embedding for the summary via model_gateway.
        2. Stores the document + embedding in pgvector via vector_store.
        3. Appends a lightweight record to the Redis team list for recency queries.
        """
        from app.services.model_gateway import model_gateway
        from app.storage.vector import vector_store

        entry_id = str(uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        source = f"conversation/{team}/{entry_id}"

        # 1. Embed
        try:
            embedding = await model_gateway.embed(summary)
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_embed_failed", error=str(exc))
            embedding = []

        # 2. Store in vector DB (only if we have an embedding)
        if embedding:
            try:
                await vector_store.store(
                    title=f"Conversation summary — {team} — {ts[:10]}",
                    source=source,
                    content=summary,
                    embedding=embedding,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("memory_vector_store_failed", error=str(exc))

        # 3. Store lightweight record in Redis
        record: dict[str, Any] = {
            "id": entry_id,
            "team": team,
            "summary": summary[:500],
            "timestamp": ts,
            "metadata": metadata,
        }
        if self._redis is not None:
            try:
                key = self._team_key(team)
                await self._redis.lpush(key, json.dumps(record))
                await self._redis.ltrim(key, 0, 99)  # keep last 100
                await self._redis.expire(key, _MEMORY_TTL_SECONDS)
            except Exception as exc:  # noqa: BLE001
                logger.warning("memory_redis_write_failed", error=str(exc))

        logger.info("memory_stored", team=team, entry_id=entry_id)
        return record

    async def retrieve_similar(
        self,
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Find conversation summaries semantically similar to the query.

        Embeds the query text, then runs a pgvector cosine-distance search.
        """
        from app.services.model_gateway import model_gateway
        from app.storage.vector import vector_store

        try:
            query_embedding = await model_gateway.embed(query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_embed_query_failed", error=str(exc))
            return []

        if not query_embedding:
            return []

        try:
            results = await vector_store.search(query_embedding, limit=limit)
            return results
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_vector_search_failed", error=str(exc))
            return []

    async def get_recent(self, team: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Return the most recent conversation entries for a team from Redis.
        """
        if self._redis is None:
            logger.warning("memory_redis_not_available")
            return []

        key = self._team_key(team)
        try:
            raw_entries = await self._redis.lrange(key, 0, limit - 1)
            entries: list[dict[str, Any]] = []
            for raw in raw_entries:
                try:
                    entries.append(json.loads(raw))
                except json.JSONDecodeError as exc:
                    logger.warning("memory_entry_parse_failed", error=str(exc))
            return entries
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_redis_read_failed", error=str(exc))
            return []


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
memory_service = MemoryService()
