from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.adapters.base import BaseAdapter

logger = structlog.get_logger(__name__)

_TOKEN_URL = "https://podio.com/oauth/token"
_API_BASE = "https://api.podio.com"

# All Podio operations are confined to this workspace (Podio "space") unless a
# different name is configured via the `podio_workspace` setting.
_DEFAULT_WORKSPACE = "Test Work"


class PodioAdapter(BaseAdapter):
    """Podio CRM adapter using client-credentials OAuth.

    Every operation is scoped to a single Podio workspace (space). The adapter
    resolves the configured workspace name to a ``space_id`` and refuses to
    read or write items that live outside it.
    """

    def __init__(self) -> None:
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._app_id: str | None = None
        self._app_token: str | None = None
        self._username: str | None = None
        self._password: str | None = None
        self._workspace_name: str = _DEFAULT_WORKSPACE
        self._space_id_setting: str | None = None
        self._space_id: int | None = None
        self._connected: bool = False
        self._access_token: str | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    def _auth_payload(self) -> tuple[dict[str, str], str]:
        """Build the OAuth token request based on which credentials are present.

        Podio's ``client_credentials`` grant only authenticates the API client
        and cannot read orgs/workspaces/items. To actually access data we need
        either user auth (``password``) or app auth (``app``).
        """
        base = {"client_id": self._client_id or "", "client_secret": self._client_secret or ""}
        if self._username and self._password:
            return ({**base, "grant_type": "password",
                     "username": self._username, "password": self._password}, "password")
        if self._app_id and self._app_token:
            return ({**base, "grant_type": "app",
                     "app_id": self._app_id, "app_token": self._app_token}, "app")
        return ({**base, "grant_type": "client_credentials"}, "client_credentials")

    async def connect(self) -> None:
        if not self._client_id or not self._client_secret:
            logger.warning("podio_credentials_missing")
            self._connected = False
            return
        data, flow = self._auth_payload()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(_TOKEN_URL, data=data)
                resp.raise_for_status()
                self._access_token = resp.json()["access_token"]
                self._connected = True
                logger.info("podio_connected", flow=flow)
        except Exception as exc:
            logger.error("podio_connect_failed", flow=flow, error=str(exc))
            self._connected = False
            raise

        # Resolve the configured workspace to a space_id so every subsequent
        # call can be confined to it.
        await self._resolve_space()

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
        self._app_token = await get_setting("podio_app_token")
        self._username = await get_setting("podio_username")
        self._password = await get_setting("podio_password")
        self._space_id_setting = await get_setting("podio_space_id")
        self._workspace_name = (await get_setting("podio_workspace")) or _DEFAULT_WORKSPACE

    async def _ensure_connected(self) -> None:
        if not self._connected:
            await self._reload_credentials()
            await self.connect()

    # ── Workspace scoping ────────────────────────────────────────────────────

    async def _resolve_space(self) -> None:
        """Resolve the configured workspace name to a Podio space_id.

        Tries the account's org/space listing first; falls back to the space of
        the configured app. Leaves ``_space_id`` as None if it cannot confirm a
        space whose name matches the configured workspace.
        """
        self._space_id = None

        # An explicitly selected space_id (from the workspace dropdown) wins —
        # no name matching required.
        if self._space_id_setting:
            try:
                self._space_id = int(self._space_id_setting)
                logger.info("podio_workspace_selected", space_id=self._space_id)
                return
            except (TypeError, ValueError):
                logger.warning("podio_space_id_invalid", value=self._space_id_setting)

        target = (self._workspace_name or "").strip().lower()
        if not target:
            return
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Method 1 — list orgs and their spaces (account-level auth).
                resp = await client.get(f"{_API_BASE}/org/", headers=self._headers())
                if resp.status_code == 200:
                    for org in resp.json() or []:
                        for space in org.get("spaces") or []:
                            if (space.get("name") or "").strip().lower() == target:
                                self._space_id = space.get("space_id")
                                logger.info(
                                    "podio_workspace_resolved",
                                    workspace=self._workspace_name,
                                    space_id=self._space_id,
                                    via="org",
                                )
                                return

                # Method 2 — derive the space from the configured app and
                # confirm its name matches (app-level auth).
                if self._app_id:
                    app_resp = await client.get(
                        f"{_API_BASE}/app/{self._app_id}", headers=self._headers()
                    )
                    if app_resp.status_code == 200:
                        sid = app_resp.json().get("space_id")
                        if sid:
                            sp = await client.get(
                                f"{_API_BASE}/space/{sid}", headers=self._headers()
                            )
                            if sp.status_code == 200 and (
                                (sp.json().get("name") or "").strip().lower() == target
                            ):
                                self._space_id = sid
                                logger.info(
                                    "podio_workspace_resolved",
                                    workspace=self._workspace_name,
                                    space_id=sid,
                                    via="app",
                                )
                                return
        except Exception as exc:  # noqa: BLE001
            logger.error("podio_workspace_resolve_failed", error=str(exc))

        logger.warning("podio_workspace_not_found", workspace=self._workspace_name)

    async def list_workspaces(self) -> list[dict[str, Any]]:
        """Return every workspace (space) the credentials can see.

        Used to populate the workspace selector. Does NOT require a workspace to
        already be resolved.
        """
        await self._ensure_connected()
        workspaces: list[dict[str, Any]] = []
        seen: set[int] = set()
        org_error: str | None = None
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Primary — all orgs and the spaces within them (account-level auth).
            resp = await client.get(f"{_API_BASE}/org/", headers=self._headers())
            if resp.status_code == 200:
                for org in resp.json() or []:
                    for space in org.get("spaces") or []:
                        sid = space.get("space_id")
                        if sid and sid not in seen:
                            seen.add(sid)
                            workspaces.append(
                                {
                                    "space_id": sid,
                                    "name": space.get("name"),
                                    "org_id": org.get("org_id"),
                                    "org_name": org.get("name"),
                                }
                            )
            else:
                org_error = self._format_podio_error(resp)

            # Fallback — at least surface the configured app's own workspace
            # when org listing isn't available (app-scoped tokens).
            if not workspaces and self._app_id:
                app_resp = await client.get(
                    f"{_API_BASE}/app/{self._app_id}", headers=self._headers()
                )
                if app_resp.status_code == 200:
                    sid = app_resp.json().get("space_id")
                    if sid and sid not in seen:
                        sp = await client.get(
                            f"{_API_BASE}/space/{sid}", headers=self._headers()
                        )
                        name = sp.json().get("name") if sp.status_code == 200 else None
                        workspaces.append(
                            {"space_id": sid, "name": name, "org_id": None, "org_name": None}
                        )

        # Surface the raw Podio error when nothing could be listed.
        if not workspaces and org_error is not None:
            raise RuntimeError(org_error)

        logger.info("podio_workspaces_listed", count=len(workspaces))
        return workspaces

    @staticmethod
    def _format_podio_error(resp: httpx.Response) -> str:
        """Extract the original Podio error message from a failed response."""
        try:
            data = resp.json()
            detail = (
                data.get("error_description")
                or data.get("error")
                or resp.text
            )
        except Exception:  # noqa: BLE001
            detail = resp.text
        return f"Podio API {resp.status_code}: {detail}"

    def _ensure_workspace(self) -> None:
        """Fail closed unless the target workspace has been resolved."""
        if not self._space_id:
            raise ValueError(
                f"Podio workspace '{self._workspace_name}' could not be resolved; "
                "refusing to operate outside it. Check the workspace name and that "
                "the Podio credentials can access it."
            )

    def _item_space_id(self, item: dict[str, Any]) -> Any:
        return (item.get("app") or {}).get("space_id") or (
            item.get("space") or {}
        ).get("space_id")

    def _assert_item_in_workspace(self, item: dict[str, Any], item_id: Any) -> None:
        sid = self._item_space_id(item)
        if sid != self._space_id:
            raise ValueError(
                f"Item {item_id} is not in the '{self._workspace_name}' workspace; "
                "access denied."
            )

    # ── Operations (all confined to the resolved workspace) ───────────────────

    async def get_contact(self, contact_id: str) -> dict[str, Any]:
        await self._ensure_connected()
        self._ensure_workspace()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_API_BASE}/item/{contact_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            item = resp.json()
        self._assert_item_in_workspace(item, contact_id)
        return item

    async def create_note(self, client_id: str, note: str) -> dict[str, Any]:
        await self._ensure_connected()
        self._ensure_workspace()
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Confirm the target item lives in the allowed workspace first.
            item_resp = await client.get(
                f"{_API_BASE}/item/{client_id}",
                headers=self._headers(),
            )
            item_resp.raise_for_status()
            self._assert_item_in_workspace(item_resp.json(), client_id)

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
        self._ensure_workspace()
        query = str(filters.get("query", "")).strip()
        limit = filters.get("limit") or 10
        body: dict[str, Any] = {"query": query, "ref_type": "item", "limit": limit}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_API_BASE}/search/space/{self._space_id}/v2/",
                headers=self._headers(),
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
        # The search endpoint may return a bare list or {"results": [...]}.
        if isinstance(data, dict):
            results = data.get("results", [])
        else:
            results = data
        return results if isinstance(results, list) else []


# Singleton
podio_adapter = PodioAdapter()
