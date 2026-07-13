"""Podio REST API integration for file upload + attach.

Podio's hosted MCP server (mcp.podio.com) cannot upload files, so we use the Podio
REST API directly. The REST API needs a *user* access token obtained via the
authorization_code OAuth flow (the client_credentials grant authenticates no user
and returns 403 "Authentication as None" — that was the Session 8 failure).

Credentials are reused from PODIO_CLIENT_ID / PODIO_CLIENT_SECRET (env), overridable
via the integration_settings keys ``podio_rest_client_id`` / ``podio_rest_client_secret``.
Tokens are stored in integration_settings under ``podio_rest_*``.

Docs:
  upload  POST {api}/file/v2/         multipart: source=<bytes>, filename=<name>  -> {file_id, ...}
  attach  POST {api}/file/{id}/attach json: {ref_type, ref_id}   ref_type in item|task|comment|status|space
  auth header: "Authorization: OAuth2 <access_token>"
"""
from __future__ import annotations

import asyncio
import os
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from app.services.settings_service import get_setting, upsert_setting

logger = structlog.get_logger(__name__)

DEFAULTS = {
    "podio_rest_authorize_url": "https://podio.com/oauth/authorize",
    # Token endpoint lives on api.podio.com (NOT podio.com) — the latter 404s/redirects
    # and causes "exchange_failed".
    "podio_rest_token_url": "https://api.podio.com/oauth/token/v2",
    "podio_rest_api_base": "https://api.podio.com",
    "podio_rest_redirect_uri": "http://localhost:8000/integrations/podio-files/callback",
}

# state -> created_at (Podio's REST OAuth does not use PKCE; we still validate state).
_state_store: dict[str, float] = {}
_STATE_TTL = 600


