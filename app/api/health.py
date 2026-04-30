from __future__ import annotations

from typing import Any

import httpx
import structlog
from fastapi import APIRouter

from app.config import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

_VERSION = "0.1.0"


@router.get("", summary="Liveness check")
async def health_check() -> dict[str, Any]:
    """Simple liveness endpoint."""
    settings = get_settings()
    return {
        "status": "ok",
        "env": settings.app_env,
        "version": _VERSION,
    }


@router.get("/detailed", summary="Detailed health with dependency checks")
async def health_detailed() -> dict[str, Any]:
    """Check each downstream dependency and report per-service health."""
    settings = get_settings()
    services: dict[str, Any] = {}

    # --- PostgreSQL ---
    try:
        from app.storage.db import _get_engine
        engine = _get_engine()
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy", fromlist=["text"]).text("SELECT 1"))
        services["postgres"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        services["postgres"] = {"status": "error", "detail": str(exc)}

    # --- Redis ---
    try:
        import redis.asyncio as aioredis
        redis_client = aioredis.from_url(settings.redis_url, socket_connect_timeout=3)
        await redis_client.ping()
        await redis_client.aclose()
        services["redis"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        services["redis"] = {"status": "error", "detail": str(exc)}

    # --- Ollama ---
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            resp.raise_for_status()
        services["ollama"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        services["ollama"] = {"status": "error", "detail": str(exc)}

    overall = "ok" if all(s["status"] == "ok" for s in services.values()) else "degraded"
    return {
        "status": overall,
        "env": settings.app_env,
        "version": _VERSION,
        "services": services,
    }
