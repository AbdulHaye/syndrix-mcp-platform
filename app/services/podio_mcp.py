"""Podio hosted MCP server integration.

Authenticates to https://mcp.podio.com via OAuth (authorization-code + PKCE) and
exposes Podio's MCP tools to the agent. Replaces the old REST PodioAdapter path.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse
from contextlib import asynccontextmanager
from typing import Any

import httpx
import structlog

from app.services.settings_service import get_setting, upsert_setting

logger = structlog.get_logger(__name__)

DEFAULTS = {
    "podio_mcp_server_url": "https://mcp.podio.com/mcp",
    "podio_mcp_authorize_url": "https://mcp.podio.com/oauth/authorize",
    "podio_mcp_token_url": "https://mcp.podio.com/oauth/token",
}

# Short-lived PKCE store: state -> (code_verifier, created_at, redirect_uri, frontend_redirect).
# redirect_uri/frontend_redirect are derived per-request (see app/services/request_origin.py)
# so they're always correct for whatever host the user is actually on — no manual
# per-environment URL configuration needed. In-memory is fine for a single-instance deployment.
_pkce_store: dict[str, tuple[str, float, str, str]] = {}
_PKCE_TTL = 600  # seconds


class PodioMCP:
    async def _cfg(self, key: str) -> str:
        return (await get_setting(key)) or DEFAULTS.get(key, "")

    # ── OAuth ────────────────────────────────────────────────────────────────

    async def build_authorize_url(self, redirect_uri: str, frontend_redirect: str) -> str:
        client_id = await get_setting("podio_mcp_client_id")
        if not client_id:
            raise ValueError("Podio MCP Client ID is not configured (Settings → Podio).")

        authorize = await self._cfg("podio_mcp_authorize_url")
        scope = await get_setting("podio_mcp_scope") or ""

        # PKCE (S256)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(24)
        self._gc_pkce()
        _pkce_store[state] = (verifier, time.time(), redirect_uri, frontend_redirect)

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if scope:
            params["scope"] = scope
        return f"{authorize}?{urllib.parse.urlencode(params)}"

    @staticmethod
    def peek_frontend_redirect(state: str | None) -> str | None:
        """Non-destructive lookup — used by the callback route to know where to
        send the browser even before (or if) exchange_code() runs."""
        if not state:
            return None
        entry = _pkce_store.get(state)
        return entry[3] if entry else None

    @staticmethod
    def discard_state(state: str | None) -> None:
        if state:
            _pkce_store.pop(state, None)

    async def exchange_code(self, code: str, state: str) -> dict[str, Any]:
        entry = _pkce_store.pop(state, None)
        if not entry:
            raise ValueError("Invalid or expired OAuth state.")
        verifier, _, redirect_uri, _frontend = entry

        client_id = await get_setting("podio_mcp_client_id")
        client_secret = await get_setting("podio_mcp_client_secret")
        token_url = await self._cfg("podio_mcp_token_url")

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        }
        if client_secret:
            data["client_secret"] = client_secret

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(token_url, data=data)
            resp.raise_for_status()
            tok = resp.json()
        await self._store_token(tok)
        logger.info("podio_mcp_authorized")
        return tok

    async def _store_token(self, tok: dict[str, Any]) -> None:
        if tok.get("access_token"):
            await upsert_setting("podio_mcp_access_token", tok["access_token"])
        if tok.get("refresh_token"):
            await upsert_setting("podio_mcp_refresh_token", tok["refresh_token"])
        expires_in = tok.get("expires_in")
        if expires_in:
            expiry = int(time.time()) + int(expires_in) - 60  # refresh 1 min early
            await upsert_setting("podio_mcp_token_expiry", str(expiry))

    async def _refresh(self) -> str | None:
        refresh = await get_setting("podio_mcp_refresh_token")
        if not refresh:
            return None
        client_id = await get_setting("podio_mcp_client_id")
        client_secret = await get_setting("podio_mcp_client_secret")
        token_url = await self._cfg("podio_mcp_token_url")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
        }
        if client_secret:
            data["client_secret"] = client_secret
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(token_url, data=data)
                if resp.status_code != 200:
                    logger.warning("podio_mcp_refresh_failed", status=resp.status_code)
                    return None
                tok = resp.json()
            await self._store_token(tok)
            return tok.get("access_token")
        except Exception as exc:  # noqa: BLE001
            logger.warning("podio_mcp_refresh_error", error=str(exc))
            return None

    async def _get_valid_token(self) -> str | None:
        token = await get_setting("podio_mcp_access_token")
        expiry = await get_setting("podio_mcp_token_expiry")
        if token and expiry:
            try:
                if int(expiry) > int(time.time()):
                    return token
            except ValueError:
                pass
        refreshed = await self._refresh()
        return refreshed or token

    async def is_connected(self) -> bool:
        return bool(await get_setting("podio_mcp_access_token"))

    async def disconnect(self) -> None:
        for key in ("podio_mcp_access_token", "podio_mcp_refresh_token", "podio_mcp_token_expiry"):
            await upsert_setting(key, None)
        logger.info("podio_mcp_disconnected")

    @staticmethod
    def _gc_pkce() -> None:
        now = time.time()
        for st in [s for s, (_, ts) in _pkce_store.items() if now - ts > _PKCE_TTL]:
            _pkce_store.pop(st, None)

    # ── MCP client ─────────────────────────────────────────────────────────────

    @asynccontextmanager
    async def _session(self):
        token = await self._get_valid_token()
        if not token:
            raise ValueError("Not connected to Podio. Click Connect to authenticate.")
        server = await self._cfg("podio_mcp_server_url")

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        headers = {"Authorization": f"Bearer {token}"}
        async with streamablehttp_client(server, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return Podio's MCP tools as Ollama function-calling schemas."""
        async with self._session() as session:
            result = await session.list_tools()
        tools: list[dict[str, Any]] = []
        for t in result.tools:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.inputSchema or {"type": "object", "properties": {}},
                    },
                }
            )
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self._session() as session:
            result = await session.call_tool(name, arguments=arguments)
        return self._result_to_data(result)

    @staticmethod
    def _result_to_data(result: Any) -> dict[str, Any]:
        # Podio's MCP tools put a human-readable summary in text content blocks and
        # the actual records in structuredContent — include both.
        parts: list[str] = []
        for block in getattr(result, "content", None) or []:
            text = getattr(block, "text", None)
            parts.append(text if text is not None else str(block))
        data: dict[str, Any] = {
            "isError": bool(getattr(result, "isError", False)),
            "content": "\n".join(parts),
        }
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            data["data"] = structured
        return data

    # ── Workspace selection ────────────────────────────────────────────────────

    async def list_organizations(self) -> list[dict[str, Any]]:
        res = await self.call_tool("get_organizations", {})
        return (res.get("data") or {}).get("items", []) or []

    async def list_spaces(self, org_id: int) -> list[dict[str, Any]]:
        res = await self.call_tool("get_spaces_in_organization", {"org_id": int(org_id)})
        return (res.get("data") or {}).get("items", []) or []

    async def get_selected_workspace(self) -> dict[str, Any] | None:
        sid = await get_setting("podio_mcp_space_id")
        if not sid:
            return None
        return {
            "space_id": int(sid) if str(sid).isdigit() else sid,
            "name": await get_setting("podio_mcp_space_name"),
            "org_name": await get_setting("podio_mcp_org_name"),
        }

    async def set_selected_workspace(
        self, space_id: int, name: str | None = None, org_name: str | None = None
    ) -> None:
        await upsert_setting("podio_mcp_space_id", str(space_id))
        await upsert_setting("podio_mcp_space_name", name or "")
        await upsert_setting("podio_mcp_org_name", org_name or "")
        logger.info("podio_mcp_workspace_selected", space_id=space_id, name=name)


podio_mcp = PodioMCP()
