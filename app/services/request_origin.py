"""Derives OAuth redirect URLs from the incoming request instead of requiring
manual per-environment configuration.

Podio's OAuth callback (backend) and the "where does the browser land after
Connect" redirect (frontend) both used to be hardcoded to localhost, requiring
someone to hand-type the right absolute URL into Settings for every
environment (local dev, a hosted IP, eventually a real domain). Deriving them
from the request that's actually asking to connect means it's always correct
for whatever host the user is on, with nothing to configure.
"""
from __future__ import annotations

import urllib.parse

from fastapi import Request


def backend_origin(request: Request) -> str:
    """Scheme + host of the backend itself, as the caller's browser sees it.

    Respects X-Forwarded-Proto/X-Forwarded-Host so this stays correct behind a
    reverse proxy that terminates TLS or maps a different external host/port.
    """
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip() or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host", "").split(",")[0].strip()
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{proto}://{host}"


def frontend_origin(request: Request, default: str) -> str:
    """Scheme + host of the frontend page that initiated this request.

    The frontend calls the `/connect` endpoint via `fetch()` from whatever
    page the user is actually on, so its Origin (or Referer, as a fallback)
    header is exactly the frontend's real address — correct for localhost, a
    hosted IP, or a future domain, without any settings to keep in sync.
    """
    origin = request.headers.get("origin")
    if not origin:
        referer = request.headers.get("referer")
        if referer:
            parsed = urllib.parse.urlsplit(referer)
            if parsed.scheme and parsed.netloc:
                origin = f"{parsed.scheme}://{parsed.netloc}"
    return (origin or default).rstrip("/")
