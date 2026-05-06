from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.bearer import TeamIdentity, require_auth
from app.services.settings_service import (
    ALL_SETTING_KEYS,
    get_all_settings,
    is_secret,
    upsert_setting,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


class EncryptedEnvelope(BaseModel):
    """Single-field wrapper — the entire payload is one AES-256-GCM ciphertext."""
    data: str


@router.get("", summary="Get integration settings (fully encrypted response)")
async def get_settings_endpoint(
    identity: TeamIdentity = Depends(require_auth),
) -> EncryptedEnvelope:
    from app.services.crypto import encrypt_value

    raw = await get_all_settings()
    payload = [
        {
            "key": k,
            "value": raw.get(k) or "",
            "is_secret": is_secret(k),
            "is_set": bool(raw.get(k)),
        }
        for k in ALL_SETTING_KEYS
    ]
    ciphertext = encrypt_value(json.dumps({"settings": payload}), identity.token)
    return EncryptedEnvelope(data=ciphertext)


@router.put("", summary="Save integration settings (fully encrypted request + response)")
async def save_settings_endpoint(
    body: EncryptedEnvelope,
    identity: TeamIdentity = Depends(require_auth),
) -> EncryptedEnvelope:
    from app.services.crypto import decrypt_value, encrypt_value

    plaintext = decrypt_value(body.data, identity.token)
    payload: dict[str, Any] = json.loads(plaintext)
    settings: dict[str, str | None] = payload.get("settings", {})

    saved: list[str] = []
    for key, value in settings.items():
        if key not in ALL_SETTING_KEYS:
            continue
        await upsert_setting(key, value or None, updated_by=identity.team_name)
        saved.append(key)

    _reset_adapters()
    logger.info("settings_saved", keys=saved, team=identity.team_name)

    result = encrypt_value(json.dumps({"success": True, "saved": saved}), identity.token)
    return EncryptedEnvelope(data=result)


def _reset_adapters() -> None:
    import importlib
    for module, attr in [
        ("app.adapters.github", "github_adapter"),
        ("app.adapters.slack",  "slack_adapter"),
        ("app.adapters.ghl",    "ghl_adapter"),
        ("app.adapters.podio",  "podio_adapter"),
        ("app.adapters.email",  "email_adapter"),
    ]:
        try:
            getattr(importlib.import_module(module), attr)._connected = False
        except Exception:
            pass
