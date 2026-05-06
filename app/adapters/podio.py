from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.adapters.base import BaseAdapter

logger = structlog.get_logger(__name__)

_TOKEN_URL = "https://podio.com/oauth/token"
_API_BASE = "https://api.podio.com"


class PodioAdapter(BaseAdapter):
    """Podio CRM adapter using client-credentials OAuth."""

    def __init__(self) -> None:
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._app_id: str | None = None
        self._connected: bool = False
        self._access_token: str | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def connect(self) -> None:
        if not self._client_id or not self._client_secret:
            logger.warning("podio_credentials_missing")
            self._connected = False
            return
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                )
                resp.raise_for_status()
                self._access_token = resp.json()["access_token"]
                self._connected = True
                logger.info("podio_connected")
        except Exception as exc:
            logger.error("podio_connect_failed", error=str(exc))
            self._connected = False
            raise

    async def disconnect(self) -> None:
        self._connected = False
        self._access_token = None
        logger.info("podio_disconnected")

    def is_connected(self) -> bool:
        return self._connected

    async def _reload_credentials(self) -> None:
        from app.services.settings_service import get_setting
        self._client_id = await get_setting("podio_client_id")
        self._client_secret = await get_setting("podio_client_secret")
        self._app_id = await get_setting("podio_app_id")

    async def _ensure_connected(self) -> None:
        if not self._connected:
            await self._reload_credentials()
            await self.connect()

    async def get_contact(self, contact_id: str) -> dict[str, Any]:
        await self._ensure_connected()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_API_BASE}/item/{contact_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def create_note(self, client_id: str, note: str) -> dict[str, Any]:
        await self._ensure_connected()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_API_BASE}/comment/item/{client_id}",
                headers=self._headers(),
                json={"value": note},
            )
            resp.raise_for_status()
            data = resp.json()
            return {"success": True, "comment_id": data.get("comment_id"), "raw": data}

    async def search_leads(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        await self._ensure_connected()
        if not self._app_id:
            raise ValueError("PODIO_APP_ID is required for search_leads")
        body: dict[str, Any] = {}
        if "query" in filters:
            body["filters"] = {}
        if "limit" in filters:
            body["limit"] = filters["limit"]
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_API_BASE}/item/app/{self._app_id}/filter/",
                headers=self._headers(),
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("items", [])


# Singleton
podio_adapter = PodioAdapter()
