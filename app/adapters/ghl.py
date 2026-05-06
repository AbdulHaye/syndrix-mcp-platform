from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.adapters.base import BaseAdapter

logger = structlog.get_logger(__name__)

_GHL_BASE_URL = "https://rest.gohighlevel.com/v1"


class GoHighLevelAdapter(BaseAdapter):
    """GoHighLevel CRM adapter using API key auth (v1)."""

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._location_id: str | None = None
        self._connected: bool = False

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def connect(self) -> None:
        if not self._api_key:
            logger.warning("ghl_credentials_missing")
            self._connected = False
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{_GHL_BASE_URL}/users/me",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                self._connected = True
                logger.info("ghl_connected")
        except Exception as exc:
            logger.error("ghl_connect_failed", error=str(exc))
            self._connected = False
            raise

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("ghl_disconnected")

    def is_connected(self) -> bool:
        return self._connected

    async def _reload_credentials(self) -> None:
        from app.services.settings_service import get_setting
        self._api_key = await get_setting("ghl_api_key")
        self._location_id = await get_setting("ghl_location_id")

    async def _ensure_connected(self) -> None:
        if not self._connected:
            await self._reload_credentials()
            await self.connect()

    async def get_contact(self, contact_id: str) -> dict[str, Any]:
        await self._ensure_connected()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_GHL_BASE_URL}/contacts/{contact_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json().get("contact", resp.json())

    async def create_note(self, client_id: str, note: str) -> dict[str, Any]:
        await self._ensure_connected()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_GHL_BASE_URL}/contacts/{client_id}/notes",
                headers=self._headers(),
                json={"body": note},
            )
            resp.raise_for_status()
            data = resp.json()
            return {"success": True, "note_id": data.get("id"), "raw": data}

    async def search_leads(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        await self._ensure_connected()
        params: dict[str, Any] = {}
        if self._location_id:
            params["locationId"] = self._location_id
        if "query" in filters:
            params["query"] = filters["query"]
        if "limit" in filters:
            params["limit"] = filters["limit"]
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_GHL_BASE_URL}/contacts/search",
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("contacts", [])

    async def send_message(
        self, contact_id: str, message: str, channel: str = "sms"
    ) -> dict[str, Any]:
        await self._ensure_connected()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_GHL_BASE_URL}/conversations/messages",
                headers=self._headers(),
                json={
                    "type": channel.upper(),
                    "contactId": contact_id,
                    "message": message,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def get_pipeline_stages(self) -> list[dict[str, Any]]:
        await self._ensure_connected()
        params: dict[str, Any] = {}
        if self._location_id:
            params["locationId"] = self._location_id
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_GHL_BASE_URL}/pipelines",
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            return resp.json().get("pipelines", [])


# Singleton
ghl_adapter = GoHighLevelAdapter()
