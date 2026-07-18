"""MyCase REST API client — read (GET) operations only.

MyCase's production API lives on a separate host from its docs
(``external-integrations.mycase.com``), authenticated with a standard OAuth2 bearer
access token (``Authorization: Bearer <token>``). Docs are at
https://mycaseapi.stoplight.io/docs/mycase-api-documentation/.

Auth: OAuth 2.0 Authorization Code grant, confirmed from MyCase's own "Getting
Started" doc (Session 24):
  1. Browser redirect: GET https://auth.mycase.com/login_sessions/new
     ?client_id=...&redirect_uri=...&response_type=code&state=...
  2. Token exchange: POST https://auth.mycase.com/tokens with a JSON body
     {client_id, client_secret, code, grant_type:"authorization_code", redirect_uri}
  3. Refresh: POST https://auth.mycase.com/tokens with a JSON body
     {client_id, client_secret, refresh_token, grant_type:"refresh_token"}
Token response: {access_token, token_type:"Bearer", scope, refresh_token,
expires_in (86400 = 24h), firm_uuid}. Refresh tokens are valid 2 weeks. Rate limit:
25 requests/second per client. The redirect_uri must exactly match what MyCase
support registered for the client — it cannot be changed client-side.

The simplest path if you already have a token in hand: paste it directly into
Settings → MyCase (``mycase_access_token``) — no browser flow needed, though it
won't auto-refresh once it expires (24h) without also setting a refresh token via
the real "Connect MyCase" flow.

Every list endpoint shares the same conventions (confirmed from live docs export):
  - ``filter[updated_after]`` (ISO 8601) — incremental sync
  - ``page_size`` (1-1000, default 25) + cursor pagination via a ``Link`` response
    header (``page_token`` on the next request) and an ``Item-Count`` header
  - related objects are returned as ``{"id": N}`` by default (sparse fieldsets via
    ``field[resource]=a,b,c`` exist on a few endpoints, e.g. Cases)
"""
from __future__ import annotations

import asyncio
import re
import time
import urllib.parse
from typing import Any

import httpx
import structlog

from app.services.settings_service import get_setting, upsert_setting

logger = structlog.get_logger(__name__)

_API_BASE = "https://external-integrations.mycase.com/v1"

DEFAULTS = {
    "mycase_authorize_url": "https://auth.mycase.com/login_sessions/new",
    "mycase_token_url": "https://auth.mycase.com/tokens",
    "mycase_redirect_uri": "http://localhost:8000/integrations/mycase/callback",
}

