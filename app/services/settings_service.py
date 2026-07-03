from __future__ import annotations

import structlog
from sqlalchemy import select

logger = structlog.get_logger(__name__)

_SECRET_KEYS = frozenset({
    "podio_mcp_client_secret",
    "podio_rest_client_secret",
    "google_api_key",
    "groq_api_key",
    "mistral_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "ghl_api_key",
    "slack_bot_token",
    "slack_signing_secret",
    "github_token",
    "smtp_password",
})

ALL_SETTING_KEYS: list[str] = [
    "podio_mcp_client_id",
    "podio_mcp_client_secret",
    "podio_rest_client_id",
    "podio_rest_client_secret",
    "google_api_key",
    "groq_api_key",
    "mistral_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "ghl_api_key",
    "ghl_location_id",
    "slack_bot_token",
    "slack_signing_secret",
    "github_token",
    "github_org",
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "smtp_from",
]


def is_secret(key: str) -> bool:
    return key in _SECRET_KEYS


async def get_setting(key: str) -> str | None:
    """Return DB-stored value for key, or None if not set."""
    try:
        from app.storage.db import get_db
        from app.storage.models import IntegrationSetting
        async with get_db() as session:
            row = await session.scalar(
                select(IntegrationSetting).where(IntegrationSetting.key == key)
            )
            return row.value if row else None
    except Exception as exc:
        logger.warning("settings_service_get_failed", key=key, error=str(exc))
        return None


async def get_all_settings() -> dict[str, str | None]:
    """Return all known setting keys with their values from the DB (or None)."""
    try:
        from app.storage.db import get_db
        from app.storage.models import IntegrationSetting
        async with get_db() as session:
            rows = await session.scalars(select(IntegrationSetting))
            db_map = {r.key: r.value for r in rows}
    except Exception as exc:
        logger.warning("settings_service_get_all_failed", error=str(exc))
        db_map = {}

    return {k: db_map.get(k) for k in ALL_SETTING_KEYS}


async def upsert_setting(key: str, value: str | None, updated_by: str | None = None) -> None:
    """Insert or update a setting by key."""
    from app.storage.db import get_db
    from app.storage.models import IntegrationSetting
    from datetime import datetime, timezone

    async with get_db() as session:
        row = await session.scalar(
            select(IntegrationSetting).where(IntegrationSetting.key == key)
        )
        if row is None:
            row = IntegrationSetting(key=key, value=value, updated_by=updated_by,
                                     updated_at=datetime.now(timezone.utc))
            session.add(row)
        else:
            row.value = value
            row.updated_by = updated_by
            row.updated_at = datetime.now(timezone.utc)
