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

    async def connection_status(self) -> dict[str, Any]:
        """Richer than is_connected() — a real incident showed the UI's "MyCase
        connected" badge staying up unchanged even after the stored token had
        actually expired, because is_connected() only checks that SOME token
        string is present, never whether it's still valid. This exposes the real
        expiry (when known) so the UI can show a live countdown / an honest
        "expired" state instead of a static badge that can silently lie.

        Does NOT trigger a refresh — this is a cheap, side-effect-free read of
        currently stored settings; the real refresh already happens transparently
        in _get_valid_token() before any actual MyCase API call.
        """
        token = await get_setting("mycase_access_token")
        refresh_token = await get_setting("mycase_refresh_token")
        expiry_raw = await get_setting("mycase_token_expiry")

        expires_at: int | None = None
        expires_in_seconds: int | None = None
        expired = False
        if expiry_raw:
            try:
                expires_at = int(expiry_raw)
                expires_in_seconds = expires_at - int(time.time())
                expired = expires_in_seconds <= 0
            except ValueError:
                expires_at = None

        return {
            "connected": bool(token),
            # expires_at/expires_in_seconds are null when the expiry is genuinely
            # unknown — e.g. a manually-pasted access token with no accompanying
            # refresh token, which _store_token() never got an expires_in for.
            "expires_at": expires_at,
            "expires_in_seconds": expires_in_seconds,
            "expired": expired,
            # Whether a refresh_token is on file — if True, an "expired" token
            # above will transparently self-heal on the next real MyCase call
            # (see _get_valid_token()); if False, "expired" means the connection
            # is genuinely dead until the user reconnects or pastes a fresh token.
            "auto_renews": bool(refresh_token),
        }

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

    async def _download(self, path: str, expires_in: str) -> dict[str, Any]:
        """Download endpoints 302-redirect to a temporary signed URL — surface it
        rather than following the redirect (bytes shouldn't transit the LLM).

        `expires_in` is the REAL validity window per MyCase's own docs (1 minute for
        a document's current version, 1 hour for a specific version) — included in
        the result itself, not just the tool description, because a model reading a
        description once amid 50+ tool schemas has repeatedly been observed to
        hallucinate a different (usually more generous) duration when telling the
        user, which sends them to click a link that's already expired. Once expired,
        MyCase's underlying S3 storage returns a confusing
        "Request specific response headers cannot be used for anonymous GET
        requests" XML error (not an obvious "expired" message) — `note` spells that
        out too so the model can explain it accurately instead of guessing the link
        itself must be broken.
        """
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            resp = await client.get(f"{_API_BASE}{path}", headers=headers)
        if resp.status_code in (301, 302, 303, 307, 308):
            return {
                "success": True,
                "download_url": resp.headers.get("Location"),
                "expires_in": expires_in,
                "note": (
                    f"This link is only valid for {expires_in} — tell the user to click it "
                    "right away, and state this exact expiry (do not guess a different "
                    "duration). If it's already expired, MyCase returns an XML error like "
                    "'Request specific response headers cannot be used for anonymous GET "
                    "requests' when opened in a browser — that error means EXPIRED, not "
                    "broken; just call this tool again for a fresh link."
                ),
            }
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

    # ── Date-precise filtering (deterministic) ──────────────────────────────────
    # Confirmed against MyCase's own docs: filter[updated_after] is the ONLY
    # server-side date filter on ANY list endpoint in this API (cases, invoices,
    # expenses, time entries, events, tasks, documents, ...) — it is a floor
    # ("created or updated after this date/time"), not an exact-date match, and it
    # has nothing to do with invoice_date/due_date/opened_date/event start time. A
    # request like "invoices CREATED on 20 July 2026" therefore has no server-side
    # equivalent — reported live: get_invoices(updated_after=<that day>) correctly
    # returned every invoice touched since then (20 rows), but the model was left to
    # eyeball which ones were actually CREATED that day, and the chat's own result
    # table (built straight from the raw, unfiltered tool result — see
    # mycase-agent/page.tsx) still showed all 20, 16 of them from unrelated creation
    # dates, even though the model's own prose correctly caught the discrepancy.
    # Same fix pattern as aggregate_cases (Session 29): walk every page and filter
    # EXACTLY in Python before anything reaches the model, rather than trusting the
    # model to filter a broader raw batch in its head.

    @staticmethod
    def _as_float(value: Any) -> float:
        """MyCase's docs type invoice amounts (total_amount/paid_amount) as
        'number', but a real account has been observed returning them as strings
        (e.g. "500.0") — a bare arithmetic op on the raw value then raises
        TypeError. Tolerates str/int/float/None uniformly; an unparseable value
        degrades to 0.0 rather than raising."""
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip())
        except ValueError:
            return 0.0

    @staticmethod
    def _date_only(value: str | None) -> str | None:
        """First 10 chars of an ISO date/datetime string ('2026-07-20T14:05Z' ->
        '2026-07-20'). Plain string slicing is safe and sufficient here — every
        date this API returns is ISO 8601 with the date first, and lexicographic
        comparison of 'YYYY-MM-DD' strings is equivalent to chronological order."""
        if not value or not isinstance(value, str):
            return None
        return value[:10]

    @classmethod
    def _date_matches(cls, value: str | None, on: str | None, after: str | None, before: str | None) -> bool:
        d = cls._date_only(value)
        if d is None:
            return False
        if on and d != on:
            return False
        if after and d < after:
            return False
        if before and d > before:
            return False
        return True

    # Shared safety ceiling for every dynamic scan cap below. Confirmed LIVE
    # against the real connected account (Session 34): 7,216 total cases and
    # 48,161 total documents — both comfortably past the various fixed guesses
    # (5000 for cases/invoices, 20000 for documents) that used to be hardcoded
    # here, which is exactly why "find case X" / "N cases and their invoices" /
    # "cases with documents" kept silently missing real, existing records. Raised
    # from the earlier 50000 (invoices-only) ceiling for headroom above the
    # observed 48k-document scale.
    _SCAN_HARD_CEILING = 100000

    @classmethod
    def _cap_from_probe(cls, requested: int | None, probe: dict[str, Any]) -> tuple[int, bool]:
        """Turns a cheap page_size=1 probe's item_count into a scan cap sized to
        the REAL total instead of a fixed guess. Returns (cap, probe_ok) —
        probe_ok is False when the probe didn't return a usable item_count (seen
        live on /invoices, even though the same Item-Count header works reliably
        on /cases and /documents); callers log that themselves so the log line
        can name which resource's probe failed. An explicit `requested` cap
        always short-circuits to itself, no probe call needed."""
        if requested is not None:
            return requested, True
        total = probe.get("item_count")
        if isinstance(total, int) and total > 0:
            return min(total, cls._SCAN_HARD_CEILING), True
        return cls._SCAN_HARD_CEILING, False

    async def _invoice_scan_cap(
        self, requested: int | None, only_allowed_online_payments: bool | None = None,
        updated_after: str | None = None,
    ) -> int:
        """Sizes an invoice page-walk to the REAL total via a cheap page_size=1
        probe (using the SAME filters the real walk will use, so the probed total
        matches what's actually being walked) instead of trusting a fixed guess —
        a fixed cap silently under-fetches once there are more matching invoices
        than the guess (confirmed live: a real account with >5000 invoices had
        4 of 5 requested cases' invoices sitting past a hardcoded 5000 cutoff).
        Shared by aggregate_cases(include_invoices=True), get_case_invoices, and
        get_invoices_by_date — all three independently had this exact gap."""
        if requested is not None:
            return requested
        probe = await self.get_invoices(
            page_size=1, only_allowed_online_payments=only_allowed_online_payments, updated_after=updated_after,
        )
        cap, ok = self._cap_from_probe(requested, probe)
        if not ok:
            logger.warning("mycase_scan_cap_probe_missing_item_count", resource="invoices", probe_keys=list(probe.keys()))
        return cap

    async def _case_scan_cap(self, requested: int | None, status: str | None = None) -> int:
        """Same idea as _invoice_scan_cap, for the full-case-list walk used by
        search_cases/aggregate_cases. Confirmed live: this real account has 7,216
        total cases — well past the old hardcoded 5000-case cap — so a case
        created/ordered past position 5000 (e.g. "Asylum Case For Berette Desir")
        was silently invisible to search_cases regardless of how exactly the
        query matched its name, because it was never even in the scanned batch."""
        if requested is not None:
            return requested
        probe = await self.get_cases(page_size=1, status=status)
        cap, ok = self._cap_from_probe(requested, probe)
        if not ok:
            logger.warning("mycase_scan_cap_probe_missing_item_count", resource="cases", probe_keys=list(probe.keys()))
        return cap

    async def _document_scan_cap(self, requested: int | None) -> int:
        """Same idea, for the firm-wide document walk in find_cases_with_documents.
        Confirmed live: this real account has 48,161 total documents — past the
        old hardcoded 20000-document cap."""
        if requested is not None:
            return requested
        probe = await self.get_documents(page_size=1)
        cap, ok = self._cap_from_probe(requested, probe)
        if not ok:
            logger.warning("mycase_scan_cap_probe_missing_item_count", resource="documents", probe_keys=list(probe.keys()))
        return cap

    async def _walk_all_pages(self, fetch_page, max_records: int) -> tuple[list[dict[str, Any]], bool]:
        """fetch_page(page_token) awaits one {"items", "next_page_token"} page.
        Returns (all items collected, truncated) — truncated is True only if
        max_records was hit before the real last page."""
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(items) < max_records:
            page = await fetch_page(page_token)
            items.extend(page.get("items", []))
            page_token = page.get("next_page_token")
            if not page_token:
                return items, False
        return items, True

    async def _fetch_filtered_by_date(
        self, fetch_page, date_field: str,
        on: str | None, after: str | None, before: str | None, max_records: int,
    ) -> dict[str, Any]:
        on_d, after_d, before_d = self._date_only(on), self._date_only(after), self._date_only(before)
        all_items, truncated = await self._walk_all_pages(fetch_page, max_records)
        matched = [it for it in all_items if self._date_matches(it.get(date_field), on_d, after_d, before_d)]
        return {
            "success": True,
            "date_field": date_field,
            "matched_count": len(matched),
            "records_scanned": len(all_items),
            "truncated": truncated,
            "items": matched,
        }

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

    async def find_cases_with_documents(self, limit: int = 5, max_documents: int | None = None) -> dict[str, Any]:
        """Find cases that actually HAVE documents attached — MyCase has no server-
        side filter for this, so the naive approach (sample a few cases, hope some
        have documents) gives wrong/incomplete answers, the same class of problem
        aggregate_cases/search_cases solve elsewhere. Instead this walks every page
        of get_documents() firm-wide (each document's ``case`` field is ``{"id": N}``
        per MyCase's schema), tallies how many documents belong to each case, then
        fetches full case details for the ``limit`` cases with the MOST documents.

        max_documents defaults to the REAL total document count (via a cheap
        probe — see _document_scan_cap), not a fixed guess — confirmed live this
        account has 48,161 total documents, well past the old hardcoded 20000 cap
        that used to silently under-count which cases actually have the most.
        """
        doc_cap = await self._document_scan_cap(max_documents)

        async def fetch_doc_page(token: str | None) -> dict[str, Any]:
            return await self.get_documents(page_size=1000, page_token=token)

        # _walk_all_pages reports truncation correctly via page_token — see
        # search_cases' identical comment for why a len(...) >= cap comparison is
        # now wrong essentially every time, since doc_cap equals the real total
        # for most firms.
        all_docs, documents_truncated = await self._walk_all_pages(fetch_doc_page, doc_cap)

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
            "documents_truncated": documents_truncated,
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
        return await self._download(f"/documents/{int(document_id)}/data", expires_in="1 minute")

    async def download_document_version(self, document_id: int, version_number: int):
        return await self._download(
            f"/documents/{int(document_id)}/versions/{int(version_number)}/data", expires_in="1 hour"
        )

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

    _INVOICE_DATE_FIELDS = {"created_at", "updated_at", "invoice_date", "due_date"}

    async def get_invoices_by_date(
        self, date_field: str = "created_at",
        on: str | None = None, after: str | None = None, before: str | None = None,
        only_allowed_online_payments: bool | None = None, max_invoices: int | None = None,
    ) -> dict[str, Any]:
        """Exact/range-filter invoices by created_at, updated_at, invoice_date, or
        due_date. get_invoices' only server-side filter is updated_after (a floor on
        created-OR-updated time) — it cannot express "created ON this exact day" and
        has no relationship at all to invoice_date/due_date. This walks every page
        and filters precisely in Python instead.

        `on`/`after`/`before` are YYYY-MM-DD (a full timestamp also works — only the
        date portion is compared). `on` is an exact-day match; `after`/`before` are
        inclusive range bounds; combine after+before for a range.

        `max_invoices` defaults to the real (possibly pre-filtered by
        updated_after) total via a cheap probe — see `_invoice_scan_cap` — not a
        fixed guess, so a firm with more invoices than a hardcoded cap doesn't
        silently miss matches sitting past it.

        `only_allowed_online_payments` defaults to False here (match ALL invoices in
        the date range) — NOT MyCase's own server default of True, which silently
        excludes invoices with online payments disabled from the result entirely.
        """
        if date_field not in self._INVOICE_DATE_FIELDS:
            raise ValueError(f"date_field must be one of {sorted(self._INVOICE_DATE_FIELDS)}")

        # updated_after is a safe SERVER-SIDE pre-filter (reduces pages walked) only
        # when we're checking created_at/updated_at with a lower bound — updated_at
        # is always >= created_at, so any invoice created on/after X necessarily has
        # updated_at >= X too and will still be fetched. It is NOT safe for
        # invoice_date/due_date (unrelated to when the row was last touched) or
        # when only an upper bound (`before`) is given (we'd need the old rows).
        updated_after_hint = (on or after) if (date_field in ("created_at", "updated_at") and not before) else None
        effective_online_filter = only_allowed_online_payments if only_allowed_online_payments is not None else False

        async def fetch_page(token: str | None) -> dict[str, Any]:
            return await self.get_invoices(
                updated_after=updated_after_hint,
                only_allowed_online_payments=effective_online_filter,
                page_size=1000, page_token=token,
            )

        invoice_cap = await self._invoice_scan_cap(
            max_invoices, only_allowed_online_payments=effective_online_filter, updated_after=updated_after_hint,
        )
        result = await self._fetch_filtered_by_date(fetch_page, date_field, on, after, before, invoice_cap)
        result["only_allowed_online_payments"] = effective_online_filter
        return result

    # Real MyCase invoice status values (confirmed from the docs — Session 22):
    # overdue | paid | partial | draft | unsent | sent | forwarded.
    _INVOICE_STATUSES = {"overdue", "paid", "partial", "draft", "unsent", "sent", "forwarded"}

    async def aggregate_invoices(
        self,
        status: str | None = None,
        paid: bool | None = None,
        min_balance_due: float | None = None,
        invoice_date_after: str | None = None,
        invoice_date_before: str | None = None,
        due_date_after: str | None = None,
        due_date_before: str | None = None,
        sort_by: str = "balance_due",
        limit: int | None = None,
        only_allowed_online_payments: bool | None = None,
        max_invoices: int | None = None,
    ) -> dict[str, Any]:
        """Filter/sort/limit invoices FIRM-WIDE — the invoice equivalent of
        aggregate_cases, and for the exact same reason: get_invoices has NO
        server-side filter for status or balance-due at all (only updated_after),
        so a request like "top 100 unpaid invoices" has no targeted endpoint to
        call. A real incident: asked for "top 100 invoices that are unpaid", the
        model called plain get_invoices(page_size=100) — the first 100 invoices
        in whatever order MyCase returns them, UNFILTERED, several of which were
        already fully paid. Use this instead of get_invoices for ANY "N invoices
        where status/paid is X" or "invoices sorted by Y" request.

        `status`: exact match (case-insensitive) against one of MyCase's real
        invoice statuses — overdue, paid, partial, draft, unsent, sent, forwarded.
        `paid`: a convenience filter on balance_due (total_amount - paid_amount):
        True = fully paid (balance_due <= 0), False = NOT fully paid — this is
        what "unpaid" means in a request like "unpaid invoices" or "invoices that
        haven't been paid" (covers overdue/partial/draft/unsent/sent/forwarded in
        one filter, not just status="overdue"). Combine `status` and `paid` if the
        user is specific about both (e.g. "overdue invoices that are unpaid" — a
        no-op combo since overdue implies unpaid, but harmless).
        `min_balance_due`: only invoices owing at least this much.
        `invoice_date_after`/`invoice_date_before`, `due_date_after`/
        `due_date_before` (YYYY-MM-DD, inclusive): same date-range filtering as
        get_invoices_by_date, available here too so a single call can combine a
        status/paid filter with a date range instead of needing two tools.
        `sort_by`: "balance_due" (default, DESCENDING — largest amount owed
        first, the usual meaning of "top N unpaid invoices"), "due_date"
        (ASCENDING — the oldest/most-overdue due date first), or "invoice_date"
        (DESCENDING — most recently invoiced first).
        `limit`: caps `items` to the first N sorted/filtered rows — total_invoices
        still reports the TRUE full-match count regardless of limit.

        Each returned row is a real invoice with total_amount/paid_amount
        normalized to floats (see _as_float — this account has been observed
        returning them as strings) plus a computed `balance_due` column.
        `only_allowed_online_payments` defaults to False (match ALL invoices,
        not just the online-payable subset) — same reasoning as every other
        invoice tool here.
        """
        if status is not None and status.strip().lower() not in self._INVOICE_STATUSES:
            raise ValueError(f"status must be one of {sorted(self._INVOICE_STATUSES)}, got {status!r}")
        if sort_by not in ("balance_due", "due_date", "invoice_date"):
            raise ValueError("sort_by must be one of 'balance_due', 'due_date', 'invoice_date'")

        effective_online_filter = only_allowed_online_payments if only_allowed_online_payments is not None else False
        invoice_cap = await self._invoice_scan_cap(max_invoices, only_allowed_online_payments=effective_online_filter)

        async def fetch_page(token: str | None) -> dict[str, Any]:
            return await self.get_invoices(page_size=1000, page_token=token, only_allowed_online_payments=effective_online_filter)

        all_invoices, truncated = await self._walk_all_pages(fetch_page, invoice_cap)

        status_l = status.strip().lower() if status else None
        invoice_after_d, invoice_before_d = self._date_only(invoice_date_after), self._date_only(invoice_date_before)
        due_after_d, due_before_d = self._date_only(due_date_after), self._date_only(due_date_before)

        rows: list[dict[str, Any]] = []
        for inv in all_invoices:
            total = self._as_float(inv.get("total_amount"))
            paid_amt = self._as_float(inv.get("paid_amount"))
            row = {**inv, "total_amount": total, "paid_amount": paid_amt, "balance_due": round(total - paid_amt, 2)}
            rows.append(row)

        def matches(r: dict[str, Any]) -> bool:
            if status_l and (r.get("status") or "").strip().lower() != status_l:
                return False
            if paid is True and r["balance_due"] > 0.005:
                return False
            if paid is False and r["balance_due"] <= 0.005:
                return False
            if min_balance_due is not None and r["balance_due"] < min_balance_due:
                return False
            if (invoice_after_d or invoice_before_d) and not self._date_matches(
                r.get("invoice_date"), None, invoice_after_d, invoice_before_d
            ):
                return False
            if (due_after_d or due_before_d) and not self._date_matches(
                r.get("due_date"), None, due_after_d, due_before_d
            ):
                return False
            return True

        survivors = [r for r in rows if matches(r)]

        # balance_due/invoice_date: biggest/newest first (descending). due_date:
        # oldest first (ascending) — the earliest due date is the most overdue /
        # highest priority, which is what "top" means for a due-date sort.
        if sort_by == "due_date":
            survivors.sort(key=lambda r: r.get("due_date") or "9999-99-99")
        elif sort_by == "invoice_date":
            survivors.sort(key=lambda r: r.get("invoice_date") or "", reverse=True)
        else:
            survivors.sort(key=lambda r: r["balance_due"], reverse=True)

        total_matching = len(survivors)
        display = survivors[:limit] if limit is not None else survivors

        return {
            "success": True,
            "total_invoices": total_matching,
            "invoices_scanned": len(all_invoices),
            "truncated": truncated,
            "invoices_shown": len(display),
            "limited": limit is not None and len(display) < total_matching,
            "sort_by": sort_by,
            "items": display,
        }

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

    async def _staff_name_map(self) -> dict[int, str]:
        """id -> "First Last" for every staff member — one paginated fetch, used to
        resolve a case's lead_lawyer staff id to a real name (the case object itself
        only ever returns a bare staff id, never a name)."""
        names: dict[int, str] = {}
        page_token: str | None = None
        while True:
            page = await self.get_staff(page_size=200, page_token=page_token)
            for s in page.get("items", []):
                sid = s.get("id")
                if isinstance(sid, int):
                    full = " ".join(p for p in (s.get("first_name"), s.get("last_name")) if p) or None
                    names[sid] = full or s.get("email") or f"staff #{sid}"
            page_token = page.get("next_page_token")
            if not page_token:
                break
        return names

    # Curated, admin-extensible canonicalization map for Processing Agent name
    # variants (duplicate spellings, nicknames, etc.) — deliberately a small static
    # dict rather than automatic fuzzy-matching: fuzzy name matching risks silently
    # merging two DIFFERENT real people who happen to have similar names, which is a
    # worse outcome than an occasional unmerged duplicate. Keys are matched against
    # the whitespace-collapsed, trimmed raw value, case-insensitively. Extend this
    # as real duplicate spellings are found in the data — it's intentionally empty
    # by default since no confirmed duplicates have been identified yet.
    _AGENT_ALIAS_MAP: dict[str, str] = {}

    @classmethod
    def _normalize_agent_name(cls, raw: str | None) -> str:
        """Blank/None -> "(unassigned)". Otherwise: collapse internal whitespace
        runs and trim (never re-case or otherwise alter real name text — that risks
        corrupting legitimate capitalization, e.g. a name with an internal
        capital), then apply the curated alias map if this exact cleaned value is a
        known variant of a canonical name."""
        if raw is None:
            return "(unassigned)"
        cleaned = re.sub(r"\s+", " ", str(raw)).strip()
        if not cleaned:
            return "(unassigned)"
        return cls._AGENT_ALIAS_MAP.get(cleaned.lower(), cleaned)

    async def aggregate_cases(
        self,
        practice_area: str | None = None,
        custom_field_filters: dict[str, str] | None = None,
        case_stages: list[str] | None = None,
        exclude_case_stages: list[str] | None = None,
        group_by: str | None = None,
        status: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        opened_after: str | None = None,
        opened_before: str | None = None,
        closed_after: str | None = None,
        closed_before: str | None = None,
        max_cases: int | None = None,
        limit: int | None = None,
        include_invoices: bool = False,
    ) -> dict[str, Any]:
        """Fetch every case matching the given filters (paginating internally,
        server-side) — including case_stages (keep ONLY cases in one of these exact
        stages, if given) and excluding exclude_case_stages — then group the survivors
        by ``group_by`` (a builtin field name — practice_area/case_stage/status/
        billing_type — or a CUSTOM FIELD NAME, e.g. "PROCESSING AGENT") and count per
        group. Returns a flat, report-ready ``items`` array —
        one row per surviving case, with EVERY field MyCase returns for that case
        (id, case_number, name, case_stage, practice_area, status, clients, staff,
        etc. — whatever get_cases returns) as its own column, PLUS:

        ``limit`` — when the user asked for a SPECIFIC NUMBER of cases ("show me 5
        immigration cases", "list 10 open cases"), pass it here rather than trying to
        eyeball-truncate the result yourself. Applied LAST, after every filter and
        after group_name/case_count are computed on the FULL matching set — so
        ``total_cases``/``total_groups``/each row's ``case_count`` always reflect the
        true totals, while ``items``/``cases_shown`` reflect only the first ``limit``
        rows actually returned. Without ``limit``, ALL matching cases are returned
        (existing behavior, unchanged).

        ``include_invoices`` — when the user wants each case's invoices too ("...and
        their invoices", "with billing info"), set this instead of separately calling
        get_case_invoices once per case. This fetches every firm invoice ONE time
        (regardless of how many cases matched) and attaches each surviving case's own
        invoices directly onto its row: ``invoice_count``, ``outstanding_invoice_total``
        (sum of unpaid balances), and ``invoices`` (each with id, invoice_number,
        status, invoice_date, due_date, total_amount, paid_amount, balance_due, and a
        ready-to-read ``label``). Calling get_case_invoices in a loop instead re-walks
        MyCase's entire invoice list from scratch on EVERY case — slow, and each call
        is a separate step the model can forget to make for every case; this does the
        whole thing in one deterministic pass.
        - ``client_name`` — resolved from the case's clients (first_name + last_name,
          requested via field[client] expansion so no extra API calls are needed),
          comma-joined if there's more than one client.
        - ``assigned_attorney`` — the staff member with lead_lawyer=true, resolved to
          a real name via one staff lookup (the case object itself only has a bare
          staff id). "(unassigned)" if no staff member is flagged lead_lawyer.
        - Each custom field is ALSO broken out into its own column labelled with its
          real name (e.g. "CASE TYPE", "PROCESSING AGENT") instead of being left as
          one nested custom_field_values blob. The "PROCESSING AGENT" field
          specifically gets BOTH "PROCESSING AGENT (original)" (verbatim raw value)
          AND a cleaned "PROCESSING AGENT" column (whitespace-collapsed, blank/None
          normalized to "(unassigned)", and passed through a curated alias map for
          any confirmed duplicate spellings — see `_AGENT_ALIAS_MAP`).
        Plus that case's group's ``group_name``/``case_count``. The report-level
        totals (total_cases, total_groups, group_by_field, report_date) are only at
        the top level of this result, not repeated on every row.

        practice_area / custom_field_filters values match case-insensitively as a
        SUBSTRING (so filtering "Asylum" also matches "Asylum - Affirmative" and
        "Asylum - Defense"). case_stages / exclude_case_stages match case-insensitively
        but EXACTLY (a stage name is a whole discrete value, not something to
        substring-match — "CLOSED" must not also pull in "CLOSED WITH BALANCE") —
        resolve the real stage strings via get_case_stages() first; don't guess them.

        Date filters (all optional, YYYY-MM-DD, inclusive): `opened_after`/
        `opened_before` filter on the case's opened_date; `closed_after`/
        `closed_before` on closed_date; `updated_after`/`updated_before` on the
        case's updated_at (updated_after is ALSO used as a server-side pre-filter
        to reduce pages fetched, same as get_invoices_by_date's pattern — safe
        because updated_at is never earlier than the case's own opened_date).
        MyCase has no server-side filter for opened_date/closed_date at all, and no
        exact/upper-bound filter for updated_at either — all of this is computed
        client-side in Python, same reliability pattern as everything else here.
        """
        name_to_id, id_to_name = await self._custom_field_maps()
        staff_names = await self._staff_name_map()

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
        # case_cap is sized to the REAL total case count (see _case_scan_cap) —
        # confirmed live this account has 7,216 cases, well past the old fixed
        # 5000-case guess that used to silently truncate this exact walk.
        case_cap = await self._case_scan_cap(max_cases, status=status)

        async def fetch_case_page(token: str | None) -> dict[str, Any]:
            return await self.get_cases(
                status=status, updated_after=updated_after, page_size=1000, page_token=token,
                field_client="id,first_name,last_name",
            )

        # _walk_all_pages (not a hand-rolled loop) reports `truncated` correctly
        # via page_token, not a len(...) >= cap comparison — see search_cases'
        # identical comment for why that naive comparison is now wrong essentially
        # every time, since case_cap equals the real total for most firms.
        all_cases, case_walk_truncated = await self._walk_all_pages(fetch_case_page, case_cap)

        opened_after_d, opened_before_d = self._date_only(opened_after), self._date_only(opened_before)
        closed_after_d, closed_before_d = self._date_only(closed_after), self._date_only(closed_before)
        updated_before_d = self._date_only(updated_before)
        updated_after_d = self._date_only(updated_after)

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
            if (opened_after_d or opened_before_d) and not self._date_matches(
                case.get("opened_date"), None, opened_after_d, opened_before_d
            ):
                return False
            if (closed_after_d or closed_before_d) and not self._date_matches(
                case.get("closed_date"), None, closed_after_d, closed_before_d
            ):
                return False
            if (updated_after_d or updated_before_d) and not self._date_matches(
                case.get("updated_at"), None, updated_after_d, updated_before_d
            ):
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
            its real display name — so a report never shows a raw nested blob. The
            PROCESSING AGENT field specifically gets an extra "(original)" column
            alongside a cleaned version of the main column — see
            `_normalize_agent_name` for exactly what "cleaned" means (whitespace
            collapsing + a curated alias map; never re-casing or guessing)."""
            for cfv in case.get("custom_field_values") or []:
                cfid = (cfv.get("custom_field") or {}).get("id")
                name = id_to_name.get(cfid) if isinstance(cfid, int) else None
                col = name or f"custom_field_{cfid}"
                if col in row:  # avoid clobbering a same-named builtin field
                    col = f"{col} (custom field)"
                raw_val = cfv.get("value")
                if name and name.strip().lower() == "processing agent":
                    row[f"{col} (original)"] = raw_val
                    row[col] = self._normalize_agent_name(raw_val)
                else:
                    row[col] = raw_val

        def resolve_client_name(case: dict[str, Any]) -> str:
            names = []
            for cl in case.get("clients") or []:
                full = " ".join(p for p in (cl.get("first_name"), cl.get("last_name")) if p)
                if full:
                    names.append(full)
            return ", ".join(names) if names else "(none)"

        def resolve_assigned_attorney(case: dict[str, Any]) -> str:
            for s in case.get("staff") or []:
                if s.get("lead_lawyer") is True:
                    sid = s.get("id")
                    return staff_names.get(sid, f"staff #{sid}") if isinstance(sid, int) else "(unassigned)"
            return "(unassigned)"

        items: list[dict[str, Any]] = []
        for c in survivors:
            g = group_value(c)
            row = {k: v for k, v in c.items() if k != "custom_field_values"}
            expand_custom_fields(c, row)
            row["client_name"] = resolve_client_name(c)
            row["assigned_attorney"] = resolve_assigned_attorney(c)
            row["group_name"] = g
            row["case_count"] = counts[g]
            items.append(row)

        display_items = items[:limit] if limit is not None else items

        if include_invoices and display_items:
            try:
                async def fetch_invoice_page(token: str | None) -> dict[str, Any]:
                    # only_allowed_online_payments=False is required here — MyCase's
                    # own server-side default is True (returns ONLY invoices with
                    # online payments enabled), which would silently drop every
                    # invoice that has online payments turned off. A user asking for
                    # "a case's invoices" means ALL of them, not that subset.
                    return await self.get_invoices(
                        page_size=1000, page_token=token, only_allowed_online_payments=False,
                    )

                invoice_cap = await self._invoice_scan_cap(None, only_allowed_online_payments=False)
                all_invoices, invoices_truncated = await self._walk_all_pages(fetch_invoice_page, invoice_cap)
                invoices_by_case: dict[int, list[dict[str, Any]]] = {}
                for inv in all_invoices:
                    cid = (inv.get("case") or {}).get("id")
                    if not isinstance(cid, int):
                        continue
                    # Confirmed live against a real account: total_amount/paid_amount
                    # can come back as STRINGS (e.g. "500.0") despite MyCase's own
                    # docs typing them as "number" — a bare `total - paid` then raises
                    # TypeError and (before the try/except above existed) took the
                    # whole case report down with it. _as_float tolerates str/int/
                    # float/None uniformly.
                    total = self._as_float(inv.get("total_amount"))
                    paid = self._as_float(inv.get("paid_amount"))
                    invoices_by_case.setdefault(cid, []).append({
                        "id": inv.get("id"),
                        "invoice_number": inv.get("invoice_number"),
                        "status": inv.get("status"),
                        "invoice_date": inv.get("invoice_date"),
                        "due_date": inv.get("due_date"),
                        "total_amount": total,
                        "paid_amount": paid,
                        "balance_due": total - paid,
                        "label": f"{inv.get('invoice_number') or inv.get('id')} (${total:.2f}, {inv.get('status')})",
                    })
                for row in display_items:
                    row_invoices = invoices_by_case.get(row.get("id"), [])
                    row["invoice_count"] = len(row_invoices)
                    row["outstanding_invoice_total"] = sum(i["balance_due"] for i in row_invoices)
                    row["invoices"] = row_invoices
                if invoices_truncated:
                    for row in display_items:
                        row["invoices_note"] = (
                            f"Invoice scan stopped early ({invoice_cap}-invoice cap) — some "
                            "invoices may be missing."
                        )
            except Exception as exc:  # noqa: BLE001
                # A slow/failed firm-wide invoice scan must NOT take the whole case
                # report down with it (the case data is still valid and useful on
                # its own) — same graceful-degradation pattern as
                # find_cases_with_documents' per-case fetch guard above.
                logger.warning("aggregate_cases_invoices_fetch_failed", error=str(exc))
                for row in display_items:
                    row["invoices_error"] = (
                        f"Could not fetch invoices for this report: {exc}. "
                        "Case data above is still complete and accurate."
                    )

        return {
            "success": True,
            "total_cases": total_cases,
            "total_groups": total_groups,
            "group_by_field": group_field_label,
            "report_date": report_date,
            "cases_scanned": len(all_cases),
            "truncated": case_walk_truncated,
            "cases_shown": len(display_items),
            "limited": limit is not None and len(items) > len(display_items),
            "items": display_items,
        }

    async def search_cases(
        self,
        query: str,
        status: str | None = None,
        max_cases: int | None = None,
    ) -> dict[str, Any]:
        """Find cases by a loose text query — MyCase's API has NO server-side search
        or filter for case_number or case name (confirmed: get_cases only documents
        filter[status] and filter[updated_after]), so a request like "find the case
        numbered/named X" has no targeted endpoint to call. This walks every page of
        get_cases server-side (same pattern as aggregate_cases).

        max_cases defaults to the REAL total case count (via a cheap probe — see
        _case_scan_cap), not a fixed guess — confirmed live that a hardcoded 5000
        cap silently made a genuinely existing, correctly-spelled case invisible
        to search on an account with 7,216 real cases, no matter how the query
        was phrased, because the case was simply never in the scanned batch.

        If ``query`` is an EXACT case_number match (case-insensitive) for one or more
        cases, ONLY those are returned — a case's display ``name`` often embeds a
        padded reference number (e.g. name "01597-Smith" vs a DIFFERENT case whose
        real case_number happens to also contain "1597"), so when the user gave an
        exact number, an exact case_number match is unambiguous and must win over any
        coincidental name substring hit. When there is no exact case_number/id match,
        this tries the query as ONE whole substring against case_number OR name. If
        THAT also finds nothing — e.g. a natural-language description like "ASYLUM
        Case for MOISE PIERRE" — it falls back to multi-word matching: filler words
        (case, matter, for, the, a, an, of, and, in, on, re) are stripped, and a case
        matches if EVERY remaining significant word appears somewhere in
        case_number + name + practice_area + that case's CASE-TYPE-classification
        custom field value(s) (see `_CASE_TYPE_CUSTOM_FIELD_NAMES`), any order, each
        word independently. That custom field is included at THIS tier only, and
        deliberately as a NAMED ALLOWLIST rather than every custom field on the case
        — confirmed live: a firm's case named e.g. "01639-Moise Pierre
        MOISE PIERRE-IMMIGRATION" has its actual case TYPE — "Asylum" — living only
        in a "CASE TYPE" custom field value, never in the name string at all, so a
        query like "asylum case for moise pierre" could never match it without
        checking that field. But checking EVERY custom field was tried first and
        produced a real false positive: an unrelated case's "PROCESSING AGENT" field
        happened to be "Jean-Baptiste Saint-Cyr (Moise)" (a staff nickname), which
        alone made "moise" match a case that had nothing to do with Moise Pierre.
        Restricting to a small, curated set of actual case-classification fields
        avoids that whole class of coincidental hits from free-text/personnel
        fields (agent names, reviewer notes, deadlines, etc.).
        Requires 2+ significant words for this tier (a single leftover word after
        stripping fillers falls through to plain substring behavior only — too weak
        a signal to safely broaden on its own).
        """
        q = (query or "").strip().lower()
        if not q:
            raise ValueError("query must not be empty")

        case_cap = await self._case_scan_cap(max_cases, status=status)

        async def fetch_page(token: str | None) -> dict[str, Any]:
            return await self.get_cases(status=status, page_size=1000, page_token=token)

        # _walk_all_pages (not a hand-rolled loop) is deliberate here: it reports
        # `truncated` correctly by checking page_token directly, whereas comparing
        # len(all_cases) >= case_cap after the fact (the old inline pattern) falsely
        # says "truncated" whenever the real total happens to land exactly on the
        # cap — which, now that case_cap IS the real total for any firm under the
        # safety ceiling, is essentially every successful complete scan.
        all_cases, truncated = await self._walk_all_pages(fetch_page, case_cap)

        # A purely numeric query could be either the case_number OR the case's own
        # internal numeric id — check both before falling back to a substring match.
        exact = [c for c in all_cases if (c.get("case_number") or "").strip().lower() == q]
        if not exact and q.isdigit():
            exact = [c for c in all_cases if str(c.get("id")) == q]
        if exact:
            matched = exact
        else:
            def matches(c: dict[str, Any]) -> bool:
                return q in (c.get("case_number") or "").lower() or q in (c.get("name") or "").lower()

            matched = [c for c in all_cases if matches(c)]
            if not matched:
                words = [w for w in re.split(r"\W+", q) if w and w not in self._SEARCH_STOPWORDS]
                if len(words) >= 2:
                    _, id_to_name = await self._custom_field_maps()

                    def multi_word_matches(c: dict[str, Any]) -> bool:
                        return all(w in self._case_search_haystack(c, id_to_name) for w in words)

                    matched = [c for c in all_cases if multi_word_matches(c)]
        return {
            "success": True,
            "query": query,
            "cases_scanned": len(all_cases),
            "truncated": truncated,
            "match_count": len(matched),
            "items": matched,
        }

    # Curated allowlist of custom field NAMES (case-insensitive) that classify what
    # KIND of case this is — the only custom fields search_cases' multi-word fallback
    # will read. Deliberately narrow: broader attempts (searching every custom field)
    # produced a real false positive via a "PROCESSING AGENT" field whose value
    # happened to contain a staff nickname that collided with a client's first name.
    _CASE_TYPE_CUSTOM_FIELD_NAMES = {"case type", "matter type", "practice type"}

    @classmethod
    def _case_search_haystack(cls, case: dict[str, Any], id_to_name: dict[int, str]) -> str:
        """case_number + name + practice_area + case-type custom field value(s),
        lowercased — the search surface for search_cases' multi-word fallback tier."""
        parts = [case.get("case_number") or "", case.get("name") or "", case.get("practice_area") or ""]
        for cfv in case.get("custom_field_values") or []:
            cfid = (cfv.get("custom_field") or {}).get("id")
            fname = id_to_name.get(cfid, "") if isinstance(cfid, int) else ""
            if fname.strip().lower() in cls._CASE_TYPE_CUSTOM_FIELD_NAMES:
                val = cfv.get("value")
                if val is not None:
                    parts.append(str(val))
        return " ".join(parts).lower()

    _SEARCH_STOPWORDS = {"a", "an", "and", "the", "for", "of", "in", "on", "case", "matter", "re"}

    async def get_case_invoices(
        self, case_id: int | None = None, case_query: str | None = None,
        only_allowed_online_payments: bool | None = None, max_invoices: int | None = None,
    ) -> dict[str, Any]:
        """All invoices for ONE case — resolved by an exact `case_id`, or by a loose
        `case_query` (case_number, internal id, or name — same resolution logic as
        search_cases). MyCase's /invoices endpoint has no server-side case filter,
        so this walks every invoice page and matches `item.case.id` client-side —
        but ONLY once the case itself is confirmed to be a single, real match.

        `max_invoices` defaults to the firm's REAL total invoice count (via a cheap
        probe — see `_invoice_scan_cap`), not a fixed guess — a hardcoded cap
        silently misses this case's invoices whenever they happen to sit past that
        cutoff in a firm with more invoices than the guess. Pass an explicit value
        to override (e.g. to bound a very large firm more aggressively).

        `only_allowed_online_payments` defaults to False here (fetch ALL of the
        case's invoices) — NOT MyCase's own server default of True, which silently
        returns only invoices that have online payments enabled. This tool's whole
        point is "ALL invoices for this case"; a caller who genuinely wants only the
        online-payable subset can still pass True explicitly.

        If `case_query` resolves to ZERO cases, this returns immediately with
        `matched_case: null` and an EMPTY items[] WITHOUT ever calling /invoices —
        a real incident showed the model falling back to scanning up to 1000
        invoices firm-wide "just in case" after a failed case search, burning
        tokens on a guaranteed-empty result and confusing the user with an
        irrelevant "found 1,000 invoices in the system" aside. If `case_query`
        resolves to MORE than one case, this also returns without touching
        invoices — `candidates` lists them so the caller can ask the user which one
        is meant before fetching anything.
        """
        if case_id is None and not (case_query or "").strip():
            raise ValueError("Provide either case_id or case_query.")

        resolved_id = case_id
        matched_case: dict[str, Any] | None = None

        if resolved_id is None:
            search = await self.search_cases(case_query)
            items = search.get("items", [])
            if len(items) == 0:
                return {
                    "success": True,
                    "matched_case": None,
                    "candidates": [],
                    "note": (
                        f"No case found matching '{case_query}' — invoices were NOT searched "
                        "(that would be a pointless firm-wide scan with a guaranteed-empty, "
                        "misleading result). Try searching by the client's name instead, or ask "
                        "the user for the exact case id or case number."
                    ),
                    "items": [],
                }
            if len(items) > 1:
                return {
                    "success": True,
                    "matched_case": None,
                    "candidates": [
                        {"id": c.get("id"), "case_number": c.get("case_number"), "name": c.get("name")}
                        for c in items
                    ],
                    "note": f"'{case_query}' matched {len(items)} cases — ask the user which one before fetching invoices.",
                    "items": [],
                }
            matched_case = items[0]
            resolved_id = matched_case.get("id")

        effective_online_filter = only_allowed_online_payments if only_allowed_online_payments is not None else False

        async def fetch_page(token: str | None) -> dict[str, Any]:
            return await self.get_invoices(
                only_allowed_online_payments=effective_online_filter, page_size=1000, page_token=token,
            )

        invoice_cap = await self._invoice_scan_cap(max_invoices, only_allowed_online_payments=effective_online_filter)
        all_invoices, truncated = await self._walk_all_pages(fetch_page, invoice_cap)
        matched_invoices = [inv for inv in all_invoices if (inv.get("case") or {}).get("id") == resolved_id]

        return {
            "success": True,
            "matched_case": (
                {"id": matched_case.get("id"), "case_number": matched_case.get("case_number"), "name": matched_case.get("name")}
                if matched_case else {"id": resolved_id}
            ),
            "candidates": [],
            "invoices_scanned": len(all_invoices),
            "truncated": truncated,
            "items": matched_invoices,
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
mycase_rest = MyCaseREST()