_state_store: dict[str, float] = {}
_STATE_TTL = 600
_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class MyCaseREST:
    async def _cfg(self, key: str) -> str:
        return (await get_setting(key)) or DEFAULTS.get(key, "")

    # ── OAuth (authorization_code) — best-effort; direct token paste also works ──

    async def build_authorize_url(self) -> str:
        client_id = await get_setting("mycase_client_id")
        if not client_id:
            raise ValueError("MyCase Client ID is not configured (Settings - MyCase).")
        authorize = await self._cfg("mycase_authorize_url")
        redirect = await self._cfg("mycase_redirect_uri")
        import secrets

        state = secrets.token_urlsafe(24)
        now = time.time()
        for st in [s for s, ts in _state_store.items() if now - ts > _STATE_TTL]:
            _state_store.pop(st, None)
        _state_store[state] = now

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

        client_id = await get_setting("mycase_client_id")
        client_secret = await get_setting("mycase_client_secret")
        token_url = await self._cfg("mycase_token_url")
        redirect = await self._cfg("mycase_redirect_uri")

        body = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(token_url, json=body)
            resp.raise_for_status()
            tok = resp.json()
        await self._store_token(tok)
        logger.info("mycase_authorized", firm_uuid=tok.get("firm_uuid"))
        return tok

    async def _store_token(self, tok: dict[str, Any]) -> None:
        if tok.get("access_token"):
            await upsert_setting("mycase_access_token", tok["access_token"])
        if tok.get("refresh_token"):
            await upsert_setting("mycase_refresh_token", tok["refresh_token"])
        if tok.get("firm_uuid"):
            await upsert_setting("mycase_firm_uuid", tok["firm_uuid"])
        expires_in = tok.get("expires_in")
        if expires_in:
            expiry = int(time.time()) + int(expires_in) - 60
            await upsert_setting("mycase_token_expiry", str(expiry))

    async def _refresh(self) -> str | None:
        refresh = await get_setting("mycase_refresh_token")
        if not refresh:
            return None
        client_id = await get_setting("mycase_client_id")
        client_secret = await get_setting("mycase_client_secret")
        token_url = await self._cfg("mycase_token_url")
        body = {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(token_url, json=body)
                if resp.status_code != 200:
                    logger.warning("mycase_refresh_failed", status=resp.status_code, body=resp.text[:300])
                    return None
                tok = resp.json()
            await self._store_token(tok)
            return tok.get("access_token")
        except Exception as exc:  # noqa: BLE001
            logger.warning("mycase_refresh_error", error=str(exc))
            return None

    async def _get_valid_token(self) -> str | None:
        token = await get_setting("mycase_access_token")
        refresh = await get_setting("mycase_refresh_token")
        expiry = await get_setting("mycase_token_expiry")
        if token and expiry:
            try:
                if int(expiry) > int(time.time()):
                    return token
            except ValueError:
                pass
        elif token and not refresh:
            return token  # manually-pasted token, no refresh token available — nothing else we can do
        # Either the token is expired, or its expiry is unknown (e.g. an access_token +
        # refresh_token pasted straight from Settings, with no accompanying expires_in) —
        # in either case, if a refresh_token exists, use it now so we get both a fresh
        # token AND a real stored expiry going forward, instead of trusting an
        # unverified token indefinitely.
        refreshed = await self._refresh()
        return refreshed or token

    async def is_connected(self) -> bool:
        return bool(await get_setting("mycase_access_token"))

    async def disconnect(self) -> None:
        for key in ("mycase_access_token", "mycase_refresh_token", "mycase_token_expiry"):
            await upsert_setting(key, None)
        logger.info("mycase_disconnected")

    # ── HTTP core ────────────────────────────────────────────────────────────────

    async def _headers(self) -> dict[str, str]:
        token = await self._get_valid_token()
        if not token:
            raise ValueError("Not connected to MyCase. Add an access token in Settings - MyCase.")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # Confirmed error code meanings (MyCase "Error Handling" doc, Session 25).
    _ERROR_CODE_TEXT = {
        400: "Bad Request - the request body format is incorrect",
        401: "Unauthorized - not authenticated; the access token may be expired, try reconnecting",
        403: "Forbidden - insufficient MyCase permissions for this action",
        404: "Not Found - the path or id was not found",
        422: "Unprocessable Entity - a field has invalid data or fails a business rule",
        429: "Too Many Requests - rate limited (25 requests/second per client)",
        500: "Internal Server Error - MyCase had a problem, try again later",
        503: "Service Unavailable - MyCase is down for scheduled maintenance",
    }

    @staticmethod
    def _extract_error_message(resp: httpx.Response) -> str:
        """Parse MyCase's structured error body: {"errors": [{"description", "source":
        {"pointer"|"parameter"}}]} — falls back to raw text if the body isn't that shape."""
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            return (resp.text or "").strip()[:300]
        errors = data.get("errors") if isinstance(data, dict) else None
        if isinstance(errors, list) and errors:
            parts = []
            for e in errors:
                if not isinstance(e, dict):
                    continue
                desc = e.get("description", "")
                source = e.get("source") or {}
                loc = source.get("pointer") or source.get("parameter")
                parts.append(f"{desc} (at {loc})" if loc else desc)
            joined = "; ".join(p for p in parts if p)
            if joined:
                return joined
        return (resp.text or "").strip()[:300]

    async def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        headers = await self._headers()
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        # Retries cover BOTH: rate limiting (429 — checked via status code after a response
        # comes back) AND transient network failures (httpx.TransportError — e.g. the
        # "All connection attempts failed" ConnectError class — which raises INSTEAD OF
        # returning a response, so it needs its own try/except; a bare status-code check
        # after the request call never runs if the request itself raised).
        _RETRY_DELAYS = [1, 2, 4]  # seconds between retries (rate limit is 25 req/s per client)
        resp: httpx.Response | None = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                logger.warning("mycase_retry", path=path, attempt=attempt, wait=delay)
                await asyncio.sleep(delay)
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.request(method, f"{_API_BASE}{path}", headers=headers, params=clean)
            except httpx.TransportError as exc:
                if attempt < len(_RETRY_DELAYS):
                    continue
                logger.error("mycase_connection_error", path=path, detail=str(exc))
                raise RuntimeError(f"Could not connect to MyCase's API after retries: {exc}") from exc
            if resp.status_code == 429 and attempt < len(_RETRY_DELAYS):
                continue
            break
        assert resp is not None
        if resp.status_code >= 400:
            message = self._extract_error_message(resp)
            logger.warning("mycase_api_error", path=path, status=resp.status_code, message=message)
            code_text = self._ERROR_CODE_TEXT.get(resp.status_code, "")
            detail = f"{message} ({code_text})" if code_text and message else (message or code_text or f"HTTP {resp.status_code}")
            raise RuntimeError(f"MyCase API {resp.status_code}: {detail}")
        return resp

    async def _get_one(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = await self._request("GET", path, params)
        return resp.json()

    async def _get_list(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return {"items": [...], "item_count": int|None, "next_page_token": str|None}."""
        resp = await self._request("GET", path, params)
        items = resp.json()
        item_count = resp.headers.get("Item-Count")
        next_token: str | None = None
        link = resp.headers.get("Link")
        if link:
            m = _LINK_NEXT_RE.search(link)
            if m:
                next_url = urllib.parse.urlparse(m.group(1))
                qs = urllib.parse.parse_qs(next_url.query)
                next_token = (qs.get("page_token") or [None])[0]
        # item_count/next_page_token BEFORE items deliberately — items can be up to
        # 1000 full records (way past _MAX_TOOL_OUTPUT_CHARS), and truncation in the
        # agent loop is a dumb string cut, so putting the huge array first would push
        # this pagination metadata out of what the model ever gets to see — leaving
        # it no reliable way to know the true total or whether more pages remain.
        return {
            "item_count": int(item_count) if item_count else None,
            "next_page_token": next_token,
            "items": items if isinstance(items, list) else [],
        }

    async def _download(self, path: str) -> dict[str, Any]:
        """Download endpoints 302-redirect to a temporary signed URL — surface it
        rather than following the redirect (bytes shouldn't transit the LLM)."""
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            resp = await client.get(f"{_API_BASE}{path}", headers=headers)
        if resp.status_code in (301, 302, 303, 307, 308):
            return {"success": True, "download_url": resp.headers.get("Location")}
        if resp.status_code >= 400:
            message = self._extract_error_message(resp)
            code_text = self._ERROR_CODE_TEXT.get(resp.status_code, "")
            detail = f"{message} ({code_text})" if code_text and message else (message or code_text or f"HTTP {resp.status_code}")
            raise RuntimeError(f"MyCase API {resp.status_code}: {detail}")
        return {"success": True, "download_url": None, "note": f"Unexpected status {resp.status_code}"}

    @staticmethod
    def _page(page_size: int | None, page_token: str | None, updated_after: str | None = None) -> dict[str, Any]:
        p: dict[str, Any] = {}
        if page_size is not None:
            p["page_size"] = page_size
        if page_token is not None:
            p["page_token"] = page_token
        if updated_after is not None:
            p["filter[updated_after]"] = updated_after
        return p

    # ── Calls ────────────────────────────────────────────────────────────────────

    async def get_calls(self, updated_after=None, page_size=None, page_token=None):
        return await self._get_list("/calls", self._page(page_size, page_token, updated_after))

    # ── Case Roles ───────────────────────────────────────────────────────────────

    async def get_case_roles(self, updated_after=None, page_size=None, page_token=None):
        return await self._get_list("/case_roles", self._page(page_size, page_token, updated_after))

    # ── Cases ────────────────────────────────────────────────────────────────────

    async def get_cases(
        self, status=None, updated_after=None, field_client=None, field_custom_field=None,
        page_size=None, page_token=None,
    ):
        params = self._page(page_size, page_token, updated_after)
        if status is not None:
            params["filter[status]"] = status
        if field_client is not None:
            params["field[client]"] = field_client
        if field_custom_field is not None:
            params["field[custom_field]"] = field_custom_field
        return await self._get_list("/cases", params)

    async def get_client_cases(self, client_id: int, page_size=None, page_token=None):
        return await self._get_list(f"/clients/{int(client_id)}/cases", self._page(page_size, page_token))

    async def get_case(self, case_id: int, field_client=None, field_custom_field=None):
        params: dict[str, Any] = {}
        if field_client is not None:
            params["field[client]"] = field_client
        if field_custom_field is not None:
            params["field[custom_field]"] = field_custom_field
        return await self._get_one(f"/cases/{int(case_id)}", params)

    async def get_case_folder(self, case_id: int):
        return await self._get_one(f"/cases/{int(case_id)}/folder")

    async def get_case_folder_tree(self, case_id: int, max_depth: int = 6, max_folders: int = 500) -> dict[str, Any]:
        """Recursively walk a case's ENTIRE folder tree — its root folder, every
        subfolder to max_depth, and the documents directly inside each — and return
        it flattened, one row per folder. MyCase has no single endpoint for a full
        tree; relying on an LLM to correctly chain get_folder_subfolders /
        get_folder_documents calls itself for an arbitrarily-nested structure is
        unreliable (a branch gets missed, or the step limit is hit first), the same
        reliability gap search_cases/aggregate_cases/find_cases_with_documents solve
        elsewhere. `path` is a human-readable breadcrumb (e.g. "Root/Discovery/2024").
        """
        root = await self.get_case_folder(case_id)
        root_id = root.get("id")
        if not isinstance(root_id, int):
            return {"success": False, "error": "Could not resolve the case's root folder id.", "items": []}

        folders: list[dict[str, Any]] = []
        truncated = False

        async def walk(folder_id: int, folder_name: str | None, depth: int, path: str) -> None:
            nonlocal truncated
            if len(folders) >= max_folders:
                truncated = True
                return

            docs: list[dict[str, Any]] = []
            page_token: str | None = None
            while True:
                page = await self.get_folder_documents(folder_id, page_token=page_token)
                docs.extend(page.get("items", []))
                page_token = page.get("next_page_token")
                if not page_token:
                    break

            folders.append({
                "folder_id": folder_id,
                "folder_name": folder_name,
                "path": path,
                "depth": depth,
                "document_count": len(docs),
                "documents": [d.get("name") or d.get("id") for d in docs],
            })

            if depth >= max_depth:
                return

            subfolders: list[dict[str, Any]] = []
            page_token = None
            while True:
                page = await self.get_folder_subfolders(folder_id, page_token=page_token)
                subfolders.extend(page.get("items", []))
                page_token = page.get("next_page_token")
                if not page_token:
                    break

            for sf in subfolders:
                sf_id = sf.get("id")
                if isinstance(sf_id, int):
                    sf_name = sf.get("name")
                    await walk(sf_id, sf_name, depth + 1, f"{path}/{sf_name or sf_id}")

        await walk(root_id, root.get("name"), 0, root.get("name") or str(root_id))

        return {
            "success": True,
            "case_id": case_id,
            "root_folder_id": root_id,
            "folder_count": len(folders),
            "total_documents": sum(f["document_count"] for f in folders),
            "truncated": truncated,
            "items": folders,
        }

    async def get_case_documents(self, case_id: int):
        return await self._get_list(f"/cases/{int(case_id)}/documents")

    async def get_case_notes(self, case_id: int):
        return await self._get_list(f"/cases/{int(case_id)}/notes")

    # ── Case Stages ──────────────────────────────────────────────────────────────

    async def get_case_stages(self, updated_after=None, page_size=None, page_token=None):
        return await self._get_list("/case_stages", self._page(page_size, page_token, updated_after))

    # ── Clients (People) ─────────────────────────────────────────────────────────

    async def get_clients(
        self, first_name=None, last_name=None, email=None, cell_phone_number=None,
        home_phone_number=None, work_phone_number=None, updated_after=None,
        page_size=None, page_token=None,
    ):
        params = self._page(page_size, page_token, updated_after)
        for key, val in (
            ("filter[first_name]", first_name), ("filter[last_name]", last_name),
            ("filter[email]", email), ("filter[cell_phone_number]", cell_phone_number),
            ("filter[home_phone_number]", home_phone_number),
            ("filter[work_phone_number]", work_phone_number),
        ):
            if val is not None:
                params[key] = val
        return await self._get_list("/clients", params)

    async def get_client(self, client_id: int):
        return await self._get_one(f"/clients/{int(client_id)}")

    async def get_client_notes(self, client_id: int):
        return await self._get_list(f"/clients/{int(client_id)}/notes")

    async def get_client_message_threads(self, client_id: int):
        return await self._get_list(f"/clients/{int(client_id)}/message_threads")

    # ── Companies ────────────────────────────────────────────────────────────────

    async def get_companies(
        self, name=None, email=None, main_phone_number=None, fax_phone_number=None,
        updated_after=None, page_size=None, page_token=None,
    ):
        params = self._page(page_size, page_token, updated_after)
        for key, val in (
            ("filter[name]", name), ("filter[email]", email),
            ("filter[main_phone_number]", main_phone_number),
            ("filter[fax_phone_number]", fax_phone_number),
        ):
            if val is not None:
                params[key] = val
        return await self._get_list("/companies", params)

    async def get_company(self, company_id: int):
        return await self._get_one(f"/companies/{int(company_id)}")

    # ── Custom Fields ────────────────────────────────────────────────────────────

    async def get_custom_fields(self, updated_after=None, page_size=None, page_token=None):
        return await self._get_list("/custom_fields", self._page(page_size, page_token, updated_after))

    async def get_custom_field(self, custom_field_id: int):
        return await self._get_one(f"/custom_fields/{int(custom_field_id)}")

    async def get_custom_field_list_options(self, custom_field_id: int):
        return await self._get_list(f"/custom_fields/{int(custom_field_id)}/list_options")

    # ── Documents ────────────────────────────────────────────────────────────────

    async def get_documents(self, updated_after=None, page_size=None, page_token=None):
        return await self._get_list("/documents", self._page(page_size, page_token, updated_after))

    async def get_document(self, document_id: int):
        return await self._get_one(f"/documents/{int(document_id)}")

    async def find_cases_with_documents(self, limit: int = 5, max_documents: int = 20000) -> dict[str, Any]:
        """Find cases that actually HAVE documents attached — MyCase has no server-
        side filter for this, so the naive approach (sample a few cases, hope some
        have documents) gives wrong/incomplete answers, the same class of problem
        aggregate_cases/search_cases solve elsewhere. Instead this walks every page
        of get_documents() firm-wide (each document's ``case`` field is ``{"id": N}``
        per MyCase's schema), tallies how many documents belong to each case, then
        fetches full case details for the ``limit`` cases with the MOST documents.
        """
        all_docs: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(all_docs) < max_documents:
            page = await self.get_documents(page_size=1000, page_token=page_token)
            all_docs.extend(page.get("items", []))
            page_token = page.get("next_page_token")
            if not page_token:
                break

        counts: dict[int, int] = {}
        for d in all_docs:
            case_ref = d.get("case") or {}
            cid = case_ref.get("id") if isinstance(case_ref, dict) else None
            if isinstance(cid, int):
                counts[cid] = counts.get(cid, 0) + 1

        ordered_ids = sorted(counts, key=lambda cid: counts[cid], reverse=True)[: max(0, limit)]

        cases: list[dict[str, Any]] = []
        for cid in ordered_ids:
            try:
                case = await self.get_case(cid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("find_cases_with_documents_case_fetch_failed", case_id=cid, error=str(exc))
                continue
            cases.append({**case, "document_count": counts[cid]})

        return {
            "success": True,
            "documents_scanned": len(all_docs),
            "documents_truncated": len(all_docs) >= max_documents,
            "distinct_cases_with_documents": len(counts),
            "match_count": len(cases),
            "items": cases,
        }

    async def get_document_versions_all(self, updated_after=None, page_size=None, page_token=None):
        """Firm-wide document versions list (Get all Firm Document Versions)."""
        return await self._get_list("/document_versions", self._page(page_size, page_token, updated_after))

    async def get_document_versions(self, document_id: int):
        """Versions of ONE document (Get All Versions of a Document)."""
        return await self._get_list(f"/documents/{int(document_id)}/versions")

    async def download_document(self, document_id: int):
        return await self._download(f"/documents/{int(document_id)}/data")

    async def download_document_version(self, document_id: int, version_number: int):
        return await self._download(f"/documents/{int(document_id)}/versions/{int(version_number)}/data")

    # ── Events (calendar) ────────────────────────────────────────────────────────

    async def get_events(self, updated_after=None, page_size=None, page_token=None):
        return await self._get_list("/events", self._page(page_size, page_token, updated_after))

    # ── Expenses ─────────────────────────────────────────────────────────────────

    async def get_expenses(self, updated_after=None, page_size=None, page_token=None):
        return await self._get_list("/expenses", self._page(page_size, page_token, updated_after))

    async def get_expense(self, expense_id: int):
        return await self._get_one(f"/expenses/{int(expense_id)}")

    # ── Firm ─────────────────────────────────────────────────────────────────────

    async def get_firm(self):
        return await self._get_one("/firm")

    async def get_me(self):
        return await self._get_one("/me")

    # ── Folders ──────────────────────────────────────────────────────────────────

    async def get_folder_documents(self, folder_id: int, updated_after=None, page_size=None, page_token=None):
        return await self._get_list(
            f"/folders/{int(folder_id)}/documents", self._page(page_size, page_token, updated_after)
        )

    async def get_folder_subfolders(self, folder_id: int, updated_after=None, page_size=None, page_token=None):
        return await self._get_list(
            f"/folders/{int(folder_id)}/subfolders", self._page(page_size, page_token, updated_after)
        )

    # ── Invoices ─────────────────────────────────────────────────────────────────

    async def get_invoices(
        self, updated_after=None, only_allowed_online_payments=None, page_size=None, page_token=None,
    ):
        params = self._page(page_size, page_token, updated_after)
        if only_allowed_online_payments is not None:
            params["only_allowed_online_payments"] = only_allowed_online_payments
        return await self._get_list("/invoices", params)

    async def get_invoice_payments(self, payable_id=None, status=None, page_size=None, page_token=None):
        params = self._page(page_size, page_token)
        if payable_id is not None:
            params["filter[payable_id]"] = payable_id
        if status is not None:
            params["filter[status]"] = status
        return await self._get_list("/invoice_payments", params)

    # ── Leads ────────────────────────────────────────────────────────────────────

    async def get_leads(
        self, first_name=None, last_name=None, email=None, cell_phone_number=None,
        home_phone_number=None, work_phone_number=None, updated_after=None,
        page_size=None, page_token=None,
    ):
        params = self._page(page_size, page_token, updated_after)
        for key, val in (
            ("filter[first_name]", first_name), ("filter[last_name]", last_name),
            ("filter[email]", email), ("filter[cell_phone_number]", cell_phone_number),
            ("filter[home_phone_number]", home_phone_number),
            ("filter[work_phone_number]", work_phone_number),
        ):
            if val is not None:
                params[key] = val
        return await self._get_list("/leads", params)

    async def get_lead(self, lead_id: int):
        return await self._get_one(f"/leads/{int(lead_id)}")

    # ── Locations ────────────────────────────────────────────────────────────────

    async def get_locations(self, updated_after=None, page_size=None, page_token=None):
        return await self._get_list("/locations", self._page(page_size, page_token, updated_after))

    # ── Notes ────────────────────────────────────────────────────────────────────

    async def get_note(self, note_id: int):
        return await self._get_one(f"/notes/{int(note_id)}")

    # ── People Groups ────────────────────────────────────────────────────────────

    async def get_people_groups(self, updated_after=None, page_size=None, page_token=None):
        return await self._get_list("/people_groups", self._page(page_size, page_token, updated_after))

    # ── Practice Areas ───────────────────────────────────────────────────────────

    async def get_practice_areas(self, updated_after=None, page_size=None, page_token=None):
        return await self._get_list("/practice_areas", self._page(page_size, page_token, updated_after))

    # ── Referral Sources ─────────────────────────────────────────────────────────

    async def get_referral_sources(self, updated_after=None, page_size=None, page_token=None):
        return await self._get_list("/referral_sources", self._page(page_size, page_token, updated_after))

    # ── Staff ────────────────────────────────────────────────────────────────────

    async def get_staff(self, status=None, updated_after=None, page_size=None, page_token=None):
        params = self._page(page_size, page_token, updated_after)
        if status is not None:
            params["filter[status]"] = status
        return await self._get_list("/staff", params)

    async def get_individual_staff(self, staff_id: int):
        return await self._get_one(f"/staff/{int(staff_id)}")

    # ── Tasks ────────────────────────────────────────────────────────────────────

    async def get_tasks(self, updated_after=None, page_size=None, page_token=None):
        return await self._get_list("/tasks", self._page(page_size, page_token, updated_after))

    # ── Time Entries ─────────────────────────────────────────────────────────────

    async def get_time_entries(self, updated_after=None, page_size=None, page_token=None):
        return await self._get_list("/time_entries", self._page(page_size, page_token, updated_after))

    async def get_time_entry(self, time_entry_id: int):
        return await self._get_one(f"/time_entries/{int(time_entry_id)}")

    # ── Webhooks ─────────────────────────────────────────────────────────────────

    async def get_webhook_subscriptions(self):
        result = await self._get_one("/webhooks/subscriptions")
        return {"items": result if isinstance(result, list) else []}

    # ── Case reporting (deterministic filter/exclude/group-by, computed server-side) ──
    # Session 29: an LLM reasoning over hundreds/thousands of raw case records to
    # compute a filtered, grouped report is unreliable BY CONSTRUCTION — each tool
    # result is truncated at _MAX_TOOL_OUTPUT_CHARS before it ever reaches the model,
    # so it never actually sees most records' custom field values at that scale, and
    # even without truncation, LLM arithmetic over hundreds of rows is error-prone.
    # This does the fetching (walks EVERY page internally, never exposing raw case
    # JSON to the LLM), filtering, stage exclusion, and grouping/counting in Python —
    # accurate regardless of case count — and returns a small, already-aggregated,
    # already-flattened result the agent only needs to present, not compute.

    _BUILTIN_CASE_FIELDS = {"practice_area", "case_stage", "status", "billing_type"}

    async def _load_custom_fields(self) -> list[dict[str, Any]]:
        """Walk EVERY page of get_custom_fields() — a single unpaginated call only
        returns the default page_size (25), silently missing any field beyond that
        (confirmed live: a 46-field firm had both "CASE TYPE" and "PROCESSING AGENT"
        past position 25, causing aggregate_cases to falsely reject both as unknown
        despite them being real, confirmed field names)."""
        fields: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            result = await self.get_custom_fields(page_size=200, page_token=page_token)
            fields.extend(result.get("items", []))
            page_token = result.get("next_page_token")
            if not page_token:
                break
        return fields

    async def _custom_field_maps(self) -> tuple[dict[str, int], dict[int, str]]:
        """(name.lower() -> id, id -> real display name) — one paginated fetch,
        used both to resolve a user-given field name to its id and to expand each
        case's custom_field_values back into name-labelled columns."""
        name_to_id: dict[str, int] = {}
        id_to_name: dict[int, str] = {}
        for cf in await self._load_custom_fields():
            name, cfid = cf.get("name"), cf.get("id")
            if isinstance(name, str) and isinstance(cfid, int):
                name_to_id[name.strip().lower()] = cfid
                id_to_name[cfid] = name
        return name_to_id, id_to_name

    @staticmethod
    def _case_custom_value(case: dict[str, Any], field_id: int) -> str | None:
        for cfv in case.get("custom_field_values") or []:
            cf = cfv.get("custom_field") or {}
            if cf.get("id") == field_id:
                val = cfv.get("value")
                return None if val is None else str(val)
        return None

    async def aggregate_cases(
        self,
        practice_area: str | None = None,
        custom_field_filters: dict[str, str] | None = None,
        case_stages: list[str] | None = None,
        exclude_case_stages: list[str] | None = None,
        group_by: str | None = None,
        status: str | None = None,
        updated_after: str | None = None,
        max_cases: int = 5000,
    ) -> dict[str, Any]:
        """Fetch every case matching the given filters (paginating internally,
        server-side) — including case_stages (keep ONLY cases in one of these exact
        stages, if given) and excluding exclude_case_stages — then group the survivors
        by ``group_by`` (a builtin field name — practice_area/case_stage/status/
        billing_type — or a CUSTOM FIELD NAME, e.g. "PROCESSING AGENT") and count per
        group. Returns a flat, report-ready ``items`` array —
        one row per surviving case, with EVERY field MyCase returns for that case
        (id, case_number, name, case_stage, practice_area, status, clients, staff,
        etc. — whatever get_cases returns) as its own column. Each custom field is
        ALSO broken out into its own column labelled with its real name (e.g. "CASE
        TYPE", "PROCESSING AGENT") instead of being left as one nested
        custom_field_values blob — plus that case's group's ``group_name``/
        ``case_count``. The report-level totals (total_cases, total_groups,
        group_by_field, report_date) are only at the top level of this result, not
        repeated on every row.

        practice_area / custom_field_filters values match case-insensitively as a
        SUBSTRING (so filtering "Asylum" also matches "Asylum - Affirmative" and
        "Asylum - Defense"). case_stages / exclude_case_stages match case-insensitively
        but EXACTLY (a stage name is a whole discrete value, not something to
        substring-match — "CLOSED" must not also pull in "CLOSED WITH BALANCE") —
        resolve the real stage strings via get_case_stages() first; don't guess them.
        """
        name_to_id, id_to_name = await self._custom_field_maps()

        def resolve_field(field: str) -> tuple[bool, int | str]:
            """Return (is_builtin, key) — key is the builtin field name, or the
            resolved numeric custom-field id."""
            low = field.strip().lower()
            if low in self._BUILTIN_CASE_FIELDS:
                return True, low
            if low in name_to_id:
                return False, name_to_id[low]
            raise ValueError(
                f"'{field}' is not a builtin case field ({sorted(self._BUILTIN_CASE_FIELDS)}) "
                f"or a known custom field name. Call get_custom_fields() to see valid names."
            )

        filter_specs: list[tuple[bool, int | str, str]] = []
        for field, want in (custom_field_filters or {}).items():
            is_builtin, key = resolve_field(field)
            filter_specs.append((is_builtin, key, want.strip().lower()))

        group_is_builtin, group_key = (True, "practice_area") if group_by is None else resolve_field(group_by)

        exclude_set = {s.strip().upper() for s in (exclude_case_stages or [])}
        include_stage_set = {s.strip().upper() for s in (case_stages or [])}

        # Walk every page server-side — this is exactly what avoids truncation:
        # nothing here is exposed to the LLM until after filtering/grouping below.
        all_cases: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(all_cases) < max_cases:
            page = await self.get_cases(status=status, updated_after=updated_after, page_size=1000, page_token=page_token)
            all_cases.extend(page.get("items", []))
            page_token = page.get("next_page_token")
            if not page_token:
                break

        def matches(case: dict[str, Any]) -> bool:
            if practice_area and practice_area.strip().lower() not in (case.get("practice_area") or "").lower():
                return False
            stage = (case.get("case_stage") or "").strip().upper()
            if stage in exclude_set:
                return False
            if include_stage_set and stage not in include_stage_set:
                return False
            for is_builtin, key, want in filter_specs:
                actual = case.get(key) if is_builtin else self._case_custom_value(case, key)  # type: ignore[arg-type]
                if not actual or want not in actual.lower():
                    return False
            return True

        survivors = [c for c in all_cases if matches(c)]

        def group_value(case: dict[str, Any]) -> str:
            if group_is_builtin:
                return case.get(group_key) or "(none)"  # type: ignore[arg-type]
            val = self._case_custom_value(case, group_key)  # type: ignore[arg-type]
            return val or "(unassigned)"

        counts: dict[str, int] = {}
        for c in survivors:
            g = group_value(c)
            counts[g] = counts.get(g, 0) + 1

        total_cases = len(survivors)
        total_groups = len(counts)
        report_date = time.strftime("%Y-%m-%d")
        group_field_label = group_by or "practice_area"

        def expand_custom_fields(case: dict[str, Any], row: dict[str, Any]) -> None:
            """Break custom_field_values out into one column per field, named with
            its real display name — so a report never shows a raw nested blob."""
            for cfv in case.get("custom_field_values") or []:
                cfid = (cfv.get("custom_field") or {}).get("id")
                name = id_to_name.get(cfid) if isinstance(cfid, int) else None
                col = name or f"custom_field_{cfid}"
                if col in row:  # avoid clobbering a same-named builtin field
                    col = f"{col} (custom field)"
                row[col] = cfv.get("value")

        items: list[dict[str, Any]] = []
        for c in survivors:
            g = group_value(c)
            row = {k: v for k, v in c.items() if k != "custom_field_values"}
            expand_custom_fields(c, row)
            row["group_name"] = g
            row["case_count"] = counts[g]
            items.append(row)

        return {
            "success": True,
            "total_cases": total_cases,
            "total_groups": total_groups,
            "group_by_field": group_field_label,
            "report_date": report_date,
            "cases_scanned": len(all_cases),
            "truncated": len(all_cases) >= max_cases,
            "items": items,
        }

    async def search_cases(
        self,
        query: str,
        status: str | None = None,
        max_cases: int = 5000,
    ) -> dict[str, Any]:
        """Find cases by a loose text query — MyCase's API has NO server-side search
        or filter for case_number or case name (confirmed: get_cases only documents
        filter[status] and filter[updated_after]), so a request like "find the case
        numbered/named X" has no targeted endpoint to call. This walks every page of
        get_cases server-side (same pattern as aggregate_cases).

        If ``query`` is an EXACT case_number match (case-insensitive) for one or more
        cases, ONLY those are returned — a case's display ``name`` often embeds a
        padded reference number (e.g. name "01597-Smith" vs a DIFFERENT case whose
        real case_number happens to also contain "1597"), so when the user gave an
        exact number, an exact case_number match is unambiguous and must win over any
        coincidental name substring hit. Only when there is NO exact case_number match
        does this fall back to a substring match against case_number OR name — so the
        caller gets back ONLY the real, intended match(es), never noise.
        """
        q = (query or "").strip().lower()
        if not q:
            raise ValueError("query must not be empty")

        all_cases: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(all_cases) < max_cases:
            page = await self.get_cases(status=status, page_size=1000, page_token=page_token)
            all_cases.extend(page.get("items", []))
            page_token = page.get("next_page_token")
            if not page_token:
                break

        exact = [c for c in all_cases if (c.get("case_number") or "").strip().lower() == q]
        if exact:
            matched = exact
        else:
            def matches(c: dict[str, Any]) -> bool:
                return q in (c.get("case_number") or "").lower() or q in (c.get("name") or "").lower()

            matched = [c for c in all_cases if matches(c)]
        return {
            "success": True,
            "query": query,
            "cases_scanned": len(all_cases),
            "truncated": len(all_cases) >= max_cases,
            "match_count": len(matched),
            "items": matched,
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
mycase_rest = MyCaseREST()
