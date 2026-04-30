from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

_AUDIT_REDIS_KEY = "audit:log"
_AUDIT_MAX_ENTRIES = 1000


class AuditEntry(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    team_name: str
    tool_name: str
    input_summary: str = ""
    success: bool = True
    latency_ms: float = 0.0
    error_msg: str | None = None


class AuditService:
    """Logs audit entries to structlog and stores them in Redis."""

    def __init__(self) -> None:
        self._redis: Any = None  # Set at startup via set_redis()

    def set_redis(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def log(self, entry: AuditEntry) -> None:
        """Persist an audit entry to structlog and Redis."""
        log = logger.bind(
            audit=True,
            entry_id=str(entry.id),
            team=entry.team_name,
            tool=entry.tool_name,
            success=entry.success,
            latency_ms=entry.latency_ms,
        )
        if entry.success:
            log.info("tool_invoked", input_summary=entry.input_summary[:200])
        else:
            log.warning("tool_failed", error=entry.error_msg, input_summary=entry.input_summary[:200])

        if self._redis is not None:
            try:
                serialized = entry.model_dump_json()
                await self._redis.lpush(_AUDIT_REDIS_KEY, serialized)
                await self._redis.ltrim(_AUDIT_REDIS_KEY, 0, _AUDIT_MAX_ENTRIES - 1)
            except Exception as exc:  # noqa: BLE001
                logger.error("audit_redis_write_failed", error=str(exc))

    async def recent(self, limit: int = 50) -> list[AuditEntry]:
        """Return up to `limit` most recent audit entries."""
        if self._redis is None:
            logger.warning("audit_redis_not_available")
            return []
        try:
            raw_entries = await self._redis.lrange(_AUDIT_REDIS_KEY, 0, limit - 1)
            entries: list[AuditEntry] = []
            for raw in raw_entries:
                try:
                    data = json.loads(raw)
                    entries.append(AuditEntry(**data))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("audit_entry_parse_failed", error=str(exc))
            return entries
        except Exception as exc:  # noqa: BLE001
            logger.error("audit_redis_read_failed", error=str(exc))
            return []


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
audit_service = AuditService()