class PodioREST:
    async def _cfg(self, key: str) -> str:
        return (await get_setting(key)) or DEFAULTS.get(key, "")

    async def _client_id(self) -> str:
        # Prefer a dedicated REST app id, then env, then fall back to the MCP app id
        # the user already entered in Settings (Podio MCP group).
        return (
            (await get_setting("podio_rest_client_id"))
            or os.environ.get("PODIO_CLIENT_ID", "")
            or (await get_setting("podio_mcp_client_id"))
            or ""
        )

    async def _client_secret(self) -> str:
        return (
            (await get_setting("podio_rest_client_secret"))
            or os.environ.get("PODIO_CLIENT_SECRET", "")
            or (await get_setting("podio_mcp_client_secret"))
            or ""
        )

    # ── OAuth (authorization_code) ─────────────────────────────────────────────

    async def build_authorize_url(self) -> str:
        client_id = await self._client_id()
        if not client_id:
            raise ValueError(
                "Podio API Client ID is not configured. Set PODIO_CLIENT_ID in .env "
                "(or podio_rest_client_id in Settings)."
            )
        authorize = await self._cfg("podio_rest_authorize_url")
        redirect = await self._cfg("podio_rest_redirect_uri")

        state = secrets.token_urlsafe(24)
        self._gc_state()
        _state_store[state] = time.time()

        params = {
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "state": state,
        }
        return f"{authorize}?{urllib.parse.urlencode(params)}"

    async def exchange_code(self, code: str, state: str) -> dict[str, Any]:
        if state not in _state_store:
            raise ValueError("Invalid or expired OAuth state.")
        _state_store.pop(state, None)

        token_url = await self._cfg("podio_rest_token_url")
        redirect = await self._cfg("podio_rest_redirect_uri")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "client_id": await self._client_id(),
            "client_secret": await self._client_secret(),
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Podio's /oauth/token/v2 expects a JSON body (not form-encoded).
            resp = await client.post(token_url, json=data)
            if resp.status_code != 200:
                detail = resp.text[:300]
                logger.error("podio_rest_token_error", status=resp.status_code, url=token_url, detail=detail)
                raise RuntimeError(f"Podio token endpoint {resp.status_code}: {detail}")
            tok = resp.json()
        await self._store_token(tok)
        logger.info("podio_rest_authorized")
        return tok

    async def _store_token(self, tok: dict[str, Any]) -> None:
        if tok.get("access_token"):
            await upsert_setting("podio_rest_access_token", tok["access_token"])
        if tok.get("refresh_token"):
            await upsert_setting("podio_rest_refresh_token", tok["refresh_token"])
        expires_in = tok.get("expires_in")
        if expires_in:
            await upsert_setting("podio_rest_token_expiry", str(int(time.time()) + int(expires_in) - 60))

    async def _refresh(self) -> str | None:
        refresh = await get_setting("podio_rest_refresh_token")
        if not refresh:
            return None
        token_url = await self._cfg("podio_rest_token_url")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": await self._client_id(),
            "client_secret": await self._client_secret(),
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(token_url, json=data)  # JSON body, like exchange
                if resp.status_code != 200:
                    logger.warning("podio_rest_refresh_failed", status=resp.status_code)
                    return None
                tok = resp.json()
            await self._store_token(tok)
            return tok.get("access_token")
        except Exception as exc:  # noqa: BLE001
            logger.warning("podio_rest_refresh_error", error=str(exc))
            return None

    async def _get_valid_token(self) -> str | None:
        token = await get_setting("podio_rest_access_token")
        expiry = await get_setting("podio_rest_token_expiry")
        if token and expiry:
            try:
                if int(expiry) > int(time.time()):
                    return token
            except ValueError:
                pass
        return (await self._refresh()) or token

    async def is_connected(self) -> bool:
        return bool(await get_setting("podio_rest_access_token"))

    async def disconnect(self) -> None:
        for key in ("podio_rest_access_token", "podio_rest_refresh_token", "podio_rest_token_expiry"):
            await upsert_setting(key, None)
        logger.info("podio_rest_disconnected")

    @staticmethod
    def _gc_state() -> None:
        now = time.time()
        for st in [s for s, ts in _state_store.items() if now - ts > _STATE_TTL]:
            _state_store.pop(st, None)

    async def _auth_headers(self) -> dict[str, str]:
        token = await self._get_valid_token()
        if not token:
            raise ValueError("Not connected to the Podio API. Connect Podio Files first.")
        return {"Authorization": f"OAuth2 {token}"}

    # ── File operations ────────────────────────────────────────────────────────

    async def upload_file(self, filename: str, content: bytes) -> dict[str, Any]:
        """Upload bytes to Podio. Returns the created file object (incl. file_id)."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{api}/file/v2/",
                headers=headers,
                files={"source": (filename, content)},
                data={"filename": filename},
            )
            resp.raise_for_status()
            data = resp.json()
        logger.info("podio_rest_file_uploaded", file_id=data.get("file_id"), filename=filename)
        return data

    async def attach_file(
        self, file_id: int, ref_type: str, ref_id: int, silent: bool = False
    ) -> dict[str, Any]:
        """Attach an uploaded file to an item/task/comment/status/space."""
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        body = {"ref_type": ref_type, "ref_id": int(ref_id), "silent": silent}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{api}/file/{int(file_id)}/attach", headers=headers, json=body)
            resp.raise_for_status()
            # Attach returns 204 No Content on success.
            data = resp.json() if resp.content else {}
        logger.info("podio_rest_file_attached", file_id=file_id, ref_type=ref_type, ref_id=ref_id)
        return data or {"file_id": file_id, "ref_type": ref_type, "ref_id": ref_id, "attached": True}

    async def upload_and_attach(
        self, filename: str, content: bytes, ref_type: str, ref_id: int
    ) -> dict[str, Any]:
        uploaded = await self.upload_file(filename, content)
        file_id = uploaded.get("file_id")
        await self.attach_file(file_id, ref_type, ref_id)
        return {"file_id": file_id, "filename": filename, "ref_type": ref_type, "ref_id": ref_id}

    async def get_item(self, item_id: int) -> dict[str, Any]:
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(f"{api}/item/{int(item_id)}", headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def get_app(self, app_id: int) -> dict[str, Any]:
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(f"{api}/app/{int(app_id)}", headers=headers)
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _normalise_stream_event(e: dict[str, Any]) -> dict[str, Any]:
        """Flatten one Podio activity-stream object to the fields the agent needs.
        Podio nests the object either at the top level or under 'data'; read both."""
        data = e.get("data") if isinstance(e.get("data"), dict) else {}

        def pick(*keys: str) -> Any:
            for src in (e, data):
                for k in keys:
                    v = src.get(k)
                    if v not in (None, "", [], {}):
                        return v
            return None

        app = e.get("app") or data.get("app") or {}
        if not isinstance(app, dict):
            app = {}
        by = e.get("created_by") or data.get("created_by") or {}
        if not isinstance(by, dict):
            by = {}
        return {
            "type": pick("type"),                        # item | task | status | ...
            "ref_id": pick("item_id", "task_id", "id", "comment_id", "status_id"),
            "title": pick("title", "text", "name", "subject"),
            "app": app.get("name"),
            "app_id": app.get("app_id"),
            "created_on": pick("created_on"),
            "last_edit_on": pick("last_edit_on"),
            "created_by": by.get("name"),
        }

    async def get_activity_stream(
        self,
        space_id: int | None = None,
        app_id: int | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Recent activity (items created/edited, comments, files, tasks) newest-first.

        Scope precedence: app_id > space_id > global. This is the reliable way to
        answer "what changed / what did I create or comment on today" across a WHOLE
        workspace: unlike sorting one app's items by last_edit_on, the stream spans
        every app AND surfaces comment/file activity — which do NOT bump an item's
        last_edit_on and are therefore invisible to a plain get_items sort.
        """
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        if app_id:
            path, scope = f"/stream/app/{int(app_id)}/", f"app:{app_id}"
        elif space_id:
            path, scope = f"/stream/space/{int(space_id)}/", f"space:{space_id}"
        else:
            path, scope = "/stream/", "global"
        params = {"limit": min(int(limit or 30), 100), "offset": int(offset or 0)}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(f"{api}{path}", headers=headers, params=params)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to get activity stream: HTTP {resp.status_code} "
                    f"{resp.reason_phrase} — {detail}"
                )
            raw = resp.json() if resp.content else []
        events = [
            self._normalise_stream_event(ev) for ev in (raw if isinstance(raw, list) else []) if isinstance(ev, dict)
        ]
        logger.info("podio_rest_activity_stream", scope=scope, count=len(events))
        return {"scope": scope, "count": len(events), "events": events}

    async def set_item_image(
        self, item_id: int, file_id: int, image_field: str | None = None
    ) -> dict[str, Any]:
        """Set an uploaded file as the item's image (the app's image-type field).

        Podio app items have no separate 'profile picture' — the item thumbnail is an
        image FIELD. We locate that field (type 'image') on the item's app and set its
        value to [file_id]. ``image_field`` (external_id or field_id) can target a
        specific field if an app has more than one.
        """
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}

        item = await self.get_item(item_id)
        app_id = (item.get("app") or {}).get("app_id")
        fields = (await self.get_app(app_id)).get("fields", []) if app_id else []

        target = None
        for f in fields:
            if f.get("type") != "image":
                continue
            if image_field is None:
                target = f
                break
            if str(f.get("external_id")) == str(image_field) or str(f.get("field_id")) == str(image_field):
                target = f
                break
        if target is None:
            raise RuntimeError(
                "This app has no image field, so an item image can't be set. Add an Image "
                "field to the app in Podio, or attach the file normally instead."
            )

        field_key = target.get("field_id") or target.get("external_id")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.put(
                f"{api}/item/{int(item_id)}/value/{field_key}",
                headers=headers,
                json=[int(file_id)],
            )
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
        logger.info("podio_rest_item_image_set", item_id=item_id, file_id=file_id, field=field_key)
        return {"item_id": item_id, "file_id": file_id, "image_field": field_key, **(data or {})}

    async def update_item(self, item_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        """Update one or more fields on a Podio item.

        ``fields`` is a dict mapping each field's external_id to its new value,
        using the same value format as create_item (text→str, category→option id,
        phone/email→[{type, value}], relationship→item_id int, etc.).
        """
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.put(
                f"{api}/item/{int(item_id)}",
                headers=headers,
                json={"fields": fields},
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to update item: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json() if resp.content else {}
        logger.info("podio_rest_item_updated", item_id=item_id, fields=list(fields.keys()))
        return {"success": True, "item_id": item_id, **(data or {})}

    async def delete_item(self, item_id: int, silent: bool = False) -> dict[str, Any]:
        """Permanently delete a Podio item. This action cannot be undone."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        params: dict[str, str] = {"silent": "true"} if silent else {}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.delete(
                f"{api}/item/{int(item_id)}",
                headers=headers,
                params=params or None,
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to delete item: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_item_deleted", item_id=item_id)
        return {"success": True, "deleted": True, "item_id": item_id}

    async def clone_item(self, item_id: int) -> dict[str, Any]:
        """Clone a Podio item within the same app. Returns the new item_id."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{api}/item/{int(item_id)}/clone/",
                headers=headers,
            )
            if resp.status_code not in (200, 201):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Clone failed: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json()
        new_id = data.get("item_id")
        logger.info("podio_rest_item_cloned", source_item_id=item_id, new_item_id=new_id)
        return {"item_id": new_id}

    async def bulk_delete_items(self, app_id: int, item_ids: list[int]) -> dict[str, Any]:
        """Delete multiple Podio items from an app in a single API call."""
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{api}/item/app/{int(app_id)}/delete/",
                headers=headers,
                json={"item_ids": [int(i) for i in item_ids]},
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Bulk delete failed: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json() if resp.content else {}
        logger.info("podio_rest_items_bulk_deleted", app_id=app_id, count=len(item_ids))
        return {"deleted": len(item_ids), "item_ids": item_ids, **(data or {})}

    async def get_item_files(self, item_id: int) -> list[dict[str, Any]]:
        """Return the list of files attached to a Podio item."""
        item = await self.get_item(item_id)
        files: list[dict[str, Any]] = item.get("files") or []
        return [
            {
                "file_id": f.get("file_id"),
                "name": f.get("name"),
                "size": f.get("size"),
                "mimetype": f.get("mimetype"),
                "link": f.get("link"),
            }
            for f in files
        ]

    async def delete_file(self, file_id: int) -> dict[str, Any]:
        """Permanently delete a Podio file by its file_id.

        Use this to remove an uploaded attachment. To find the file_id call
        get_item_files(item_id) first.
        """
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.delete(f"{api}/file/{int(file_id)}", headers=headers)
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to delete file: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_file_deleted", file_id=file_id)
        return {"success": True, "deleted": True, "file_id": file_id}

    async def download_file(self, file_id: int) -> tuple[str, str, bytes]:
        """Download a Podio file. Returns (filename, mimetype, bytes).

        Fetches file metadata first (GET /file/{id}) to get the download link,
        then follows that link with auth headers to retrieve the bytes.
        """
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            meta_resp = await client.get(f"{api}/file/{int(file_id)}", headers=headers)
            if meta_resp.status_code != 200:
                detail = meta_resp.text[:300]
                raise RuntimeError(
                    f"Failed to fetch file metadata: HTTP {meta_resp.status_code} — {detail}"
                )
            meta = meta_resp.json()
            link: str = meta.get("link") or ""
            filename: str = meta.get("name") or f"file_{file_id}"
            mimetype: str = meta.get("mimetype") or "application/octet-stream"
            if not link:
                raise RuntimeError("Podio did not return a download link for this file.")
            dl_resp = await client.get(link, headers=headers)
            if dl_resp.status_code != 200:
                raise RuntimeError(
                    f"Failed to download file bytes: HTTP {dl_resp.status_code}"
                )
        logger.info("podio_rest_file_downloaded", file_id=file_id, filename=filename, size=len(dl_resp.content))
        return filename, mimetype, dl_resp.content

    # ── Task label operations ──────────────────────────────────────────────────

    async def get_task_labels(self) -> list[dict[str, Any]]:
        """Return all task labels available to the authenticated user."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/task/label/", headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch labels: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: list[dict[str, Any]] = resp.json()
        return [
            {"label_id": l.get("label_id"), "name": l.get("text"), "color": l.get("color")}
            for l in (raw if isinstance(raw, list) else [])
        ]

    async def create_task_label(self, name: str, color: str) -> dict[str, Any]:
        """Create a new task label. ``color`` is a hex string without '#', e.g. 'DCDCDC'."""
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api}/task/label/",
                headers=headers,
                json={"text": name, "color": color.lstrip("#")},
            )
            if resp.status_code not in (200, 201):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to create label: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json()
        logger.info("podio_rest_task_label_created", label_id=data.get("label_id"), name=name)
        return {"label_id": data.get("label_id"), "name": data.get("text"), "color": data.get("color")}

    async def update_task_label(
        self, label_id: int, name: str | None = None, color: str | None = None
    ) -> dict[str, Any]:
        """Update an existing task label's name, color, or both."""
        if name is None and color is None:
            raise ValueError("At least one of 'name' or 'color' must be provided.")
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        body: dict[str, Any] = {}
        if name is not None:
            body["text"] = name
        if color is not None:
            body["color"] = color.lstrip("#")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                f"{api}/task/label/{int(label_id)}/",
                headers=headers,
                json=body,
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to update label: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json() if resp.content else {}
        logger.info("podio_rest_task_label_updated", label_id=label_id)
        return {"label_id": label_id, **(data or {})}

    async def delete_task_label(self, label_id: int) -> dict[str, Any]:
        """Permanently delete a task label by its ID."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(f"{api}/task/label/{int(label_id)}/", headers=headers)
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to delete label: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_task_label_deleted", label_id=label_id)
        return {"success": True, "deleted": True, "label_id": label_id}

    async def rank_task(
        self,
        task_id: int,
        before: int | None = None,
        after: int | None = None,
    ) -> dict[str, Any]:
        """Change the priority order of a Podio task relative to other tasks.

        NOTE: Podio's rank endpoint (POST /task/{id}/rank/) returns HTTP 410 Gone —
        the feature has been removed from the public API. This method will raise an
        error with a clear explanation rather than a confusing 410 response.
        """
        raise RuntimeError(
            "Podio's task rank API (POST /task/{id}/rank/) has been removed "
            "(returns HTTP 410 Gone). Task ordering can only be changed manually "
            "in the Podio UI."
        )

    async def uncomplete_task(self, task_id: int) -> dict[str, Any]:
        """Mark a completed Podio task back as incomplete.

        Official Podio endpoint: POST /task/{task_id}/incomplete
        (NOT DELETE /task/{task_id}/complete — that is a different operation.)
        """
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api}/task/{int(task_id)}/incomplete",
                headers=headers,
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to uncomplete task: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_task_uncompleted", task_id=task_id)
        return {"success": True, "task_id": task_id, "completed": False}

    async def reassign_task(self, task_id: int, user_id: int) -> dict[str, Any]:
        """Reassign a Podio task to a different user. Returns the updated task state.

        Uses POST /task/{task_id}/assign with {"responsible": user_id} — the dedicated
        assign endpoint (PUT /task/{id} with responsible as a nested object was the wrong format).
        """
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api}/task/{int(task_id)}/assign",
                headers=headers,
                json={"responsible": int(user_id)},
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to reassign task: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_task_reassigned", task_id=task_id, user_id=user_id)
        return await self.get_task(task_id)

    async def update_task(
        self,
        task_id: int,
        text: str | None = None,
        description: str | None = None,
        due_on: str | None = None,
        label_id: int | None = None,
    ) -> dict[str, Any]:
        """Update a Podio task's text, description, due date, or label.

        ``due_on`` must be "YYYY-MM-DD HH:MM:SS".
        ``label_id`` assigns a task label (use get_task_labels to find IDs).
        """
        body: dict[str, Any] = {}
        if text is not None:
            body["text"] = text
        if description is not None:
            body["description"] = description
        if due_on is not None:
            body["due_on"] = due_on
        if label_id is not None:
            body["label"] = label_id
        if not body:
            raise ValueError("At least one field must be provided to update.")
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                f"{api}/task/{int(task_id)}/",
                headers=headers,
                json=body,
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to update task: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json() if resp.content else {}
        logger.info("podio_rest_task_updated", task_id=task_id, fields=list(body.keys()))
        return {"success": True, "task_id": task_id, **(self._normalise_task(data) if data else {})}

    async def delete_task(self, task_id: int) -> dict[str, Any]:
        """Permanently delete a Podio task. This action cannot be undone."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(f"{api}/task/{int(task_id)}/", headers=headers)
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to delete task: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_task_deleted", task_id=task_id)
        return {"success": True, "deleted": True, "task_id": task_id}

    @staticmethod
    def _normalise_task(raw: dict[str, Any]) -> dict[str, Any]:
        ref = raw.get("ref") or {}
        ref_data = ref.get("data") or {}
        ref_app = ref_data.get("app") if isinstance(ref_data.get("app"), dict) else {}
        responsible = raw.get("responsible") or {}
        return {
            "task_id": raw.get("task_id"),
            "text": raw.get("text"),
            "description": raw.get("description"),
            "due_date": raw.get("due_date") or raw.get("due_on"),
            "completed": raw.get("completed", False),
            "completed_on": raw.get("completed_on"),
            "assigned_to": responsible.get("name"),
            "assigned_profile_id": responsible.get("profile_id") or responsible.get("user_id"),
            "ref_type": ref.get("type"),
            "ref_id": ref_data.get("item_id") or ref.get("id"),
            "ref_title": ref_data.get("title"),
            # The app the linked item belongs to (present on item-referenced tasks) —
            # lets callers tell WHICH app a task sits in and filter workspace tasks by app.
            "ref_app_id": ref_app.get("app_id"),
            "ref_app_name": ref_app.get("name"),
        }

    async def get_task(self, task_id: int) -> dict[str, Any]:
        """Fetch a single Podio task with full details."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/task/{int(task_id)}/", headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch task: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw = resp.json()
        return self._normalise_task(raw)

    async def get_tasks(
        self,
        space_id: int | None = None,
        responsible_user_id: int | None = None,
        completed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List Podio tasks with optional filters. Requires at least one of space_id
        or responsible_user_id — Podio rejects unfiltered task queries."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        params: dict[str, Any] = {
            "completed": "true" if completed else "false",
            "limit": limit,
            "offset": offset,
        }
        if space_id:
            params["space"] = space_id  # Podio tasks API uses 'space' not 'space_id'
        if responsible_user_id:
            params["responsible"] = responsible_user_id
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/task/", params=params, headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to get tasks: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw = resp.json()
        tasks = [self._normalise_task(t) for t in (raw if isinstance(raw, list) else [])]
        return {"success": True, "tasks": tasks, "count": len(tasks)}

    async def _all_space_tasks(
        self, space_id: int, completed: bool = False, page: int = 100, max_pages: int = 30
    ) -> list[dict[str, Any]]:
        """Every task in a workspace (paginated). Used as the fast-path source for
        get_app_tasks when the task list carries the linked item's app."""
        out: list[dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            res = await self.get_tasks(
                space_id=space_id, completed=completed, limit=page, offset=offset
            )
            batch = res.get("tasks", []) or []
            out.extend(batch)
            if len(batch) < page:
                break
            offset += page
        return out

    async def _app_item_ids(
        self, app_id: int, max_items: int = 300, page: int = 100
    ) -> tuple[list[int], bool]:
        """All item_ids in an app via POST /item/app/{app_id}/filter/ (paginated).
        Returns (ids, truncated) — truncated=True when the app has more than max_items."""
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        ids: list[int] = []
        offset = 0
        total = 0
        async with httpx.AsyncClient(timeout=60.0) as client:
            while len(ids) < max_items:
                resp = await client.post(
                    f"{api}/item/app/{int(app_id)}/filter/",
                    headers=headers,
                    json={"limit": min(page, max_items - len(ids)), "offset": offset},
                )
                if resp.status_code != 200:
                    detail = resp.text[:400]
                    raise RuntimeError(
                        f"Failed to list app items: HTTP {resp.status_code} "
                        f"{resp.reason_phrase} — {detail}"
                    )
                data = resp.json()
                items = data.get("items") or []
                total = data.get("total", total) or total
                for it in items:
                    iid = it.get("item_id")
                    if iid is not None:
                        ids.append(int(iid))
                if len(items) < page or not items:
                    break
                offset += len(items)
        truncated = total > len(ids)
        return ids, truncated

    async def get_app_tasks(
        self, app_id: int, completed: bool = False, max_items: int = 300
    ) -> dict[str, Any]:
        """All tasks on items in an app.

        Podio has NO server-side "tasks in app" filter — GET /task/app/{id}/ only
        returns tasks that reference the app OBJECT itself, not tasks on the app's
        items (confirmed: it returns 0 even when items have tasks). So we:
          1. Fast-path: pull the workspace task list and keep tasks whose linked
             item belongs to this app (a few calls) — used only when the list
             actually carries app info (ref_app_id populated).
          2. Otherwise: enumerate the app's items and gather each item's tasks
             (thorough; bounded by max_items, concurrency-limited).
        """
        app = await self.get_app(app_id)
        space_id = app.get("space_id") or (app.get("space") or {}).get("space_id")

        # ── Fast-path: filter workspace tasks by the task's linked-item app ──────
        if space_id:
            ws = await self._all_space_tasks(space_id, completed=completed)
            if any(t.get("ref_app_id") is not None for t in ws):
                matched = [t for t in ws if t.get("ref_app_id") == int(app_id)]
                logger.info(
                    "podio_rest_app_tasks", app_id=app_id, strategy="workspace_filter",
                    count=len(matched),
                )
                return {
                    "success": True, "app_id": int(app_id), "strategy": "workspace_filter",
                    "tasks": matched, "count": len(matched),
                }

        # ── Thorough: enumerate items, fetch each item's tasks ───────────────────
        item_ids, truncated = await self._app_item_ids(app_id, max_items=max_items)
        sem = asyncio.Semaphore(6)

        async def _one(iid: int) -> list[dict[str, Any]]:
            async with sem:
                try:
                    return await self.get_reference_tasks("item", iid)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("podio_rest_app_tasks_item_failed", item_id=iid, error=str(exc))
                    return []

        results = await asyncio.gather(*[_one(i) for i in item_ids])
        seen: set[int] = set()
        tasks: list[dict[str, Any]] = []
        for lst in results:
            for t in lst:
                tid = t.get("task_id")
                if bool(t.get("completed", False)) != bool(completed):
                    continue
                if tid in seen:
                    continue
                seen.add(tid)
                tasks.append(t)
        logger.info(
            "podio_rest_app_tasks", app_id=app_id, strategy="per_item",
            items_scanned=len(item_ids), count=len(tasks), truncated=truncated,
        )
        return {
            "success": True, "app_id": int(app_id), "strategy": "per_item",
            "items_scanned": len(item_ids), "items_truncated": truncated,
            "tasks": tasks, "count": len(tasks),
        }

    # ── Workflow / flow operations ─────────────────────────────────────────────

    # ── Workspace / space lifecycle ────────────────────────────────────────────

    _VALID_PRIVACY = {"open", "closed"}

    async def create_workspace(
        self,
        org_id: int,
        name: str,
        privacy: str,
        url_label: str | None = None,
    ) -> dict[str, Any]:
        """Create a new Podio workspace inside the given organisation.

        ``privacy`` must be 'open' or 'closed'.
        ``url_label`` is an optional URL slug; Podio derives one from the name if omitted.
        Returns the new space_id and full URL.
        """
        p = (privacy or "").strip().lower()
        if p not in self._VALID_PRIVACY:
            raise ValueError(
                f"privacy must be one of {sorted(self._VALID_PRIVACY)}; got '{privacy}'."
            )
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        body: dict[str, Any] = {"name": name, "privacy": p}
        if url_label is not None:
            body["url_label"] = url_label
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api}/space/org/{int(org_id)}/",
                headers=headers,
                json=body,
            )
            if resp.status_code not in (200, 201):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to create workspace: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json()
        space_id = data.get("space_id")
        logger.info("podio_rest_workspace_created", org_id=org_id, space_id=space_id, name=name)
        return {
            "space_id": space_id,
            "name": data.get("name", name),
            "url": data.get("url"),
            "privacy": p,
        }

    # ── Recurrence ─────────────────────────────────────────────────────────────

    _VALID_RECURRENCE_REF_TYPES = {"task"}
    _VALID_RECURRENCE_STEPS = {"daily", "weekly", "monthly"}

    def _check_recurrence_ref_type(self, ref_type: str) -> str:
        rt = (ref_type or "").strip().lower()
        if rt not in self._VALID_RECURRENCE_REF_TYPES:
            raise ValueError(
                f"ref_type must be one of {sorted(self._VALID_RECURRENCE_REF_TYPES)}; got '{ref_type}'."
            )
        return rt

    async def get_recurrence(self, ref_type: str, ref_id: int) -> dict[str, Any]:
        """Retrieve the recurring schedule set on a Podio task."""
        rt = self._check_recurrence_ref_type(ref_type)
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/recurrence/{rt}/{int(ref_id)}/", headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch recurrence: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json()
        return {"ref_type": rt, "ref_id": ref_id, **data}

    async def set_recurrence(
        self, ref_type: str, ref_id: int, schedule: dict[str, Any]
    ) -> dict[str, Any]:
        """Create or update a recurring schedule on a Podio task.

        ``schedule`` must include a 'step' key ('daily', 'weekly', or 'monthly')
        plus step-specific options:
          daily   — no extra keys required (frequency defaults to 1)
          weekly  — 'days_of_week': list[int] where 0=Mon … 6=Sun
          monthly — 'day_of_month': int (1–31)
        The same PUT endpoint is used for both create and update.
        """
        rt = self._check_recurrence_ref_type(ref_type)
        step = (schedule.get("step") or "").strip().lower()
        if step not in self._VALID_RECURRENCE_STEPS:
            raise ValueError(
                f"schedule.step must be one of {sorted(self._VALID_RECURRENCE_STEPS)}; got '{step}'."
            )
        schedule = {**schedule, "step": step}  # normalise step casing
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                f"{api}/recurrence/{rt}/{int(ref_id)}/",
                headers=headers,
                json=schedule,
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to set recurrence: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_recurrence_set", ref_type=rt, ref_id=ref_id, step=step)
        return {"success": True, "ref_type": rt, "ref_id": ref_id, "schedule": schedule}

    async def delete_recurrence(self, ref_type: str, ref_id: int) -> dict[str, Any]:
        """Remove a recurring schedule from a Podio task."""
        rt = self._check_recurrence_ref_type(ref_type)
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(f"{api}/recurrence/{rt}/{int(ref_id)}/", headers=headers)
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to delete recurrence: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_recurrence_deleted", ref_type=rt, ref_id=ref_id)
        return {"success": True, "deleted": True, "ref_type": rt, "ref_id": ref_id}

    # ── Reminders ──────────────────────────────────────────────────────────────

    _VALID_REMINDER_REF_TYPES = {"task", "item"}

    def _check_reminder_ref_type(self, ref_type: str) -> str:
        rt = (ref_type or "").strip().lower()
        if rt not in self._VALID_REMINDER_REF_TYPES:
            raise ValueError(
                f"ref_type must be one of {sorted(self._VALID_REMINDER_REF_TYPES)}; got '{ref_type}'."
            )
        return rt

    async def get_reminder(self, ref_type: str, ref_id: int) -> dict[str, Any]:
        """Retrieve the reminder set on a Podio task or item."""
        rt = self._check_reminder_ref_type(ref_type)
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/reminder/{rt}/{int(ref_id)}/", headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch reminder: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json()
        return {
            "ref_type": rt,
            "ref_id": ref_id,
            "remind_at": data.get("remind_at"),
            "remind_delta": data.get("remind_delta"),
        }

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        """Parse a Podio-style datetime string ('YYYY-MM-DD HH:MM:SS', date-only ok)."""
        if not value or not isinstance(value, str):
            return None
        s = value.strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    async def _ref_due_datetime(self, rt: str, ref_id: int) -> datetime | None:
        """Best-effort due datetime for a task or item. Podio reminders fire a number
        of minutes BEFORE this due date, so we need it to convert an absolute time."""
        if rt == "task":
            task = await self.get_task(ref_id)
            return self._parse_dt(task.get("due_date"))
        # item: use the first date-type field's start value
        item = await self.get_item(ref_id)
        for field in (item.get("fields") or []):
            if field.get("type") == "date":
                values = field.get("values") or []
                if values:
                    v = values[0]
                    return self._parse_dt(v.get("start") or v.get("start_date"))
        return None

    async def set_reminder(
        self,
        ref_type: str,
        ref_id: int,
        remind_delta: int | None = None,
        remind_at: str | None = None,
    ) -> dict[str, Any]:
        """Set or update a reminder on a Podio task or item.

        Podio reminders are RELATIVE: the API takes ``remind_delta`` = the number of
        minutes BEFORE the object's due date to fire. It does NOT accept an absolute
        time. As a convenience, if ``remind_at`` (e.g. '2026-07-02 09:00:00') is given
        instead, we read the object's due date and convert it to the required delta.
        """
        rt = self._check_reminder_ref_type(ref_type)

        delta: int | None = None
        if remind_delta is not None:
            try:
                delta = int(remind_delta)
            except (TypeError, ValueError):
                raise RuntimeError(
                    f"remind_delta must be an integer number of minutes; got {remind_delta!r}."
                )
        elif remind_at is not None:
            want = self._parse_dt(remind_at)
            if want is None:
                raise RuntimeError(
                    f"Could not parse remind_at {remind_at!r}; expected 'YYYY-MM-DD HH:MM:SS'."
                )
            due = await self._ref_due_datetime(rt, ref_id)
            if due is None:
                raise RuntimeError(
                    f"This {rt} has no due date, so Podio cannot set a reminder — reminders fire a "
                    f"number of minutes BEFORE the object's due date. Set a due date on the {rt} "
                    f"first, or pass remind_delta (minutes before the due date) directly."
                )
            delta = int(round((due - want).total_seconds() / 60))
            if delta < 0:
                raise RuntimeError(
                    f"remind_at ({remind_at}) is AFTER the {rt}'s due date "
                    f"({due:%Y-%m-%d %H:%M}); a Podio reminder must be before the due date. "
                    "Choose an earlier time or set remind_delta directly."
                )
        else:
            raise RuntimeError(
                "Provide remind_delta (minutes before the due date) or remind_at (absolute time)."
            )

        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                f"{api}/reminder/{rt}/{int(ref_id)}/",
                headers=headers,
                json={"remind_delta": delta},
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to set reminder: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_reminder_set", ref_type=rt, ref_id=ref_id, remind_delta=delta)

        # Compute the absolute fire time and read the reminder back so the caller
        # gets something concrete/verifiable (a bare success is invisible in the UI).
        remind_at: str | None = None
        try:
            due = await self._ref_due_datetime(rt, ref_id)
            if due is not None:
                remind_at = (due - timedelta(minutes=delta)).strftime("%Y-%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            pass
        verified = False
        try:
            confirmed = await self.get_reminder(rt, ref_id)
            verified = (
                confirmed.get("remind_delta") is not None
                or confirmed.get("remind_at") is not None
            )
        except Exception:  # noqa: BLE001
            pass
        when = f", firing at {remind_at}" if remind_at else ""
        content = (
            f"Reminder set on {rt} {ref_id}: {delta} minute(s) before its due date{when}. "
            "A Podio reminder is a notification, not a field shown on the record — "
            f"open the {rt} in Podio to see it."
        )
        return {
            "success": True,
            "ref_type": rt,
            "ref_id": ref_id,
            "remind_delta": delta,
            "remind_at": remind_at,
            "verified": verified,
            "content": content,
        }

    async def delete_reminder(self, ref_type: str, ref_id: int) -> dict[str, Any]:
        """Remove the reminder from a Podio task or item."""
        rt = self._check_reminder_ref_type(ref_type)
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(f"{api}/reminder/{rt}/{int(ref_id)}/", headers=headers)
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to delete reminder: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_reminder_deleted", ref_type=rt, ref_id=ref_id)
        return {"success": True, "deleted": True, "ref_type": rt, "ref_id": ref_id}

    # ── Calendar ───────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_calendar_event(event: dict[str, Any]) -> dict[str, Any]:
        ref = event.get("ref") or {}
        return {
            "title": event.get("title"),
            "due_date": event.get("start") or event.get("start_utc") or event.get("start_date"),
            "end_date": event.get("end") or event.get("end_utc") or event.get("end_date"),
            # source: "podio" for native items/tasks, or "google"/"exchange"/"live"
            # for events from an externally added (linked-account) calendar.
            "source": event.get("source"),
            "type": ref.get("type") or event.get("type"),
            "ref_id": (ref.get("data") or {}).get("item_id")
                      or (ref.get("data") or {}).get("task_id")
                      or ref.get("id"),
            "location": event.get("location"),
            "description": event.get("description"),
            "link": event.get("link"),
        }

    @staticmethod
    def _calendar_range(
        date_from: str | None, date_to: str | None, days_ahead: int = 90
    ) -> dict[str, str]:
        """Build Podio's REQUIRED calendar query params.

        Podio's /calendar/* endpoints reject the request with HTTP 400
        ("must be to_date") unless both date_from and date_to (YYYY-MM-DD) are
        supplied. Default to an 'upcoming' window: today → today + days_ahead.
        """
        today = datetime.now(timezone.utc).date()
        start = date_from or today.isoformat()
        end = date_to or (today + timedelta(days=days_ahead)).isoformat()
        return {"date_from": start, "date_to": end}

    async def get_calendar(
        self, date_from: str | None = None, date_to: str | None = None
    ) -> list[dict[str, Any]]:
        """Return tasks and item due dates across the user's Podio account.

        date_from/date_to are YYYY-MM-DD; both are required by Podio and default
        to today → +90 days when omitted.
        """
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        params = self._calendar_range(date_from, date_to)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/calendar/", headers=headers, params=params)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch calendar: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: list[dict[str, Any]] = resp.json()
        events = [self._normalise_calendar_event(e) for e in (raw if isinstance(raw, list) else [])]
        logger.info("podio_rest_calendar_fetched", count=len(events), **params)
        return events

    async def get_space_calendar(
        self, space_id: int, date_from: str | None = None, date_to: str | None = None
    ) -> list[dict[str, Any]]:
        """Return calendar events scoped to a specific Podio workspace."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        params = self._calendar_range(date_from, date_to)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api}/calendar/space/{int(space_id)}/", headers=headers, params=params
            )
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch workspace calendar: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: list[dict[str, Any]] = resp.json()
        events = [self._normalise_calendar_event(e) for e in (raw if isinstance(raw, list) else [])]
        logger.info("podio_rest_space_calendar_fetched", space_id=space_id, count=len(events), **params)
        return events

    async def get_app_calendar(
        self, app_id: int, date_from: str | None = None, date_to: str | None = None
    ) -> list[dict[str, Any]]:
        """Return calendar events for items with due dates in a specific Podio app."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        params = self._calendar_range(date_from, date_to)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api}/calendar/app/{int(app_id)}/", headers=headers, params=params
            )
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch app calendar: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: list[dict[str, Any]] = resp.json()
        events = [self._normalise_calendar_event(e) for e in (raw if isinstance(raw, list) else [])]
        logger.info("podio_rest_app_calendar_fetched", app_id=app_id, count=len(events), **params)
        return events

    async def list_linked_accounts(
        self, capability: str | None = None, provider: str | None = None
    ) -> list[dict[str, Any]]:
        """List the user's linked (external) accounts — e.g. calendars added via
        Podio's "Add Calendar" (Google/Exchange/Live). Filter by capability
        ("calendar", "contacts", "files", …) or provider."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        params: dict[str, Any] = {}
        if capability:
            params["capability"] = capability
        if provider:
            params["provider"] = provider
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/linked_account/", headers=headers, params=params)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to list linked accounts: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw = resp.json()
        accounts = [
            {
                "linked_account_id": a.get("linked_account_id"),
                "label": a.get("label"),
                "provider": a.get("provider"),
                "provider_name": a.get("provider_humanized_name"),
                "status": a.get("status"),
            }
            for a in (raw if isinstance(raw, list) else [])
        ]
        logger.info("podio_rest_linked_accounts_listed", count=len(accounts), capability=capability)
        return accounts

    async def get_linked_account_calendar(
        self,
        linked_account_id: int,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return events from ONE externally added (linked-account) calendar.

        These are the calendars added via Podio's "Add Calendar" — they are NOT
        returned by get_calendar / get_space_calendar / get_app_calendar, which
        only surface Podio-native items and tasks. Get the id from
        list_linked_accounts(capability="calendar")."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        params = self._calendar_range(date_from, date_to)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api}/calendar/linked_account/{int(linked_account_id)}/",
                headers=headers,
                params=params,
            )
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch linked-account calendar: HTTP {resp.status_code} "
                    f"{resp.reason_phrase} — {detail}"
                )
            raw = resp.json()
        events = [self._normalise_calendar_event(e) for e in (raw if isinstance(raw, list) else [])]
        logger.info(
            "podio_rest_linked_account_calendar_fetched",
            linked_account_id=linked_account_id, count=len(events), **params,
        )
        return events

    # ── Direct messaging / conversations ───────────────────────────────────────

    async def create_conversation(
        self, participant_ids: list[int], subject: str, text: str
    ) -> dict[str, Any]:
        """Start a new private conversation thread with one or more users."""
        if not participant_ids:
            raise ValueError("At least one participant user_id must be provided.")
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api}/conversation/",
                headers=headers,
                json={
                    "subject": subject,
                    "text": text,
                    "participants": [int(uid) for uid in participant_ids],
                },
            )
            if resp.status_code not in (200, 201):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to create conversation: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json()
        conv_id = data.get("conversation_id")
        logger.info("podio_rest_conversation_created", conversation_id=conv_id, participants=participant_ids)
        return {
            "conversation_id": conv_id,
            "subject": data.get("subject", subject),
            "created_on": data.get("created_on"),
        }

    async def reply_to_conversation(self, conversation_id: int, text: str) -> dict[str, Any]:
        """Add a reply to an existing Podio conversation thread."""
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api}/conversation/{int(conversation_id)}/reply/",
                headers=headers,
                json={"text": text},
            )
            if resp.status_code not in (200, 201):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to reply to conversation: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json() if resp.content else {}
        logger.info("podio_rest_conversation_reply_sent", conversation_id=conversation_id)
        return {
            "conversation_id": conversation_id,
            "message_id": data.get("message_id") if data else None,
            "created_on": data.get("created_on") if data else None,
        }

    async def list_conversations(self) -> list[dict[str, Any]]:
        """Return all conversation threads for the authenticated user."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/conversation/", headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to list conversations: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: list[dict[str, Any]] = resp.json()
        return [
            {
                "conversation_id": c.get("conversation_id"),
                "subject": c.get("subject"),
                "participants": [
                    {"user_id": p.get("user_id"), "name": p.get("name")}
                    for p in (c.get("participants") or [])
                ],
                "last_message": (c.get("last") or {}).get("text"),
                "unread_count": c.get("unread_count", 0),
                "created_on": c.get("created_on"),
            }
            for c in (raw if isinstance(raw, list) else [])
        ]

    async def get_conversation(self, conversation_id: int) -> dict[str, Any]:
        """Retrieve a specific conversation thread with all messages in full."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api}/conversation/{int(conversation_id)}/", headers=headers
            )
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch conversation: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw = resp.json()
        return {
            "conversation_id": raw.get("conversation_id"),
            "subject": raw.get("subject"),
            "participants": [
                {"user_id": p.get("user_id"), "name": p.get("name")}
                for p in (raw.get("participants") or [])
            ],
            "messages": [
                {
                    "message_id": m.get("message_id"),
                    "text": m.get("text"),
                    "created_by": (m.get("created_by") or {}).get("name"),
                    "created_on": m.get("created_on"),
                }
                for m in (raw.get("messages") or [])
            ],
            "created_on": raw.get("created_on"),
        }

    # ── App lifecycle ──────────────────────────────────────────────────────────

    async def create_app(
        self,
        space_id: int,
        name: str,
        item_name: str,
        fields: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a new Podio app inside a workspace.

        ``item_name`` is what a single record is called in this app (e.g. 'Contact', 'Lead').
        ``fields`` is an optional list of field definitions; each must have a 'type' and a
        nested 'config' dict with at minimum a 'label' key.
        Returns the new app_id and URL.
        """
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        config: dict[str, Any] = {"name": name, "item_name": item_name}
        if fields:
            config["fields"] = fields
        body: dict[str, Any] = {"space_id": int(space_id), "config": config}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{api}/app/", headers=headers, json=body)
            if resp.status_code not in (200, 201):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to create app: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json()
        app_id = data.get("app_id")
        logger.info("podio_rest_app_created", space_id=space_id, app_id=app_id, name=name)
        return {
            "app_id": app_id,
            "name": data.get("config", {}).get("name", name),
            "item_name": data.get("config", {}).get("item_name", item_name),
            "url": data.get("link"),
            "space_id": space_id,
        }

    # ── Workspace member operations ────────────────────────────────────────────

    _VALID_ROLES = {"admin", "regular", "light"}

    async def invite_workspace_member(
        self, space_id: int, identifier: str | int
    ) -> dict[str, Any]:
        """Invite a user to a workspace by email address or numeric user_id."""
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        # Route by type: strings with '@' are email addresses; everything else is a user_id.
        id_str = str(identifier).strip()
        if "@" in id_str:
            body: dict[str, Any] = {"mails": [id_str]}
        else:
            body = {"users": [int(id_str)]}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api}/space/{int(space_id)}/member/",
                headers=headers,
                json=body,
            )
            if resp.status_code not in (200, 201, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to invite member: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_workspace_member_invited", space_id=space_id, identifier=id_str)
        return {"success": True, "space_id": space_id, "invited": id_str}

    async def remove_workspace_member(self, space_id: int, user_id: int) -> dict[str, Any]:
        """Remove a member from a Podio workspace."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(
                f"{api}/space/{int(space_id)}/member/{int(user_id)}/",
                headers=headers,
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to remove member: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_workspace_member_removed", space_id=space_id, user_id=user_id)
        return {"success": True, "space_id": space_id, "removed_user_id": user_id}

    async def update_workspace_member_role(
        self, space_id: int, user_id: int, role: str
    ) -> dict[str, Any]:
        """Change a workspace member's access level (admin, regular, or light)."""
        r = (role or "").strip().lower()
        if r not in self._VALID_ROLES:
            raise ValueError(
                f"role must be one of {sorted(self._VALID_ROLES)}; got '{role}'."
            )
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                f"{api}/space/{int(space_id)}/member/{int(user_id)}/",
                headers=headers,
                json={"role": r},
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to update member role: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_workspace_member_role_updated", space_id=space_id, user_id=user_id, role=r)
        return {"success": True, "space_id": space_id, "user_id": user_id, "role": r}

    async def update_workspace(
        self,
        space_id: int,
        name: str | None = None,
        privacy: str | None = None,
        url_label: str | None = None,
    ) -> dict[str, Any]:
        """Update a Podio workspace's name, privacy setting, or URL label."""
        if name is None and privacy is None and url_label is None:
            raise ValueError("At least one of 'name', 'privacy', or 'url_label' must be provided.")
        if privacy is not None:
            p = privacy.strip().lower()
            if p not in self._VALID_PRIVACY:
                raise ValueError(
                    f"privacy must be one of {sorted(self._VALID_PRIVACY)}; got '{privacy}'."
                )
            privacy = p
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if privacy is not None:
            body["privacy"] = privacy
        if url_label is not None:
            body["url_label"] = url_label
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                f"{api}/space/{int(space_id)}/",
                headers=headers,
                json=body,
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to update workspace: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json() if resp.content else {}
        logger.info("podio_rest_workspace_updated", space_id=space_id, updated_keys=list(body.keys()))
        return {"space_id": space_id, **(data or {})}

    async def archive_workspace(self, space_id: int) -> dict[str, Any]:
        """Archive a Podio workspace. Reversible — call restore_workspace to undo."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{api}/space/{int(space_id)}/archive/", headers=headers)
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to archive workspace: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_workspace_archived", space_id=space_id)
        return {"success": True, "space_id": space_id, "status": "archived"}

    async def restore_workspace(self, space_id: int) -> dict[str, Any]:
        """Restore a previously archived Podio workspace."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{api}/space/{int(space_id)}/restore/", headers=headers)
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to restore workspace: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_workspace_restored", space_id=space_id)
        return {"success": True, "space_id": space_id, "status": "active"}

    async def delete_workspace(self, space_id: int) -> dict[str, Any]:
        """Permanently delete a Podio workspace. This action cannot be undone."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(f"{api}/space/{int(space_id)}/", headers=headers)
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to delete workspace: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_workspace_deleted", space_id=space_id)
        return {"success": True, "deleted": True, "space_id": space_id}

    # ── Webhook / hook operations ──────────────────────────────────────────────

    _VALID_HOOK_REF_TYPES = {"app", "space"}

    @staticmethod
    def _validate_webhook_url(url: str) -> None:
        """Raise ValueError if the URL uses a port other than 80 or 443."""
        parsed = urllib.parse.urlparse(url)
        port = parsed.port  # None when no explicit port in URL
        if port is not None and port not in (80, 443):
            raise ValueError(
                f"Webhook URLs must use port 80 or 443 only; got port {port}. "
                "Remove the explicit port or use :443 (https) or :80 (http)."
            )

    async def delete_webhook(self, hook_id: int) -> dict[str, Any]:
        """Permanently delete a Podio webhook by its ID."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(f"{api}/hook/{int(hook_id)}/", headers=headers)
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to delete webhook: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_webhook_deleted", hook_id=hook_id)
        return {"success": True, "deleted": True, "hook_id": hook_id}

    async def request_webhook_verification(self, hook_id: int) -> dict[str, Any]:
        """Step 1 of 2: ask Podio to POST a verification code to the webhook's callback URL."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api}/hook/{int(hook_id)}/verify/request/",
                headers=headers,
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to request verification: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_webhook_verification_requested", hook_id=hook_id)
        return {
            "hook_id": hook_id,
            "step": "1/2",
            "status": "verification_requested",
            "next": (
                "Podio has sent a verification code to your callback URL. "
                "Retrieve the code from your endpoint and call "
                "validate_webhook_verification(hook_id, code) to activate the webhook."
            ),
        }

    async def validate_webhook_verification(self, hook_id: int, code: str) -> dict[str, Any]:
        """Step 2 of 2: submit the received code to activate the webhook."""
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api}/hook/{int(hook_id)}/verify/validate/",
                headers=headers,
                json={"code": str(code)},
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to validate webhook: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_webhook_verified", hook_id=hook_id)
        return {
            "hook_id": hook_id,
            "step": "2/2",
            "status": "active",
            "next": f"Webhook {hook_id} is now active and will receive events.",
        }

    async def create_webhook(
        self, ref_type: str, ref_id: int, url: str, event_type: str
    ) -> dict[str, Any]:
        """Register a new webhook on a Podio app or workspace."""
        rt = (ref_type or "").strip().lower()
        if rt not in self._VALID_HOOK_REF_TYPES:
            raise ValueError(
                f"ref_type must be one of {sorted(self._VALID_HOOK_REF_TYPES)}; got '{ref_type}'."
            )
        self._validate_webhook_url(url)

        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api}/hook/{rt}/{int(ref_id)}/",
                headers=headers,
                json={"url": url, "type": event_type},
            )
            if resp.status_code not in (200, 201):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to create webhook: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json()
        hook_id = data.get("hook_id")
        logger.info("podio_rest_webhook_created", hook_id=hook_id, ref_type=rt, ref_id=ref_id, event_type=event_type)
        return {"hook_id": hook_id, "url": url, "event_type": event_type, "ref_type": rt, "ref_id": ref_id}

    async def list_webhooks(self, ref_type: str, ref_id: int) -> list[dict[str, Any]]:
        """Return all webhooks registered on a Podio app or workspace."""
        rt = (ref_type or "").strip().lower()
        if rt not in self._VALID_HOOK_REF_TYPES:
            raise ValueError(
                f"ref_type must be one of {sorted(self._VALID_HOOK_REF_TYPES)}; got '{ref_type}'."
            )
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/hook/{rt}/{int(ref_id)}/", headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to list webhooks: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: list[dict[str, Any]] = resp.json()
        logger.info("podio_rest_webhooks_listed", ref_type=rt, ref_id=ref_id, count=len(raw))
        return [
            {
                "hook_id": h.get("hook_id"),
                "url": h.get("url"),
                "event_type": h.get("type"),
                "status": h.get("status"),
            }
            for h in (raw if isinstance(raw, list) else [])
        ]

    async def get_flow_effect_attributes(self, app_id: int, effect_type: str) -> list[dict[str, Any]]:
        """Return the required attribute IDs for configuring a specific flow effect type.

        ``effect_type`` is a dot-namespaced string, e.g. 'task.create', 'item.update',
        'comment.create'. The result lists each attribute with its ID, label, type, and
        whether it is required — use this before building an effect's 'attributes'/'values' dict.
        """
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api}/flow/effect/{effect_type}/attribute/app/{int(app_id)}/",
                headers=headers,
            )
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch effect attributes: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: list[dict[str, Any]] = resp.json()
        logger.info(
            "podio_rest_flow_effect_attributes_fetched",
            app_id=app_id,
            effect_type=effect_type,
            count=len(raw),
        )
        return [
            {
                "attribute_id": a.get("id") or a.get("attribute_id"),
                "label": a.get("label") or a.get("name"),
                "type": a.get("type"),
                "required": bool(a.get("required", False)),
            }
            for a in (raw if isinstance(raw, list) else [])
        ]

    async def get_flow_possible_attributes(
        self, app_id: int, effect_type: str, attribute_id: str
    ) -> list[dict[str, Any]]:
        """Return the dynamic expressions available for a specific effect attribute.

        Each entry represents an injectable expression (e.g. {{item.creator}},
        a field value) that can be used as the value of that attribute.
        """
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api}/flow/effect/{effect_type}/attribute/{attribute_id}/app/{int(app_id)}/",
                headers=headers,
            )
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch possible attributes: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: list[dict[str, Any]] = resp.json()
        logger.info(
            "podio_rest_flow_possible_attributes_fetched",
            app_id=app_id,
            effect_type=effect_type,
            attribute_id=attribute_id,
            count=len(raw),
        )
        return [
            {
                "expression": a.get("value") or a.get("expression"),
                "label": a.get("label") or a.get("name"),
                "type": a.get("type"),
            }
            for a in (raw if isinstance(raw, list) else [])
        ]

    async def delete_flow(self, flow_id: int) -> dict[str, Any]:
        """Permanently delete a Podio workflow. This action cannot be undone."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(f"{api}/flow/{int(flow_id)}/", headers=headers)
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to delete flow: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_flow_deleted", flow_id=flow_id)
        return {"success": True, "deleted": True, "flow_id": flow_id}

    async def update_flow(
        self,
        flow_id: int,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        effects: list[dict[str, Any]] | None = None,
        trigger_type: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing Podio flow's name, trigger config, or effects.

        ⚠️ Podio's PUT /flow/{id}/ is a FULL REPLACE: it REQUIRES ``name`` (400
        "missing required properties: ['name']" without it) and DROPS any config/
        effects you omit (a name-only PUT wipes the field-trigger filter). So we
        fetch the current flow and MERGE — only the parts you pass are changed.

        Effects on update use the ``attributes`` array (same as create) — the old
        "use 'values'" guidance was wrong and makes PUT 500. ``_normalise_flow_effects``
        accepts either key. The trigger type (cause) cannot be changed — pass a
        differing ``trigger_type`` and this raises, telling you to delete + recreate.
        """
        if name is None and config is None and effects is None:
            raise ValueError("At least one of 'name', 'config', or 'effects' must be provided.")

        current = await self.get_flow(flow_id)

        if trigger_type is not None and current.get("trigger_type") != trigger_type:
            raise ValueError(
                f"A flow's trigger type cannot be changed after creation "
                f"(current: '{current.get('trigger_type')}', requested: '{trigger_type}'). "
                "Delete this flow and create a new one with the desired trigger type."
            )

        # Merge with the current flow so unspecified parts are preserved (PUT replaces).
        final_name = name if name is not None else current.get("name")
        final_effects = (
            self._normalise_flow_effects(effects)
            if effects is not None
            else self._normalise_flow_effects(current.get("effects") or [])
        )
        if config is not None:
            final_config = dict(config)
            if final_config.get("field_ids"):
                app_id = current.get("app_id")
                if app_id:
                    final_config["field_ids"] = await self._resolve_flow_field_ids(
                        app_id, final_config["field_ids"]
                    )
        else:
            final_config = current.get("config")

        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        body: dict[str, Any] = {"name": final_name}
        if final_config is not None:
            body["config"] = final_config
        if final_effects:
            body["effects"] = final_effects

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(f"{api}/flow/{int(flow_id)}/", headers=headers, json=body)
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to update flow: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json() if resp.content else {}
        logger.info(
            "podio_rest_flow_updated",
            flow_id=flow_id,
            changed=[k for k, v in (("name", name), ("config", config), ("effects", effects)) if v is not None],
        )
        return {"flow_id": flow_id, "updated": True, **(data or {})}

    async def _resolve_flow_field_ids(self, app_id: int, refs: list[Any]) -> list[int]:
        """Map each flow-trigger field reference to a real numeric field_id.

        Accepts numeric field_ids, external_id strings, or field labels. Validates
        every reference against the app's live schema so a hallucinated/wrong field_id
        fails fast with the valid options instead of Podio's opaque 404 "Object not found".
        """
        app = await self.get_app(int(app_id))
        raw = app.get("data") if isinstance(app, dict) and "data" in app else app
        fields = (raw or {}).get("fields") or [] if isinstance(raw, dict) else []
        by_id: set[int] = set()
        lookup: dict[str, int] = {}
        for f in fields:
            fid = f.get("field_id")
            if fid is None:
                continue
            by_id.add(int(fid))
            ext = f.get("external_id")
            if ext:
                lookup[str(ext).strip().lower()] = int(fid)
            label = (f.get("config") or {}).get("label")
            if label:
                lookup[str(label).strip().lower()] = int(fid)

        resolved: list[int] = []
        for ref in refs:
            try:
                n = int(ref)
                if n in by_id:
                    resolved.append(n)
                    continue
            except (TypeError, ValueError):
                pass
            key = str(ref).strip().lower()
            if key in lookup:
                resolved.append(lookup[key])
                continue
            valid = ", ".join(
                f"{(f.get('config') or {}).get('label')} (field_id={f.get('field_id')}, "
                f"external_id={f.get('external_id')})"
                for f in fields
            ) or "(app returned no fields)"
            raise RuntimeError(
                f"Flow trigger field '{ref}' does not exist on app {app_id}. "
                f"Pass a valid numeric field_id, external_id, or label. Available fields: {valid}"
            )
        return resolved

    @staticmethod
    def _normalise_flow_effects(effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalise flow effects to the shape Podio requires for BOTH create and update.

        Podio wants each effect's payload under the key ``attributes`` as an ARRAY of
        ``{attribute_id, value}`` objects with STRING values, for create (POST) AND
        update (PUT). NOTE: the old "use 'values' on update" guidance was WRONG — a
        ``values`` key (array or dict) makes PUT /flow/{id}/ 500. This accepts attributes
        given as a dict, a list, or under the legacy ``values`` key; strips each element
        to just ``{attribute_id, value}`` (drops Podio's echoed label/effect_id/null
        values); and coerces every value to a string ("Invalid value N (integer): must
        be string"). Field-update effects are rejected up front (unsupported by Podio).
        Official attribute_id strings: comment.create→"comment.value",
        status.create→"status.value", task.create→"task.text"/"task.due"/"task.responsible".
        """
        def _extract_comment_text(attrs: Any) -> str:
            if isinstance(attrs, dict):
                for key in ("text", "value", "content", "comment"):
                    if attrs.get(key):
                        return str(attrs[key])
                return str(next(iter(attrs.values()), ""))
            if isinstance(attrs, list):
                for elem in attrs:
                    if isinstance(elem, dict):
                        v = elem.get("value") or next(
                            (elem[k] for k in elem if k != "attribute_id"), None
                        )
                        if v:
                            return str(v)
            return str(attrs) if attrs else ""

        out: list[dict[str, Any]] = []
        for i, effect in enumerate(effects):
            if "type" not in effect:
                raise ValueError(f"Effect at index {i} is missing the required 'type' key.")
            # Accept the payload under 'attributes' OR the legacy 'values' key.
            raw_attrs = effect.get("attributes")
            if raw_attrs is None:
                raw_attrs = effect.get("values")
            if raw_attrs is None:
                raise ValueError(f"Effect at index {i} is missing the 'attributes' key.")
            effect_type_str = effect.get("type", "")

            # Field-update effects are NOT supported by Podio's basic flow API — only
            # task.create, comment.create, status.create work (Podio: "Unknown attribute
            # item.field.X"). Field updates require GlobiFlow. Fail fast with a clear message.
            _attr_ids: list[str] = []
            if isinstance(raw_attrs, dict):
                _attr_ids = [str(k) for k in raw_attrs.keys()]
            elif isinstance(raw_attrs, list):
                _attr_ids = [
                    str(e.get("attribute_id"))
                    for e in raw_attrs
                    if isinstance(e, dict) and e.get("attribute_id")
                ]
            if effect_type_str in ("item.update", "item.field.update") or any(
                a.startswith("item.field") for a in _attr_ids
            ):
                raise RuntimeError(
                    "Updating a field value is not a supported automation effect in Podio's "
                    "flow API (Podio returns 'Unknown attribute'). Supported flow effects are: "
                    "create a task (task.create), add a comment (comment.create), or post a "
                    "status update (status.create). To auto-change a field value, set it up in "
                    "Podio's GlobiFlow / Workflow Automation manually inside Podio."
                )

            if effect_type_str == "comment.create":
                arr: list[dict[str, Any]] = [
                    {"attribute_id": "comment.value", "value": _extract_comment_text(raw_attrs)}
                ]
            elif isinstance(raw_attrs, dict):
                arr = [{"attribute_id": k, "value": v} for k, v in raw_attrs.items()]
            elif isinstance(raw_attrs, list):
                arr = []
                for elem in raw_attrs:
                    if isinstance(elem, dict) and "attribute_id" in elem:
                        arr.append({"attribute_id": elem["attribute_id"], "value": elem.get("value")})
                    elif isinstance(elem, dict):
                        for k, v in elem.items():
                            arr.append({"attribute_id": k, "value": v})
                    else:
                        arr.append(elem)
            else:
                arr = raw_attrs

            # Drop null/empty values (Podio echoes task.responsible/description as null),
            # then stringify every remaining value.
            if isinstance(arr, list):
                cleaned: list[Any] = []
                for elem in arr:
                    if not isinstance(elem, dict):
                        cleaned.append(elem)
                        continue
                    val = elem.get("value")
                    if val is None or val == "":
                        continue
                    cleaned.append({"attribute_id": elem.get("attribute_id"), "value": str(val)})
                arr = cleaned

            # Re-emit a clean effect: keep 'type', drop 'values'/echoed 'effect_id'/'config'.
            clean_effect = {
                k: v for k, v in effect.items()
                if k not in ("values", "attributes", "effect_id", "config")
            }
            out.append({**clean_effect, "type": effect_type_str, "attributes": arr})
        return out

    async def create_flow(
        self,
        app_id: int,
        trigger_type: str,
        name: str,
        effects: list[dict[str, Any]],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new automated workflow on a Podio app.

        ``trigger_type`` must be 'item.create', 'item.update', or 'item.delete'.
        ``config`` is optional but required for filtered item.update triggers:
          pass ``{"field_ids": [<numeric_field_id>, ...]}`` to fire only when
          specific fields change.  Numeric field IDs come from ``get_app`` →
          field.field_id (NOT the external_id string).
        ``ref_type`` is always 'app' — flows cannot be attached to spaces.
        Each effect must have a 'type' key and an 'attributes' dict.
        Returns the new flow_id on success.
        """
        _VALID_TRIGGERS = {"item.create", "item.update", "item.delete"}
        if trigger_type not in _VALID_TRIGGERS:
            raise ValueError(
                f"trigger_type must be one of {sorted(_VALID_TRIGGERS)}; got '{trigger_type}'."
            )
        if not effects:
            raise ValueError("At least one effect must be provided.")

        normalised_effects = self._normalise_flow_effects(effects)

        # Resolve/validate a filtered item.update trigger's field_ids against the
        # LIVE app schema. Models routinely hallucinate a numeric field_id (e.g.
        # 45577653 for Category whose real field_id is 223289103) → Podio returns a
        # cryptic 404 "Object not found". Accept a numeric field_id, an external_id,
        # or a field label and map every one to the real numeric field_id; fail fast
        # with the valid options if a reference cannot be resolved.
        if config and config.get("field_ids"):
            config = {**config, "field_ids": await self._resolve_flow_field_ids(app_id, config["field_ids"])}

        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        body: dict[str, Any] = {
            "type": trigger_type,
            "name": name,
            "ref_type": "app",
            "ref_id": int(app_id),
            "effects": normalised_effects,
        }
        if config:
            body["config"] = config
        async with httpx.AsyncClient(timeout=30.0) as client:
            # POST /flow/app/{id}/ is the correct create endpoint;
            # POST /flow/ (root) returns 404 on Podio's API.
            resp = await client.post(f"{api}/flow/app/{int(app_id)}/", headers=headers, json=body)
            if resp.status_code not in (200, 201):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to create flow: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json()
        flow_id = data.get("flow_id")
        logger.info("podio_rest_flow_created", app_id=app_id, flow_id=flow_id, trigger_type=trigger_type)
        return {"flow_id": flow_id, "name": name, "trigger_type": trigger_type, "active": data.get("active", True)}

    async def get_flow(self, flow_id: int) -> dict[str, Any]:
        """Fetch the full definition of a Podio flow including trigger config and effects."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/flow/{int(flow_id)}/", headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch flow: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: dict[str, Any] = resp.json()
        ref = raw.get("ref") if isinstance(raw.get("ref"), dict) else {}
        return {
            "flow_id": raw.get("flow_id"),
            "name": raw.get("name"),
            "trigger_type": raw.get("type"),
            # Podio omits 'active' on flow objects; a created flow is live. See get_app_flows.
            "active": raw.get("active", True),
            "config": raw.get("config"),
            "effects": raw.get("effects") or [],
            # The app this flow is attached to (ref.type == "app") — needed to resolve
            # field_ids when updating the flow.
            "app_id": ref.get("id"),
        }

    async def get_flow_attributes(self, flow_id: int) -> list[dict[str, Any]]:
        """Return all variable attributes available for injection in a flow's effect expressions."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/flow/{int(flow_id)}/attribute/", headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch flow attributes: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: list[dict[str, Any]] = resp.json()
        logger.info("podio_rest_flow_attributes_fetched", flow_id=flow_id, count=len(raw))
        return [
            {
                "name": a.get("name"),
                "label": a.get("label") or a.get("description"),
                "type": a.get("type"),
            }
            for a in (raw if isinstance(raw, list) else [])
        ]

    async def get_app_flows(self, app_id: int) -> list[dict[str, Any]]:
        """Return all automated workflows (flows) configured on a Podio app."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/flow/app/{int(app_id)}/", headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch app flows: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: list[dict[str, Any]] = resp.json()
        logger.info("podio_rest_app_flows_fetched", app_id=app_id, count=len(raw))
        return [
            {
                "flow_id": f.get("flow_id"),
                "name": f.get("name"),
                "trigger_type": f.get("type"),
                # Podio's flow object omits an 'active' key entirely — a flow is live the
                # moment it is created (no activation endpoint exists). Absence ≠ inactive,
                # so default to True to avoid falsely reporting a working flow as disabled.
                "active": f.get("active", True),
            }
            for f in (raw if isinstance(raw, list) else [])
        ]

    # ── Task aggregation ───────────────────────────────────────────────────────

    async def get_task_summary(self) -> dict[str, Any]:
        """Return aggregated task statistics across the user's workspaces.

        Calls GET /task/summary/ and normalises the result to
        {total, completed, overdue}, deriving totals from Podio's
        category buckets when a single 'total' key is absent.
        """
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/task/summary/", headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch task summary: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: dict[str, Any] = resp.json()

        def _count(key: str) -> int:
            bucket = raw.get(key) or {}
            return int(bucket.get("count", 0)) if isinstance(bucket, dict) else int(bucket or 0)

        overdue = _count("overdue")
        completed = _count("completed")
        # Podio returns category buckets (overdue/today/upcoming/later/completed).
        # Sum them for a total when no top-level total is provided.
        total = int(raw.get("total", 0)) or sum(
            _count(k) for k in ("overdue", "today", "upcoming", "later", "completed", "other")
        )
        return {"total": total, "completed": completed, "overdue": overdue, "raw": raw}

    async def get_task_count(
        self,
        space_id: int | None = None,
    ) -> dict[str, Any]:
        """Return the number of active (incomplete) tasks for the authenticated user.

        Calls GET /task/total/ which returns category buckets (overdue, today, tomorrow,
        upcoming, later) split by own vs reassigned. Sums across all buckets.
        Accepts optional ``space`` query param (single space_id) to scope to one workspace.

        Note: the Podio /task/total/ endpoint does NOT accept completed/app_id filters.
        Use get_task_summary for a breakdown by category.
        """
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        params: dict[str, str] = {}
        if space_id is not None:
            params["space"] = str(int(space_id))
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api}/task/total/",
                headers=headers,
                params=params or None,
            )
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch task count: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw = resp.json()

        def _sum_section(section: dict) -> int:
            if not isinstance(section, dict):
                return 0
            return sum(int(v) for v in section.values() if isinstance(v, int))

        own_total = _sum_section(raw.get("own") or {})
        reassigned_total = _sum_section(raw.get("reassigned") or {})
        total = own_total + reassigned_total
        logger.info("podio_rest_task_count_fetched", total=total, own=own_total, reassigned=reassigned_total)
        return {
            "count": total,
            "own": own_total,
            "reassigned": reassigned_total,
            "breakdown": raw,
        }

    async def remove_task_reference(self, task_id: int) -> dict[str, Any]:
        """Remove the reference link from a Podio task, making it a standalone task."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(f"{api}/task/{int(task_id)}/ref/", headers=headers)
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to remove task reference: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_task_reference_removed", task_id=task_id)
        return {"success": True, "task_id": task_id, "ref_type": None, "ref_id": None}

    async def get_reference_tasks(self, ref_type: str, ref_id: int) -> list[dict[str, Any]]:
        """Return all tasks linked to a specific Podio object.

        ``ref_type`` is one of: item, app, space, status.
        ``ref_id`` is that object's numeric ID.
        """
        _VALID_REF_TYPES = {"item", "app", "space", "status"}
        rt = (ref_type or "").strip().lower()
        if rt not in _VALID_REF_TYPES:
            raise ValueError(f"ref_type must be one of {sorted(_VALID_REF_TYPES)}; got '{ref_type}'.")
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/task/{rt}/{int(ref_id)}/", headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch reference tasks: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: list[dict[str, Any]] = resp.json()
        tasks = [self._normalise_task(t) for t in (raw if isinstance(raw, list) else [])]
        logger.info("podio_rest_reference_tasks_fetched", ref_type=rt, ref_id=ref_id, count=len(tasks))
        return tasks

    async def update_item_field(
        self, item_id: int, field_id: str | int, value: Any
    ) -> dict[str, Any]:
        """Update a single field on a Podio item without touching any other fields.

        ``field_id`` may be a numeric field_id or an external_id string.
        ``value`` must match the field type (text→str, category→option id,
        phone/email→[{type, value}], relationship→item_id int, image→[file_id]).
        """
        api = await self._cfg("podio_rest_api_base")
        headers = {**(await self._auth_headers()), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                f"{api}/item/{int(item_id)}/value/{field_id}",
                headers=headers,
                json=value,
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Field update failed: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json() if resp.content else {}
        logger.info("podio_rest_item_field_updated", item_id=item_id, field_id=field_id)
        return {"item_id": item_id, "field_id": field_id, "updated": True, **(data or {})}

    async def get_item_references(self, item_id: int) -> list[dict[str, Any]]:
        """Return all Podio records that link to (reference) the given item."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/item/{int(item_id)}/reference/", headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch references: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: list[dict[str, Any]] = resp.json()
        logger.info("podio_rest_item_references_fetched", item_id=item_id, count=len(raw))
        return [
            {
                "item_id": r.get("id"),
                "title": r.get("title"),
                "app_id": (r.get("app") or {}).get("app_id"),
                "app_name": (r.get("app") or {}).get("name"),
            }
            for r in (raw if isinstance(raw, list) else [])
        ]

    async def revert_item_revision(self, item_id: int, revision_id: int) -> dict[str, Any]:
        """Revert a Podio item to the state before the specified revision.

        Calls DELETE /item/{item_id}/revision/{revision_id}/ which removes that revision
        and rolls the item back to its previous state. Fetches and returns the item's
        current state after the revert so the caller can confirm what it now looks like.
        """
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(
                f"{api}/item/{int(item_id)}/revision/{int(revision_id)}/",
                headers=headers,
            )
            if resp.status_code not in (200, 204):
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Revert failed: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        logger.info("podio_rest_item_revision_reverted", item_id=item_id, revision_id=revision_id)
        return await self.get_item(item_id)

    async def get_item_revisions(self, item_id: int) -> list[dict[str, Any]]:
        """Return the full revision history for a Podio item."""
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{api}/item/{int(item_id)}/revision/", headers=headers)
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch revisions: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            raw: list[dict[str, Any]] = resp.json()
        logger.info("podio_rest_item_revisions_fetched", item_id=item_id, count=len(raw))
        return [
            {
                "revision_id": r.get("revision"),
                "created_by": (r.get("created_by") or {}).get("name"),
                "created_on": r.get("created_on"),
            }
            for r in (raw if isinstance(raw, list) else [])
        ]

    async def get_items_by_view(
        self, app_id: int, view_id: int, limit: int = 30, offset: int = 0
    ) -> dict[str, Any]:
        """Filter items in a Podio app using a saved view's filter conditions.

        Step 1 — fetch the view to get its filter, sort_by, and sort_desc.
        Step 2 — POST those conditions to /item/app/{app_id}/filter/ and return
        the result verbatim (same shape as the hosted MCP's get_items response).
        """
        api = await self._cfg("podio_rest_api_base")
        auth = await self._auth_headers()
        json_headers = {**auth, "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            view_resp = await client.get(f"{api}/view/{int(view_id)}/", headers=auth)
            if view_resp.status_code != 200:
                detail = view_resp.text[:400]
                raise RuntimeError(
                    f"Failed to fetch view: HTTP {view_resp.status_code} {view_resp.reason_phrase}"
                    f" — {detail}"
                )
            view = view_resp.json()

        body: dict[str, Any] = {"limit": limit, "offset": offset}
        if view.get("filter"):
            body["filters"] = view["filter"]
        if view.get("sort_by"):
            body["sort_by"] = view["sort_by"]
        if "sort_desc" in view:
            body["sort_desc"] = view["sort_desc"]

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{api}/item/app/{int(app_id)}/filter/",
                headers=json_headers,
                json=body,
            )
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Filter request failed: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
            data = resp.json()

        logger.info(
            "podio_rest_items_by_view",
            app_id=app_id,
            view_id=view_id,
            view_name=view.get("name"),
            total=data.get("total"),
        )
        data["view_name"] = view.get("name")
        return data

    async def export_app_xlsx(self, app_id: int) -> tuple[str, bytes]:
        """Export all records from a Podio app as an Excel (.xlsx) file.

        Returns (filename, bytes). The filename is taken from the Content-Disposition
        header when present, otherwise defaults to ``app_{app_id}_export.xlsx``.
        """
        api = await self._cfg("podio_rest_api_base")
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(
                f"{api}/item/app/{int(app_id)}/xlsx/",
                headers=headers,
            )
            if resp.status_code != 200:
                detail = resp.text[:400]
                raise RuntimeError(
                    f"Export failed: HTTP {resp.status_code} {resp.reason_phrase}"
                    f" — {detail}"
                )
        filename = f"app_{app_id}_export.xlsx"
        cd = resp.headers.get("content-disposition", "")
        if "filename=" in cd:
            try:
                filename = cd.split("filename=")[-1].strip().strip('"')
            except Exception:  # noqa: BLE001
                pass
        logger.info("podio_rest_app_exported", app_id=app_id, filename=filename, size=len(resp.content))
        return filename, resp.content


podio_rest = PodioREST()
