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
from datetime import date
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

    async def _validate_token(self, token: str) -> tuple[bool | None, str | None]:
        """One cheap real call to prove the stored token is actually accepted.

        Returns (valid, error): True/None when it works, False/reason when MyCase
        rejects it, and (None, reason) when we genuinely can't tell — a network
        blip must never be reported to the user as "your token is dead".

        Cached briefly because the UI polls connection_status for its countdown;
        without the cache this would add an API call every few seconds.
        """
        cached = self._token_check_cache.get(token)
        if cached and time.time() - cached[0] < self._TOKEN_CHECK_TTL:
            return cached[1], cached[2]

        valid: bool | None
        error: str | None
        try:
            await self.get_me()
            valid, error = True, None
        except RuntimeError as exc:
            message = str(exc)
            if "401" in message or "403" in message:
                valid, error = False, message[:200]
            else:
                # 5xx / rate limit / anything else — unknown, not "invalid".
                valid, error = None, message[:200]
        except Exception as exc:  # noqa: BLE001
            valid, error = None, str(exc)[:200]

        self._token_check_cache = {token: (time.time(), valid, error)}
        return valid, error

    _TOKEN_CHECK_TTL = 60  # seconds
    _token_check_cache: dict[str, tuple[float, bool | None, str | None]] = {}

    async def connection_status(self, validate: bool = True) -> dict[str, Any]:
        """Richer than is_connected() — a real incident showed the UI's "MyCase
        connected" badge staying up unchanged even after the stored token had
        actually expired, because is_connected() only checks that SOME token
        string is present, never whether it's still valid. This exposes the real
        expiry (when known) so the UI can show a live countdown / an honest
        "expired" state instead of a static badge that can silently lie.

        The stored expiry alone is NOT enough, though — confirmed live: a
        manually-pasted token has no stored expiry at all, so this reported
        `connected: true, expired: false` for hours while every single MyCase
        call was returning 401. `validate=True` therefore makes one cheap,
        60s-cached real API call so `token_valid` reflects what MyCase actually
        thinks, not just whether a string is present in the database.

        Does NOT trigger a refresh — the real refresh already happens
        transparently in _get_valid_token() before any actual MyCase API call.
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

        token_valid: bool | None = None
        token_error: str | None = None
        if token and validate:
            token_valid, token_error = await self._validate_token(token)

        return {
            "connected": bool(token),
            # What MyCase itself says, as opposed to what our settings table
            # implies: True = a real call just succeeded, False = rejected
            # (reconnect needed), None = not checked or genuinely unknown.
            "token_valid": token_valid,
            "token_error": token_error,
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

    # Retry/timeout budget for a SINGLE MyCase call, including all of its retries.
    # Tunable, but keep the total meaningfully below whatever read timeout sits in
    # front of the app (nginx's default proxy_read_timeout is 60s) — one slow call
    # must never be able to consume the whole request's deadline on its own.
    _ATTEMPT_TIMEOUT_SECONDS = 25.0
    # 45s, not 60s: connection setup/teardown adds a few seconds per attempt that the
    # in-loop clock doesn't see, and the last attempt may run its full timeout. A 60s
    # budget measured 64.8s end to end — still over nginx's 60s default, which is the
    # thing this is meant to stay under. 45s lands around 50s worst case.
    _TOTAL_BUDGET_SECONDS = 45.0
    _MIN_ATTEMPT_SECONDS = 5.0
    _MAX_GATEWAY_TIMEOUT_ATTEMPTS = 2  # a 504 already cost an upstream timeout

    async def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        headers = await self._headers()
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        # Retries cover THREE things: rate limiting (429), transient SERVER-side errors
        # (502/503/504 — confirmed live: /clients 504'd "Endpoint request timed out" on this
        # account even at page_size=1, reproduced twice — MyCase's own gateway, not a client
        # timeout, since a real response with a status code came back) — both checked via
        # status code after a response comes back — AND transient network failures
        # (httpx.TransportError — e.g. the "All connection attempts failed" ConnectError
        # class — which raises INSTEAD OF returning a response, so it needs its own
        # try/except; a bare status-code check after the request call never runs if the
        # request itself raised).
        #
        # RETRIES ARE BUDGETED. Without a ceiling the retry loop could run far longer
        # than anything waiting on it: 4 attempts x 30s + 1+2+4s of backoff is ~127s
        # for ONE call, and /clients on this account reliably 504s, so it burned the
        # full ~135s (measured) before failing anyway. A single agent turn makes many
        # such calls, so a turn could sail past a reverse proxy's read timeout
        # (nginx's default is 60s) and the browser would get a 504 with no response —
        # flagged from the hosted logs as repeated "/clients attempt 1/2/3" lines.
        # _TOTAL_BUDGET_SECONDS caps the whole thing; per-attempt timeouts shrink to
        # whatever budget is left, so the caller gets a real error in bounded time.
        #
        # 504 is special: it means MyCase's OWN gateway already timed out, so each
        # retry costs another full upstream timeout for an error that is rarely
        # transient. It gets one retry, not three.
        _RETRYABLE_STATUS = {429, 502, 503, 504}
        _RETRY_DELAYS = [1, 2, 4]  # seconds between retries (rate limit is 25 req/s per client)
        started = time.monotonic()

        def _remaining() -> float:
            return self._TOTAL_BUDGET_SECONDS - (time.monotonic() - started)

        resp: httpx.Response | None = None
        gateway_timeouts = 0
        last_error: str | None = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                if _remaining() <= delay:
                    logger.warning(
                        "mycase_retry_budget_exhausted", path=path, attempt=attempt,
                        elapsed=round(time.monotonic() - started, 1),
                    )
                    break
                logger.warning("mycase_retry", path=path, attempt=attempt, wait=delay)
                await asyncio.sleep(delay)

            attempt_timeout = max(self._MIN_ATTEMPT_SECONDS, min(self._ATTEMPT_TIMEOUT_SECONDS, _remaining()))
            try:
                async with httpx.AsyncClient(timeout=attempt_timeout) as client:
                    resp = await client.request(method, f"{_API_BASE}{path}", headers=headers, params=clean)
            except httpx.TransportError as exc:
                last_error = str(exc)
                if attempt < len(_RETRY_DELAYS) and _remaining() > 0:
                    continue
                # "Could not connect" is wrong and misleading for a READ timeout —
                # the connection was fine, MyCase just never answered in time. That
                # distinction is what tells you whether to look at the network or at
                # the endpoint being slow.
                timed_out = isinstance(exc, httpx.TimeoutException)
                took = time.monotonic() - started
                logger.error(
                    "mycase_timeout" if timed_out else "mycase_connection_error",
                    path=path, detail=last_error, elapsed=round(took, 1), attempts=attempt + 1,
                )
                raise RuntimeError(
                    f"MyCase's API did not respond within {took:.0f}s ({attempt + 1} attempt(s)) "
                    f"for {path} — the endpoint is timing out, not refusing the connection."
                    if timed_out else
                    f"Could not connect to MyCase's API after {attempt + 1} attempt(s) in "
                    f"{took:.0f}s: {exc}"
                ) from exc

            if resp.status_code not in _RETRYABLE_STATUS:
                break
            if resp.status_code == 504:
                gateway_timeouts += 1
                if gateway_timeouts > self._MAX_GATEWAY_TIMEOUT_ATTEMPTS - 1:
                    logger.warning(
                        "mycase_gateway_timeout_giving_up", path=path, attempts=attempt + 1,
                        elapsed=round(time.monotonic() - started, 1),
                    )
                    break
            if attempt >= len(_RETRY_DELAYS) or _remaining() <= 0:
                break
        assert resp is not None
        elapsed = time.monotonic() - started
        if resp.status_code >= 400:
            message = self._extract_error_message(resp)
            logger.warning(
                "mycase_api_error", path=path, status=resp.status_code, message=message,
                elapsed=round(elapsed, 1),
            )
            code_text = self._ERROR_CODE_TEXT.get(resp.status_code, "")
            detail = f"{message} ({code_text})" if code_text and message else (message or code_text or f"HTTP {resp.status_code}")
            # The elapsed time belongs in the error: a 504 after 60s is a very
            # different operational problem from a 404 after 0.2s, and the agent
            # surfaces this string to the user.
            raise RuntimeError(f"MyCase API {resp.status_code} after {elapsed:.0f}s: {detail}")
        if elapsed > self._ATTEMPT_TIMEOUT_SECONDS:
            logger.warning("mycase_slow_request", path=path, elapsed=round(elapsed, 1))
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

    async def _client_scan_cap(self, requested: int | None) -> int:
        """Same idea, for the firm-wide client walk in aggregate_clients/
        find_duplicate_clients. The /clients endpoint has been observed to
        return HTTP 504 ("Endpoint request timed out") on this account even at
        page_size=1 — _request() now retries 502/503/504 automatically (see its
        _RETRYABLE_STATUS), but a very large client list may still be slow or
        fail; callers should report that plainly rather than assume zero
        clients exist."""
        if requested is not None:
            return requested
        probe = await self.get_clients(page_size=1)
        cap, ok = self._cap_from_probe(requested, probe)
        if not ok:
            logger.warning("mycase_scan_cap_probe_missing_item_count", resource="clients", probe_keys=list(probe.keys()))
        return cap

    async def _lead_scan_cap(self, requested: int | None) -> int:
        """Same idea, for the firm-wide lead walk in aggregate_leads."""
        if requested is not None:
            return requested
        probe = await self.get_leads(page_size=1)
        cap, ok = self._cap_from_probe(requested, probe)
        if not ok:
            logger.warning("mycase_scan_cap_probe_missing_item_count", resource="leads", probe_keys=list(probe.keys()))
        return cap

    async def _payment_scan_cap(self, requested: int | None) -> int:
        """Same idea, for the firm-wide invoice-payments walk in aggregate_payments.
        Confirmed live: this real account has 9,314 total invoice payments."""
        if requested is not None:
            return requested
        probe = await self.get_invoice_payments(page_size=1)
        cap, ok = self._cap_from_probe(requested, probe)
        if not ok:
            logger.warning("mycase_scan_cap_probe_missing_item_count", resource="invoice_payments", probe_keys=list(probe.keys()))
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

    # Grouping clients by their own identifier can never produce a group larger
    # than 1, so it is always a mistake — and a specific, recurring one: asked for
    # "clients who have more than one case", the model twice reached for
    # aggregate_clients(group_by="id") instead of counting CASES per client. The
    # count it wants does not exist on a client record at all. Caught before the
    # (slow, 504-prone) /clients walk and turned into a corrective error.
    _CLIENT_IDENTITY_FIELDS = {"id", "uuid", "client_id"}

    async def aggregate_clients(
        self,
        created_after: str | None = None,
        created_before: str | None = None,
        group_by: str | None = None,
        min_group_size: int | None = None,
        max_group_size: int | None = None,
        max_clients: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Fetch every client (contact) matching the given filters (paginating
        internally, server-side, same deterministic pattern as aggregate_cases)
        then group/count. NOTE: /clients has been observed to return HTTP 504
        even at page_size=1 on this large a real account — _request() retries
        502/503/504 automatically, but this call may still be slow or fail on a
        very large client list; report the real error plainly if it does,
        rather than assuming zero clients exist.

        `created_after`/`created_before` (YYYY-MM-DD, inclusive) filter on the
        client's own created_at — use this for "contacts created this month/
        this week/on X"; MyCase has no server-side filter for this.
        `group_by` — any field name present on a client record (e.g. "status")
        — a plain lookup, grouped/counted the same way as aggregate_cases.
        Returns the same shape: total_clients, total_groups, a `groups` array
        ([{name, count}], sorted highest-count-first) when group_by is given,
        and a flat `items` array.

        `min_group_size`/`max_group_size` — HAVING on group size, as elsewhere
        (e.g. group_by="email", min_group_size=2 to find shared email addresses).
        NOTE this counts CLIENTS PER GROUP, never cases per client — see the
        identity-field guard below for why that distinction keeps coming up.
        """
        # Rejected before the slow, 504-prone /clients walk rather than after it.
        if group_by and group_by.strip().lower() in self._CLIENT_IDENTITY_FIELDS:
            raise ValueError(
                f"Grouping clients by '{group_by}' is meaningless — that field is unique per "
                "client, so every group would contain exactly one row. If you are looking for "
                "CLIENTS WHO HAVE MORE THAN ONE CASE, that is a cases question, not a clients "
                "question: a client record carries no case count. Call "
                "aggregate_cases(group_by='client_name', min_group_size=2) instead. To find "
                "clients sharing a value (e.g. the same email), group by that field, or use "
                "find_duplicate_clients."
            )

        client_cap = await self._client_scan_cap(max_clients)

        async def fetch_page(token: str | None) -> dict[str, Any]:
            return await self.get_clients(page_size=1000, page_token=token)

        all_clients, truncated = await self._walk_all_pages(fetch_page, client_cap)

        created_after_d, created_before_d = self._date_only(created_after), self._date_only(created_before)

        def matches(cl: dict[str, Any]) -> bool:
            if (created_after_d or created_before_d) and not self._date_matches(
                cl.get("created_at"), None, created_after_d, created_before_d
            ):
                return False
            return True

        survivors = [cl for cl in all_clients if matches(cl)]

        group_key = group_by.strip() if group_by else None
        groups: list[dict[str, Any]] | None = None
        counts: dict[str, int] = {}
        groups_before_size_filter = 0
        if group_key:
            for cl in survivors:
                val = cl.get(group_key)
                g = str(val) if val not in (None, "") else "(none)"
                cl["group_name"] = g
                counts[g] = counts.get(g, 0) + 1

            groups_before_size_filter = len(counts)
            if min_group_size is not None or max_group_size is not None:
                keep = {
                    g for g, n in counts.items()
                    if (min_group_size is None or n >= min_group_size)
                    and (max_group_size is None or n <= max_group_size)
                }
                survivors = [cl for cl in survivors if cl.get("group_name") in keep]
                counts = {g: n for g, n in counts.items() if g in keep}

            groups = [{"name": n, "count": c} for n, c in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]

        display_items = survivors[:limit] if limit is not None else survivors

        return {
            "success": True,
            "total_clients": len(survivors),
            "total_groups": len(counts) if group_key else None,
            "group_by_field": group_key,
            "groups": groups,
            "clients_scanned": len(all_clients),
            "truncated": truncated,
            "clients_shown": len(display_items),
            "limited": limit is not None and len(survivors) > len(display_items),
            "items": display_items,
            **(
                {"group_size_filter": {"min": min_group_size, "max": max_group_size},
                 "groups_before_size_filter": groups_before_size_filter}
                if group_key and (min_group_size is not None or max_group_size is not None)
                else {}
            ),
        }

    async def find_duplicate_clients(self, max_clients: int | None = None) -> dict[str, Any]:
        """Firm-wide duplicate-contact scan by email AND by phone (cell/home/
        work) — MyCase has no server-side "find duplicates" endpoint, so this
        walks every client ONCE (same dynamic-scan-cap pattern as
        aggregate_cases) and groups by normalized email / normalized phone in
        Python, returning ONLY groups with more than one client. Never let the
        model eyeball a firm-wide contact list for this itself — at any real
        scale it will miss matches or mismatch entries.

        Email is normalized by lowercasing + stripping whitespace. Phone
        numbers are normalized by stripping all non-digit characters (so
        "(555) 123-4567" and "555-123-4567" are recognized as the same number);
        fewer than 7 remaining digits is treated as unset/invalid and excluded.
        A blank/missing email or phone is excluded from that dimension's
        grouping — an empty string is never treated as a "duplicate".
        """
        client_cap = await self._client_scan_cap(max_clients)

        async def fetch_page(token: str | None) -> dict[str, Any]:
            return await self.get_clients(page_size=1000, page_token=token)

        all_clients, truncated = await self._walk_all_pages(fetch_page, client_cap)

        def norm_email(cl: dict[str, Any]) -> str | None:
            e = (cl.get("email") or "").strip().lower()
            return e or None

        def norm_phones(cl: dict[str, Any]) -> list[str]:
            phones = []
            for key in ("cell_phone_number", "home_phone_number", "work_phone_number"):
                digits = re.sub(r"\D", "", cl.get(key) or "")
                if len(digits) >= 7:
                    phones.append(digits)
            return phones

        def label(cl: dict[str, Any]) -> dict[str, Any]:
            name = " ".join(p for p in (cl.get("first_name"), cl.get("last_name")) if p) or f"client #{cl.get('id')}"
            return {
                "id": cl.get("id"), "name": name, "email": cl.get("email"),
                "cell_phone_number": cl.get("cell_phone_number"),
                "home_phone_number": cl.get("home_phone_number"),
                "work_phone_number": cl.get("work_phone_number"),
            }

        by_email: dict[str, list[dict[str, Any]]] = {}
        by_phone: dict[str, list[dict[str, Any]]] = {}
        for cl in all_clients:
            e = norm_email(cl)
            if e:
                by_email.setdefault(e, []).append(label(cl))
            for p in norm_phones(cl):
                by_phone.setdefault(p, []).append(label(cl))

        email_dupes = [{"email": k, "count": len(v), "clients": v} for k, v in by_email.items() if len(v) > 1]
        phone_dupes = [{"phone": k, "count": len(v), "clients": v} for k, v in by_phone.items() if len(v) > 1]
        email_dupes.sort(key=lambda d: d["count"], reverse=True)
        phone_dupes.sort(key=lambda d: d["count"], reverse=True)

        return {
            "success": True,
            "clients_scanned": len(all_clients),
            "truncated": truncated,
            "duplicate_email_groups": len(email_dupes),
            "duplicate_phone_groups": len(phone_dupes),
            "by_email": email_dupes,
            "by_phone": phone_dupes,
        }

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

    # What each entity actually returns, with the aliases people say out loud.
    # Kept deliberately small: only fields whose NAME does not give away what they
    # are, or that are computed rather than returned by MyCase. Everything else is
    # discovered live below — a hardcoded field list would go stale silently, and
    # custom fields differ per firm (46 on this account).
    _ENTITY_FIELD_GUIDE: dict[str, dict[str, str]] = {
        "cases": {
            "sol_date": "Statute-of-limitations date. A REAL native case field, not a custom "
                        "field — say 'SOL', 'SOL date' or 'statute of limitations'.",
            "opened_date": "When the matter was opened (NOT when the record was created).",
            "created_at": "When the case RECORD was created — use this for 'recently created'.",
            "closed_date": "When the matter was closed.",
            "case_stage": "The firm's workflow stage. Resolve real values via get_case_stages().",
            "status": "'open' or 'closed'. Independent of case_stage — they can disagree.",
            "practice_area": "Area of law. Real values via get_practice_areas().",
            "billing_type": "How the matter is billed.",
            "outstanding_balance": "Amount still owed on the case.",
            "assigned_attorney": "COMPUTED, not a MyCase field: the staff member flagged "
                                 "lead_lawyer=true on the case. Say 'lead attorney'/'assigned attorney'.",
            "client_name": "COMPUTED from the case's clients array.",
            "days_to_close": "COMPUTED gap between opened_date and closed_date; only present "
                             "when days_to_close_min/max was used.",
        },
        "leads": {
            "status": "Firm-defined pre-intake status, matched EXACTLY. Real values seen here: "
                      "'NEED FOLLOW-UP', 'New Lead', 'Need consultation', 'UNDECIDED', 'NOT FOUND YET'.",
            "case": "Link to the case this lead became, once converted. A lead has no attorney "
                    "of its own — it is resolved through this case.",
            "created_at": "When the lead was created.",
        },
        "invoices": {
            "balance_due": "COMPUTED total_amount - paid_amount.",
            "total_amount": "Invoice total. Sometimes returned as a STRING by MyCase.",
            "paid_amount": "Amount paid so far. Also sometimes a string.",
            "status": "overdue, paid, partial, draft, unsent, sent or forwarded.",
            "case": "Only a {id} link. An invoice has NO client or attorney field — both are "
                    "resolved through this case.",
            "invoice_date": "Date of the invoice itself (unrelated to created_at/updated_at).",
            "due_date": "When payment is due.",
        },
        "payments": {
            "attorney": "Present DIRECTLY on a payment (unlike invoices) — no join needed.",
            "client": "Present directly on a payment.",
            "case": "Present directly on a payment.",
            "amount": "Payment amount.",
            "date": "When the payment was taken.",
            "status": "e.g. success, pending, failure.",
        },
        "clients": {
            "created_at": "When the contact record was created.",
            "email": "Primary email.",
            "cell_phone_number": "Mobile number; formats vary, normalise before comparing.",
        },
        "staff": {
            "type": "Job type as free text (e.g. 'Lawyer'). NOT a permissions role.",
            "title": "Job title as free text. NOT a permissions role.",
            "active": "Whether the staff member is active.",
            "default_hourly_rate": "Default billing rate.",
        },
    }
    # Entities MyCase exposes no custom fields for — asked for anyway, we should say so.
    _CUSTOM_FIELD_PARENTS = {"case": "cases", "client": "clients", "company": "companies"}

    async def describe_entity_fields(self, entity: str | None = None) -> dict[str, Any]:
        """What fields exist on an entity, including this firm's own custom fields.

        Exists because the agent could not answer "cases with a missing SOL date":
        nothing told it the field existed or what it was called, so it had no way
        to get there from the user's wording. That is a general failure mode, not
        a one-off — every firm has different custom fields.

        Custom fields are read LIVE (never hardcoded): this account has 46, and
        they differ per firm, so a static list would quietly go out of date.
        """
        wanted = (entity or "").strip().lower().rstrip("s") if entity else None
        alias = {"case": "cases", "lead": "leads", "prospect": "leads", "invoice": "invoices",
                 "payment": "payments", "client": "clients", "contact": "clients",
                 "staff": "staff", "attorney": "staff", "company": "companies"}
        key = alias.get(wanted, f"{wanted}s") if wanted else None
        if key and key not in self._ENTITY_FIELD_GUIDE and key != "companies":
            raise ValueError(
                f"Unknown entity '{entity}'. Known: {sorted(self._ENTITY_FIELD_GUIDE)}."
            )

        custom_by_parent: dict[str, list[dict[str, Any]]] = {}
        for field in await self._load_custom_fields():
            parent = self._CUSTOM_FIELD_PARENTS.get(str(field.get("parent_type") or "").lower())
            if parent:
                custom_by_parent.setdefault(parent, []).append(
                    {"name": field.get("name"), "type": field.get("field_type")}
                )

        entities = [key] if key else sorted(self._ENTITY_FIELD_GUIDE)
        out: dict[str, Any] = {}
        for name in entities:
            out[name] = {
                "notable_fields": self._ENTITY_FIELD_GUIDE.get(name, {}),
                "custom_fields": custom_by_parent.get(name, []),
                "custom_fields_supported": name in self._CUSTOM_FIELD_PARENTS.values(),
            }
        return {
            "success": True,
            "entities": out,
            "note": (
                "Custom field names are read live and are exact — pass them verbatim as a "
                "custom_field_filters key or group_by value. Fields marked COMPUTED are "
                "calculated by these tools, not returned by MyCase, but can still be "
                "filtered/grouped like real ones. MyCase exposes NO role or permission data "
                "for staff at any level."
            ),
        }

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
        group_by: str | None = None,
        min_group_size: int | None = None,
        max_group_size: int | None = None,
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

        `min_group_size`/`max_group_size` (require `group_by`) — HAVING on the
        group's own size, same as aggregate_cases: "clients with more than one
        unpaid invoice" is group_by="client_name", paid=False, min_group_size=2.

        `group_by` (optional) — "client_name"/"client", "assigned_attorney"/
        "lead_attorney", or "status". An invoice itself only carries `case:
        {"id": N}` — no client or attorney field directly — so client_name/
        assigned_attorney are resolved via ONE bulk walk of the firm's case list
        (same case-join pattern as aggregate_cases/aggregate_leads, never a
        separate call per invoice) that's only done when group_by actually
        needs it. Adds a top-level `groups` array — [{name, count,
        total_balance_due}], sorted by total_balance_due descending — and a
        `group_name` column on every row. Use this for "unpaid invoices grouped
        by client" or "invoices by assigned attorney" — do NOT try to answer
        "by assigned attorney" some other way; an invoice's own `case` field
        with no join is not enough, and guessing here has produced a wrong/
        hallucinated answer before.
        """
        if status is not None and status.strip().lower() not in self._INVOICE_STATUSES:
            raise ValueError(f"status must be one of {sorted(self._INVOICE_STATUSES)}, got {status!r}")
        if sort_by not in ("balance_due", "due_date", "invoice_date"):
            raise ValueError("sort_by must be one of 'balance_due', 'due_date', 'invoice_date'")
        _GROUP_KEYS = {"client_name", "client", "assigned_attorney", "lead_attorney", "lead attorney", "status"}
        group_key = group_by.strip().lower() if group_by else None
        if group_key is not None and group_key not in _GROUP_KEYS:
            raise ValueError(f"'{group_by}' is not a valid group_by for invoices — use one of {sorted(_GROUP_KEYS)}.")
        group_by_case_field = group_key in ("client_name", "client", "assigned_attorney", "lead_attorney", "lead attorney")

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

        groups: list[dict[str, Any]] | None = None
        if group_key is not None:
            case_join: dict[int, dict[str, str]] = {}
            if group_by_case_field:
                staff_names = await self._staff_name_map()
                case_cap = await self._case_scan_cap(None)

                async def fetch_case_page(token: str | None) -> dict[str, Any]:
                    return await self.get_cases(page_size=1000, page_token=token, field_client="id,first_name,last_name")

                all_cases_for_join, _ = await self._walk_all_pages(fetch_case_page, case_cap)
                for case in all_cases_for_join:
                    cid = case.get("id")
                    if not isinstance(cid, int):
                        continue
                    names = [
                        " ".join(p for p in (cl.get("first_name"), cl.get("last_name")) if p)
                        for cl in (case.get("clients") or [])
                    ]
                    client_name = ", ".join(n for n in names if n) or "(none)"
                    attorney = None
                    for s in case.get("staff") or []:
                        if s.get("lead_lawyer") is True:
                            sid = s.get("id")
                            attorney = staff_names.get(sid, f"staff #{sid}") if isinstance(sid, int) else None
                            break
                    case_join[cid] = {"client_name": client_name, "assigned_attorney": attorney or "(unassigned)"}

            def group_value(r: dict[str, Any]) -> str:
                if group_key == "status":
                    return r.get("status") or "(no status)"
                cid = (r.get("case") or {}).get("id")
                if not isinstance(cid, int):
                    return "(no case linked)"
                join = case_join.get(cid)
                if join is None:
                    # NOT a gap in our walk — confirmed live that the case list is
                    # complete (7,247 of 7,247, untruncated) and that these ids
                    # return a real 404 from MyCase: the invoice outlived the case
                    # it points at. Labelled so it can never be mistaken for a
                    # client's name in a "top clients" list.
                    return "(case deleted in MyCase)"
                return join["client_name"] if group_key in ("client_name", "client") else join["assigned_attorney"]

            # The resolved value must land on the ROW under a real column name, not
            # just in `group_name`. An invoice row has no client/attorney field of
            # its own, so without this a workbook built with split_by=
            # "assigned_attorney" finds nothing to split on and collapses 8 real
            # attorney groups into a single "(none)" sheet — confirmed live.
            resolved_column = (
                "client_name" if group_key in ("client_name", "client")
                else "assigned_attorney" if group_key in ("assigned_attorney", "lead_attorney", "lead attorney")
                else None  # "status" is already a real column on the invoice
            )

            counts: dict[str, int] = {}
            sums: dict[str, float] = {}
            for r in survivors:
                g = group_value(r)
                r["group_name"] = g
                if resolved_column:
                    r[resolved_column] = g
                counts[g] = counts.get(g, 0) + 1
                sums[g] = sums.get(g, 0.0) + r["balance_due"]

            # HAVING on group size — same primitive as aggregate_cases, e.g.
            # "clients with more than one unpaid invoice".
            groups_before_size_filter = len(counts)
            if min_group_size is not None or max_group_size is not None:
                keep = {
                    g for g, n in counts.items()
                    if (min_group_size is None or n >= min_group_size)
                    and (max_group_size is None or n <= max_group_size)
                }
                survivors = [r for r in survivors if r.get("group_name") in keep]
                counts = {g: n for g, n in counts.items() if g in keep}
                sums = {g: v for g, v in sums.items() if g in keep}
                total_matching = len(survivors)
                display = survivors[:limit] if limit is not None else survivors

            unresolved = sum(
                n for g, n in counts.items()
                if g in ("(no case linked)", "(case deleted in MyCase)")
            )
            groups = [
                {"name": name, "count": counts[name], "total_balance_due": round(sums[name], 2)}
                for name in sorted(counts, key=lambda n: sums[n], reverse=True)
            ]

        return {
            "success": True,
            "total_invoices": total_matching,
            "invoices_scanned": len(all_invoices),
            "truncated": truncated,
            "invoices_shown": len(display),
            "limited": limit is not None and len(display) < total_matching,
            "sort_by": sort_by,
            "group_by_field": group_key,
            "groups": groups,
            "items": display,
            **(
                {"group_size_filter": {"min": min_group_size, "max": max_group_size},
                 "groups_before_size_filter": groups_before_size_filter}
                if group_key is not None and (min_group_size is not None or max_group_size is not None)
                else {}
            ),
            **(
                {"unresolved_client_invoices": unresolved,
                 "unresolved_note": (
                     f"{unresolved} invoice(s) could not be matched to a client because the case "
                     "they belong to no longer exists in MyCase (confirmed 404). They are grouped "
                     "under a '(...)' placeholder — report them as unmatched, never as a client name."
                 )}
                if group_key is not None and group_by_case_field and unresolved
                else {}
            ),
        }

    async def get_invoice_payments(self, payable_id=None, status=None, page_size=None, page_token=None):
        params = self._page(page_size, page_token)
        if payable_id is not None:
            params["filter[payable_id]"] = payable_id
        if status is not None:
            params["filter[status]"] = status
        return await self._get_list("/invoice_payments", params)

    async def aggregate_payments(
        self,
        status: str | None = None,
        case_id: int | None = None,
        date_after: str | None = None,
        date_before: str | None = None,
        group_by: str | None = None,
        min_group_size: int | None = None,
        max_group_size: int | None = None,
        max_payments: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Fetch every invoice payment matching the given filters (paginating
        internally, server-side, same deterministic pattern as aggregate_cases —
        confirmed live this account has 9,314 total invoice payments, each
        already carrying `attorney`, `case`, `client`, and `invoice` as direct
        {"id": N} refs) then group/count/sum. This exists because get_invoice_payments()
        with no filters already returns everything firm-wide but has no
        aggregation of its own — a raw dump that size is exactly the kind of
        thing this whole reporting layer was built to avoid handing the model.

        `status` — exact match on MyCase's own payment status (e.g. "success",
        "pending", "failure") — passed straight through to get_invoice_payments'
        server-side filter[status], so it also narrows the page walk itself.
        `case_id` — pass this for "payment history for case X"; payments already
        carry `case: {"id": N}` directly (confirmed live), no join needed.
        get_case_payments() is a thin wrapper around exactly this.
        `date_after`/`date_before` (YYYY-MM-DD, inclusive) filter on the
        payment's own `date` — MyCase has no server-side filter for this.
        `group_by` — "attorney", "client", "case", or "status" (default).
        Attorney is resolved via one staff lookup (same as aggregate_cases).
        Client/case are resolved via a per-id lookup covering only the DISTINCT
        ids present in the already-filtered result — deliberately NOT the
        firm-wide /clients list endpoint, which is unreliable at this account's
        scale (see _request's 5xx-retry notes); a single get_client()/get_case()
        lookup is a different, much cheaper endpoint.

        Returns total_payments, total_amount (sum of `amount` across every
        surviving payment, coerced via _as_float — some real accounts return
        this as a string), and a `groups` array ([{name, count, total_amount}],
        sorted by total_amount descending) alongside the flat `items` array —
        read `groups` directly for a breakdown/"which attorney/client" question,
        don't re-sum `items` yourself.
        """
        payment_cap = await self._payment_scan_cap(max_payments)

        async def fetch_page(token: str | None) -> dict[str, Any]:
            return await self.get_invoice_payments(status=status, page_size=1000, page_token=token)

        all_payments, truncated = await self._walk_all_pages(fetch_page, payment_cap)

        date_after_d, date_before_d = self._date_only(date_after), self._date_only(date_before)

        def matches(p: dict[str, Any]) -> bool:
            if case_id is not None and (p.get("case") or {}).get("id") != case_id:
                return False
            if (date_after_d or date_before_d) and not self._date_matches(
                p.get("date"), None, date_after_d, date_before_d
            ):
                return False
            return True

        survivors = [p for p in all_payments if matches(p)]

        group_key = (group_by or "status").strip().lower()
        if group_key not in ("attorney", "client", "case", "status"):
            raise ValueError(
                f"'{group_by}' is not a valid group_by for payments — use 'attorney', 'client', "
                "'case', or 'status'."
            )

        staff_names = await self._staff_name_map() if group_key == "attorney" else {}
        name_cache: dict[int, str] = {}

        async def resolve_name(ref: dict[str, Any] | None, kind: str) -> str:
            rid = (ref or {}).get("id") if isinstance(ref, dict) else None
            if not isinstance(rid, int):
                return "(unknown)"
            if kind == "attorney":
                return staff_names.get(rid, f"staff #{rid}")
            if rid in name_cache:
                return name_cache[rid]
            try:
                if kind == "client":
                    obj = await self.get_client(rid)
                    name = " ".join(p for p in (obj.get("first_name"), obj.get("last_name")) if p) or f"client #{rid}"
                else:
                    obj = await self.get_case(rid)
                    name = obj.get("name") or obj.get("case_number") or f"case #{rid}"
            except Exception as exc:  # noqa: BLE001
                logger.warning("aggregate_payments_name_resolve_failed", kind=kind, id=rid, error=str(exc))
                name = f"{kind} #{rid}"
            name_cache[rid] = name
            return name

        group_names: list[str] = []
        for p in survivors:
            if group_key == "status":
                group_names.append(p.get("status") or "(no status)")
            elif group_key == "attorney":
                group_names.append(await resolve_name(p.get("attorney"), "attorney"))
            elif group_key == "client":
                group_names.append(await resolve_name(p.get("client"), "client"))
            else:
                group_names.append(await resolve_name(p.get("case"), "case"))

        counts: dict[str, int] = {}
        sums: dict[str, float] = {}
        for g in group_names:
            counts[g] = counts.get(g, 0) + 1

        # HAVING on group size — same primitive as aggregate_cases, e.g. "clients
        # who have made more than 3 payments". Applied BEFORE the totals are
        # summed so total_payments/total_amount describe the filtered set.
        groups_before_size_filter = len(counts)
        if min_group_size is not None or max_group_size is not None:
            keep = {
                g for g, n in counts.items()
                if (min_group_size is None or n >= min_group_size)
                and (max_group_size is None or n <= max_group_size)
            }
            kept = [(p, g) for p, g in zip(survivors, group_names) if g in keep]
            survivors = [p for p, _ in kept]
            group_names = [g for _, g in kept]
            counts = {g: n for g, n in counts.items() if g in keep}

        total_amount = 0.0
        for p, g in zip(survivors, group_names):
            amt = self._as_float(p.get("amount"))
            sums[g] = sums.get(g, 0.0) + amt
            total_amount += amt

        groups = [
            {"name": name, "count": counts[name], "total_amount": round(sums[name], 2)}
            for name in sorted(counts, key=lambda n: sums[n], reverse=True)
        ]

        # Same reasoning as aggregate_invoices: put the resolved name on the row
        # under a real column, so a workbook can actually split by it.
        payment_column = {
            "attorney": "attorney_name", "client": "client_name", "case": "case_name",
        }.get(group_key)

        items: list[dict[str, Any]] = []
        for p, g in zip(survivors, group_names):
            row = dict(p)
            row["group_name"] = g
            if payment_column:
                row[payment_column] = g
            items.append(row)

        display_items = items[:limit] if limit is not None else items

        return {
            "success": True,
            "total_payments": len(survivors),
            "total_amount": round(total_amount, 2),
            "total_groups": len(counts),
            "group_by_field": group_key,
            "groups": groups,
            "payments_scanned": len(all_payments),
            "truncated": truncated,
            "payments_shown": len(display_items),
            "limited": limit is not None and len(items) > len(display_items),
            "items": display_items,
        }

    async def get_case_payments(self, case_id: int) -> dict[str, Any]:
        """Payment history for ONE case. Payments already carry `case: {"id": N}`
        directly (confirmed live) — this is a thin, obviously-named wrapper
        around aggregate_payments rather than a separate invoice->payment join,
        so the model has a real, exact-match tool to call for "payment history
        for case X" instead of fabricating one that doesn't exist."""
        return await self.aggregate_payments(case_id=int(case_id), group_by="status")

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

    async def aggregate_leads(
        self,
        status: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        assigned_attorney: str | None = None,
        group_by: str | None = None,
        min_group_size: int | None = None,
        max_group_size: int | None = None,
        max_leads: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Fetch every lead matching the given filters (paginating internally,
        server-side, same deterministic pattern as aggregate_cases — never let
        the model eyeball/count raw leads itself) then group and count.

        `status` — EXACT match, case-insensitive. A lead's status is a literal
        firm-defined string (confirmed live: "NEED FOLLOW-UP", "New Lead", "Need
        consultation", "UNDECIDED", "NOT FOUND YET" all exist as real values in
        this account) — do NOT substring-match it the way custom_field_filters
        does elsewhere; "NEED FOLLOW-UP" and "New Lead" share no useful substring.
        Use status="NEED FOLLOW-UP" for "prospects/leads that need follow-up".

        `created_after`/`created_before` (YYYY-MM-DD, inclusive) filter on the
        lead's own created_at. MyCase has no server-side filter for this — same
        client-side pattern as everywhere else here.

        `assigned_attorney` — a lead has NO attorney field of its own. Once
        converted it carries a `case: {"id": N}` link, and THAT case may have a
        staff member flagged lead_lawyer=true (same convention as
        aggregate_cases' assigned_attorney). Pass "" for "prospects/leads with NO
        assigned attorney" (covers a lead with no linked case at all, and a lead
        whose linked case has no lead_lawyer set) — or a substring to match a
        specific attorney's resolved name. Resolving this walks the firm's case
        list ONCE (only when assigned_attorney/group_by="assigned_attorney" is
        actually requested) to build a case_id -> attorney map, the same bulk-
        fetch-once-then-compute-in-Python pattern as everywhere else here —
        deliberately NOT one get_case() call per lead, which would mean one HTTP
        round-trip per DISTINCT case and get slower the more leads match.

        `group_by` — "status" (default) or "assigned_attorney"/"lead_attorney".
        Returns the same shape as aggregate_cases: a flat `items` array plus a
        top-level `groups` array ([{name, count}], sorted highest-count-first) —
        read `groups` directly for a breakdown/"which X has the most" question,
        don't re-count `items` yourself.
        """
        lead_cap = await self._lead_scan_cap(max_leads)

        async def fetch_lead_page(token: str | None) -> dict[str, Any]:
            return await self.get_leads(page_size=1000, page_token=token)

        all_leads, leads_truncated = await self._walk_all_pages(fetch_lead_page, lead_cap)

        created_after_d, created_before_d = self._date_only(created_after), self._date_only(created_before)
        want_status = status.strip().lower() if status else None

        def status_and_date_match(lead: dict[str, Any]) -> bool:
            if want_status is not None and (lead.get("status") or "").strip().lower() != want_status:
                return False
            if (created_after_d or created_before_d) and not self._date_matches(
                lead.get("created_at"), None, created_after_d, created_before_d
            ):
                return False
            return True

        survivors = [ld for ld in all_leads if status_and_date_match(ld)]

        group_key = (group_by or "status").strip().lower()
        if group_key not in ("status", "assigned_attorney", "lead_attorney", "lead attorney"):
            raise ValueError(f"'{group_by}' is not a valid group_by for leads — use 'status' or 'assigned_attorney'.")
        group_by_attorney = group_key != "status"
        need_attorney = group_by_attorney or assigned_attorney is not None

        case_attorney_map: dict[int, str | None] = {}
        if need_attorney:
            # One bulk case walk, not one get_case() per lead — same
            # fetch-once-then-compute-in-Python pattern as aggregate_cases.
            staff_names = await self._staff_name_map()
            case_cap = await self._case_scan_cap(None)

            async def fetch_case_page(token: str | None) -> dict[str, Any]:
                return await self.get_cases(page_size=1000, page_token=token)

            all_cases_for_join, _ = await self._walk_all_pages(fetch_case_page, case_cap)
            for case in all_cases_for_join:
                cid = case.get("id")
                if not isinstance(cid, int):
                    continue
                attorney = None
                for s in case.get("staff") or []:
                    if s.get("lead_lawyer") is True:
                        sid = s.get("id")
                        attorney = staff_names.get(sid, f"staff #{sid}") if isinstance(sid, int) else None
                        break
                case_attorney_map[cid] = attorney

        def resolve_attorney(lead: dict[str, Any]) -> str | None:
            case_ref = lead.get("case") or {}
            cid = case_ref.get("id") if isinstance(case_ref, dict) else None
            return case_attorney_map.get(cid) if isinstance(cid, int) else None

        resolved_attorneys = [resolve_attorney(ld) for ld in survivors]

        if assigned_attorney is not None:
            want_attorney = assigned_attorney.strip().lower()
            kept, kept_attorneys = [], []
            for lead, attorney in zip(survivors, resolved_attorneys):
                if not want_attorney:
                    if attorney:
                        continue
                elif not attorney or want_attorney not in attorney.lower():
                    continue
                kept.append(lead)
                kept_attorneys.append(attorney)
            survivors, resolved_attorneys = kept, kept_attorneys

        def group_value(lead: dict[str, Any], attorney: str | None) -> str:
            return (attorney or "(unassigned)") if group_by_attorney else (lead.get("status") or "(no status)")

        counts: dict[str, int] = {}
        for lead, attorney in zip(survivors, resolved_attorneys):
            counts[group_value(lead, attorney)] = counts.get(group_value(lead, attorney), 0) + 1

        # HAVING on group size — same primitive as aggregate_cases.
        groups_before_size_filter = len(counts)
        if min_group_size is not None or max_group_size is not None:
            keep = {
                g for g, n in counts.items()
                if (min_group_size is None or n >= min_group_size)
                and (max_group_size is None or n <= max_group_size)
            }
            kept = [
                (lead, attorney) for lead, attorney in zip(survivors, resolved_attorneys)
                if group_value(lead, attorney) in keep
            ]
            survivors = [ld for ld, _ in kept]
            resolved_attorneys = [a for _, a in kept]
            counts = {g: n for g, n in counts.items() if g in keep}

        groups = [{"name": name, "count": n} for name, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]

        items: list[dict[str, Any]] = []
        for lead, attorney in zip(survivors, resolved_attorneys):
            row = dict(lead)
            row["assigned_attorney"] = attorney or "(unassigned)"
            row["group_name"] = group_value(lead, attorney)
            row["lead_count"] = counts[row["group_name"]]
            items.append(row)

        display_items = items[:limit] if limit is not None else items

        return {
            "success": True,
            "total_leads": len(survivors),
            "total_groups": len(counts),
            "group_by_field": "assigned_attorney" if group_by_attorney else "status",
            "groups": groups,
            "leads_scanned": len(all_leads),
            "truncated": leads_truncated,
            "leads_shown": len(display_items),
            "limited": limit is not None and len(items) > len(display_items),
            "items": display_items,
        }

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

    _BUILTIN_CASE_FIELDS = {"practice_area", "case_stage", "status", "billing_type", "sol_date"}

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
    # as real duplicate spellings are found in the data.
    #
    # Confirmed live against the firm's actual Processing Agent data (1,425 cases):
    # these are first-name-only entries that always correlate to the same Case
    # Manager/office as their full-name counterpart, and were confirmed by the firm
    # to be the same person. "Jean-Baptiste Saint-Cyr (Moise)", "Fedna Delerme",
    # "Marissa Marcellus", "Lucnise Regis", and "Past Agent" are left OUT of this map
    # deliberately — they're already-canonical firm-confirmed agent names with no
    # variant to merge. "Flooddy" and "Stephanie" are also left out — they don't
    # match any name on the firm's confirmed agent list and were NOT to be
    # force-merged into a guess.
    _AGENT_ALIAS_MAP: dict[str, str] = {
        "angelo": "Angelo Bazin",
        "alexandra": "Alexandra Jean Charles",
        "jean": "Jean Cadet",
        "marjorie": "Marjorie Francois",
        "madjelida": "Madjelida Macirus",
    }

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
        created_after: str | None = None,
        created_before: str | None = None,
        sol_date_after: str | None = None,
        sol_date_before: str | None = None,
        days_to_close_min: int | None = None,
        days_to_close_max: int | None = None,
        min_group_size: int | None = None,
        max_group_size: int | None = None,
        max_cases: int | None = None,
        limit: int | None = None,
        include_invoices: bool = False,
    ) -> dict[str, Any]:
        """Fetch every case matching the given filters (paginating internally,
        server-side) — including case_stages (keep ONLY cases in one of these exact
        stages, if given) and excluding exclude_case_stages — then group the survivors
        by ``group_by`` (a builtin field name — practice_area/case_stage/status/
        billing_type — "assigned_attorney"/"Lead Attorney" (the staff member flagged
        lead_lawyer=true on the case, NOT a real MyCase field but computed here
        specifically so it can be filtered/grouped like one) — or a CUSTOM FIELD
        NAME, e.g. "PROCESSING AGENT") and count per group. custom_field_filters
        also accepts "assigned_attorney"/"Lead Attorney" as a key the same way,
        including the empty-string "no value" convention (see below) for "cases
        with no Lead Attorney assigned". Returns a flat, report-ready ``items`` array —
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
        "Asylum - Defense") — EXCEPT an empty string ("") as a custom_field_filters
        value, which means the opposite: "this field is blank/unassigned" (e.g.
        custom_field_filters={"PROCESSING AGENT": ""} for "cases with no Processing
        Agent"). case_stages / exclude_case_stages match case-insensitively
        but EXACTLY (a stage name is a whole discrete value, not something to
        substring-match — "CLOSED" must not also pull in "CLOSED WITH BALANCE") —
        resolve the real stage strings via get_case_stages() first; don't guess them.

        Date filters (all optional, YYYY-MM-DD, inclusive): `opened_after`/
        `opened_before` filter on the case's opened_date; `closed_after`/
        `closed_before` on closed_date; `created_after`/`created_before` on the
        case's own `created_at` (use this for "recently created"/"created this
        month" cases — NOT opened_date, which is a separate, case-management
        concept a case can carry even if it wasn't newly created); `sol_date_after`/
        `sol_date_before` on `sol_date` — MyCase's own native statute-of-limitations
        date field on a case (confirmed live; it is NOT a custom field, so it does
        not go through custom_field_filters' name lookup — pass it as a plain param
        here instead); `updated_after`/`updated_before` on the case's updated_at
        (updated_after is ALSO used as a server-side pre-filter to reduce pages
        fetched, same as get_invoices_by_date's pattern — safe because updated_at is
        never earlier than the case's own opened_date). MyCase has no server-side
        filter for opened_date/closed_date/created_at/sol_date at all, and no
        exact/upper-bound filter for updated_at either — all of this is computed
        client-side in Python, same reliability pattern as everything else here.

        `sol_date` is also a valid `group_by`/`custom_field_filters` key like any
        other builtin field — `custom_field_filters={"sol_date": ""}` finds cases
        with NO sol_date set at all (the standard empty-string-means-blank
        convention documented above), for "cases with a missing SOL date".

        `min_group_size` / `max_group_size` filter on HOW BIG EACH GROUP IS — a SQL
        HAVING clause, not a WHERE clause. This is the ONLY correct way to answer
        "clients who have more than one case" (group_by="client_name",
        min_group_size=2), "attorneys with at least 10 open cases"
        (group_by="assigned_attorney", status="open", min_group_size=10), or
        "practice areas with only one case" (max_group_size=1). Do NOT try to
        answer those by grouping everything and picking out the big groups
        yourself — confirmed live that this returns every matching case (2,380
        rows) and gets the answer wrong. Groups outside the range are dropped
        ENTIRELY: their cases disappear from `items`, from `groups`, and from
        `total_cases`, so every number in the result describes the same filtered
        set. `groups_before_size_filter` reports how many groups existed before
        the filter, so a narrow result is never mistaken for a small dataset.

        `days_to_close_min` / `days_to_close_max` — for a DURATION question about a
        SINGLE case's own opened_date vs closed_date ("cases closed within 1
        month/30 days of opening", "cases that took longer than 90 days to close",
        "cases resolved in under a week") — do NOT try to approximate this with
        opened_after/opened_before/closed_after/closed_before: those are independent
        ABSOLUTE date-range floors/ceilings across the whole matching set, not a
        per-case gap between two of that SAME case's own fields, and combining them
        cannot express "this case's own two dates were close together" (confirmed:
        an earlier attempt at this used opened_after/opened_before/closed_after/
        closed_before together and returned a wrong, coincidental set that had
        nothing to do with any case's actual open-to-close duration). Pass
        days_to_close_max=30 for "within 1 month" (calendar months vary in length,
        so "1 month" is treated as 30 days), days_to_close_min for a floor, or both
        for a range. Only cases with BOTH opened_date and closed_date set are
        considered — a case with no closed_date can't have a close-duration, and is
        excluded from the match rather than silently treated as 0 or infinite days.
        """
        # A field cannot be BLANK and inside a date RANGE at the same time, so this
        # combination always returns zero rows — and zero rows reads as a fact
        # ("there are no open cases with an SOL date in the next 30 days") rather
        # than as a broken query. Confirmed live: asked for SOL dates in the next 30
        # days, the model sent custom_field_filters={"sol_date": ""} together with
        # sol_date_after/before and then reported "no matches" — when the real
        # answer was 92 cases. Rejected up front so the model has to fix the query.
        _RANGE_PARAMS = {
            "sol_date": (sol_date_after, sol_date_before, "sol_date_after/sol_date_before"),
            "opened_date": (opened_after, opened_before, "opened_after/opened_before"),
            "closed_date": (closed_after, closed_before, "closed_after/closed_before"),
            "created_at": (created_after, created_before, "created_after/created_before"),
            "updated_at": (updated_after, updated_before, "updated_after/updated_before"),
        }
        for field, want in (custom_field_filters or {}).items():
            if str(want).strip():
                continue  # only the blank-check convention conflicts with a range
            after, before, param_names = _RANGE_PARAMS.get(field.strip().lower(), (None, None, ""))
            if after or before:
                raise ValueError(
                    f"Contradictory filter: custom_field_filters={{'{field}': ''}} means "
                    f"'{field} is EMPTY', but you also passed {param_names}, which means "
                    f"'{field} falls in a date range'. Nothing can satisfy both, so this would "
                    "always return zero rows and look like a real 'none found' answer. Use the "
                    f"blank check on its own for 'missing {field}', or the range on its own for "
                    f"'{field} within a period' — not both."
                )

        name_to_id, id_to_name = await self._custom_field_maps()
        staff_names = await self._staff_name_map()

        def lead_attorney_raw(case: dict[str, Any]) -> str | None:
            """The staff member flagged lead_lawyer=true on this case, resolved to a
            real name — or None if no staff member is so flagged. This is a COMPUTED
            value (derived from the case's own `staff` array), never a real MyCase
            field or custom field — so it can't go through resolve_field()'s normal
            builtin/custom-field lookup. "assigned_attorney" is special-cased as a
            pseudo-builtin field below specifically so "cases with no Lead Attorney"
            can be filtered/grouped the same way as any other field, instead of the
            group_by/custom_field_filters call failing outright (confirmed live: a
            failed group_by="assigned_attorney" call made the agent silently fall
            back to an UNFILTERED aggregate_cases call and then invent a plausible
            but fabricated count in its reply — the displayed table was actually
            every case in the firm, not the ones lacking a lead attorney)."""
            for s in case.get("staff") or []:
                if s.get("lead_lawyer") is True:
                    sid = s.get("id")
                    return staff_names.get(sid, f"staff #{sid}") if isinstance(sid, int) else None
            return None

        _COMPUTED_CASE_FIELDS = {"assigned_attorney", "lead attorney", "lead_attorney"}
        _CLIENT_NAME_FIELDS = {"client_name", "client name", "client"}

        def resolve_field(field: str) -> tuple[bool, int | str]:
            """Return (is_builtin, key) — key is the builtin field name (or the
            "assigned_attorney"/"client_name" computed-field sentinels), or the
            resolved numeric custom-field id."""
            low = field.strip().lower()
            if low in self._BUILTIN_CASE_FIELDS:
                return True, low
            if low in _COMPUTED_CASE_FIELDS:
                return True, "assigned_attorney"
            if low in _CLIENT_NAME_FIELDS:
                return True, "client_name"
            if low in name_to_id:
                return False, name_to_id[low]
            raise ValueError(
                f"'{field}' is not a builtin case field ({sorted(self._BUILTIN_CASE_FIELDS)}), "
                f"'assigned_attorney'/'Lead Attorney', 'client_name', or a known custom field "
                f"name. Call get_custom_fields() to see valid custom field names."
            )

        def builtin_value(case: dict[str, Any], key: str) -> str | None:
            """case.get(key) for a real builtin field, except the "assigned_attorney"
            and "client_name" sentinels, which are computed instead."""
            if key == "assigned_attorney":
                return lead_attorney_raw(case)
            if key == "client_name":
                names = [
                    " ".join(p for p in (cl.get("first_name"), cl.get("last_name")) if p)
                    for cl in (case.get("clients") or [])
                ]
                return ", ".join(n for n in names if n) or None
            return case.get(key)

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
        created_after_d, created_before_d = self._date_only(created_after), self._date_only(created_before)
        sol_after_d, sol_before_d = self._date_only(sol_date_after), self._date_only(sol_date_before)

        def matches(case: dict[str, Any], apply_field_filters: bool = True) -> bool:
            if practice_area and practice_area.strip().lower() not in (case.get("practice_area") or "").lower():
                return False
            stage = (case.get("case_stage") or "").strip().upper()
            if stage in exclude_set:
                return False
            if include_stage_set and stage not in include_stage_set:
                return False
            for is_builtin, key, want in (filter_specs if apply_field_filters else []):
                actual = builtin_value(case, key) if is_builtin else self._case_custom_value(case, key)  # type: ignore[arg-type]
                actual_str = (actual or "").strip()
                if want == "*":
                    # "Has ANY value" — the missing other half of the blank check.
                    # Until this existed the vocabulary was one-sided: "" meant
                    # "field is blank", and there was NO way to say "field is set".
                    # Asked to "show Criminal cases WITH assigned attorneys", the
                    # model reached for the only attorney idiom it had been taught —
                    # the blank check — and returned exactly the opposite set.
                    if not actual_str:
                        return False
                elif not want:
                    # An empty filter value means "this field is blank/unassigned" —
                    # the natural way to ask for "cases with no Processing Agent",
                    # "missing Case Manager", etc. Previously this branch fell into
                    # the substring check below, where an empty `want` is trivially
                    # `in` every non-blank string (so non-blank cases matched) while
                    # `not actual` short-circuited blank cases OUT — the exact
                    # opposite of "find the blank ones" (confirmed live: a "cases
                    # with no Processing Agent" report came back with 1,425 cases
                    # that all had a real agent assigned, and zero actually blank).
                    if actual_str:
                        return False
                elif not actual_str or want not in actual_str.lower():
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
            if (created_after_d or created_before_d) and not self._date_matches(
                case.get("created_at"), None, created_after_d, created_before_d
            ):
                return False
            if (sol_after_d or sol_before_d) and not self._date_matches(
                case.get("sol_date"), None, sol_after_d, sol_before_d
            ):
                return False
            if days_to_close_min is not None or days_to_close_max is not None:
                opened_d = self._date_only(case.get("opened_date"))
                closed_d = self._date_only(case.get("closed_date"))
                if not opened_d or not closed_d:
                    return False
                try:
                    days = (date.fromisoformat(closed_d) - date.fromisoformat(opened_d)).days
                except ValueError:
                    return False
                if days_to_close_min is not None and days < days_to_close_min:
                    return False
                if days_to_close_max is not None and days > days_to_close_max:
                    return False
            return True

        survivors = [c for c in all_cases if matches(c)]

        # The DENOMINATOR for the field filters. Without it a filtered subset gets
        # narrated as the whole population: asked to show Criminal cases WITH an
        # assigned attorney, the agent filtered to the 49 unassigned ones and
        # reported "All 49 Criminal cases in your system have no assigned attorney"
        # — there are 237, and 188 of them DO have one. The model could not have
        # known better; it only ever saw the filtered count.
        peers_ignoring_field_filters = (
            sum(1 for c in all_cases if matches(c, apply_field_filters=False))
            if filter_specs else len(survivors)
        )

        def group_value(case: dict[str, Any]) -> str:
            if group_is_builtin:
                # Match the label the ROW's own column uses, so the same bucket is
                # never named two different things in one answer. Confirmed live:
                # grouping by assigned_attorney reported "(none): 640" in `groups`
                # while every one of those rows read "(unassigned)" — and an Excel
                # report built from the rows then disagreed with the counts above it.
                empty = "(unassigned)" if group_key == "assigned_attorney" else "(none)"
                return builtin_value(case, group_key) or empty  # type: ignore[arg-type]
            val = self._case_custom_value(case, group_key)  # type: ignore[arg-type]
            return val or "(unassigned)"

        counts: dict[str, int] = {}
        for c in survivors:
            g = group_value(c)
            counts[g] = counts.get(g, 0) + 1

        # Filter on the GROUP'S OWN SIZE (a SQL HAVING clause, not a WHERE clause).
        # Without this there was no way to express "clients who have more than one
        # case" — the closest the agent could do was group everything and try to
        # eyeball which groups were big, which meant dumping all 2,380 rows and
        # getting it wrong (confirmed live). Rows are then narrowed to the
        # surviving groups so items/counts/groups all describe the same set.
        group_size_filtered = min_group_size is not None or max_group_size is not None
        groups_before_size_filter = len(counts)
        if group_size_filtered:
            keep = {
                g for g, n in counts.items()
                if (min_group_size is None or n >= min_group_size)
                and (max_group_size is None or n <= max_group_size)
            }
            survivors = [c for c in survivors if group_value(c) in keep]
            counts = {g: n for g, n in counts.items() if g in keep}

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
            return lead_attorney_raw(case) or "(unassigned)"

        items: list[dict[str, Any]] = []
        for c in survivors:
            g = group_value(c)
            row = {k: v for k, v in c.items() if k != "custom_field_values"}
            expand_custom_fields(c, row)
            row["client_name"] = resolve_client_name(c)
            row["assigned_attorney"] = resolve_assigned_attorney(c)
            row["group_name"] = g
            row["case_count"] = counts[g]
            if days_to_close_min is not None or days_to_close_max is not None:
                # Self-verifying: with the days_to_close_* filter active, show the
                # real computed gap on every surviving row rather than making the
                # user (or the model) trust the filter blindly.
                row["days_to_close"] = (
                    date.fromisoformat(self._date_only(c["closed_date"]))
                    - date.fromisoformat(self._date_only(c["opened_date"]))
                ).days
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

        # A compact, pre-sorted group-count summary — separate from the flat `items`
        # list (which can be large and gets hard-truncated before the model ever
        # sees it, confirmed live: a "which stage has the most cases" question over
        # 7,247 cases in 42 groups got a WRONG answer because the model had to scan
        # a flat, truncatable, arbitrarily-ordered row list to find the largest
        # group itself). This is computed directly from `counts` — no re-scanning.
        groups = [{"name": name, "count": n} for name, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]

        result = {
            "success": True,
            "total_cases": total_cases,
            "total_groups": total_groups,
            "group_by_field": group_field_label,
            "groups": groups,
            "report_date": report_date,
            "cases_scanned": len(all_cases),
            "truncated": case_walk_truncated,
            "cases_shown": len(display_items),
            "limited": limit is not None and len(items) > len(display_items),
            "items": display_items,
        }
        if group_size_filtered:
            # Say plainly that a HAVING filter ran and what it removed, so a small
            # result can never read as "that's all the data there is".
            result["group_size_filter"] = {"min": min_group_size, "max": max_group_size}
            result["groups_before_size_filter"] = groups_before_size_filter
        if filter_specs and peers_ignoring_field_filters != total_cases:
            result["total_ignoring_field_filters"] = peers_ignoring_field_filters
            result["field_filter_note"] = (
                f"{total_cases:,} of {peers_ignoring_field_filters:,} cases matched "
                f"{custom_field_filters!r}. The other "
                f"{peers_ignoring_field_filters - total_cases:,} exist but did not match it — "
                "never describe this result as 'all' or 'every' case, and always state the "
                "filtered count against this total."
            )
        return result

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
