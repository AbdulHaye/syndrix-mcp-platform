from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.adapters.base import BaseAdapter

logger = structlog.get_logger(__name__)

_SLACK_API = "https://slack.com/api"


class SlackAdapter(BaseAdapter):
    """Slack Web API adapter using bot token."""

    def __init__(self) -> None:
        self._bot_token: str | None = None
        self._signing_secret: str | None = None
        self._connected: bool = False

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def connect(self) -> None:
        if not self._bot_token:
            logger.warning("slack_credentials_missing")
            self._connected = False
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{_SLACK_API}/auth.test",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    raise ValueError(data.get("error", "auth.test failed"))
                self._connected = True
                logger.info("slack_connected", team=data.get("team"))
        except Exception as exc:
            logger.error("slack_connect_failed", error=str(exc))
            self._connected = False
            raise

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("slack_disconnected")

    def is_connected(self) -> bool:
        return self._connected

    async def _reload_credentials(self) -> None:
        from app.services.settings_service import get_setting
        self._bot_token = await get_setting("slack_bot_token")
        self._signing_secret = await get_setting("slack_signing_secret")

    async def _ensure_connected(self) -> None:
        if not self._connected:
            await self._reload_credentials()
            await self.connect()

    def _check_ok(self, data: dict[str, Any]) -> dict[str, Any]:
        if not data.get("ok"):
            raise ValueError(data.get("error", "Slack API error"))
        return data

    async def get_contact(self, contact_id: str) -> dict[str, Any]:
        await self._ensure_connected()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_SLACK_API}/users.info",
                headers=self._headers(),
                params={"user": contact_id},
            )
            resp.raise_for_status()
            data = self._check_ok(resp.json())
            return data.get("user", data)

    async def create_note(self, client_id: str, note: str) -> dict[str, Any]:
        return await self.send_message(channel=client_id, text=note)

    async def search_leads(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        await self._ensure_connected()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_SLACK_API}/users.list",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = self._check_ok(resp.json())
            members: list[dict[str, Any]] = data.get("members", [])
        query = str(filters.get("query", "")).lower()
        if query:
            members = [
                m for m in members
                if query in m.get("name", "").lower()
                or query in m.get("real_name", "").lower()
            ]
        return members[: filters.get("limit", 20)]

    async def send_message(self, channel: str, text: str) -> dict[str, Any]:
        await self._ensure_connected()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_SLACK_API}/chat.postMessage",
                headers=self._headers(),
                json={"channel": channel, "text": text},
            )
            resp.raise_for_status()
            data = self._check_ok(resp.json())
            return {"success": True, "ts": data.get("ts"), "channel": data.get("channel")}

    async def get_channel_history(
        self, channel: str, limit: int = 100
    ) -> dict[str, Any]:
        await self._ensure_connected()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_SLACK_API}/conversations.history",
                headers=self._headers(),
                params={"channel": channel, "limit": limit},
            )
            resp.raise_for_status()
            data = self._check_ok(resp.json())
            return {"messages": data.get("messages", []), "has_more": data.get("has_more", False)}


# Singleton
slack_adapter = SlackAdapter()
