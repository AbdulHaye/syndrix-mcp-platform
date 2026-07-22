from __future__ import annotations

import structlog
from langfuse import get_client

logger = structlog.get_logger(__name__)


def get_tracer():
    """Return the singleton Langfuse client.

    Safe to call unconditionally, even when Langfuse isn't configured at all —
    the SDK detects a TRULY ABSENT LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY (or
    LANGFUSE_TRACING_ENABLED=false) and falls back to a real no-op tracer, no
    network calls at all, instead of raising. It never raises either way, so
    every call site in this app can start/annotate spans unconditionally
    without its own "is monitoring configured" branch — but note an env var
    present-and-BLANK (e.g. `LANGFUSE_PUBLIC_KEY=` in a .env file, which
    python-dotenv sets to "" rather than leaving it unset) is NOT the same as
    absent: confirmed directly against this SDK version, a blank (not unset)
    key still attempts a real network call and logs a 401 on every span. Set
    LANGFUSE_TRACING_ENABLED=false explicitly (see .env) to guarantee a true
    no-op while keys are blank.
    """
    return get_client()


def score_current_trace(name: str, value: str, comment: str | None = None) -> None:
    """Attach a CATEGORICAL score (e.g. pass/fail/corrected) to whichever Langfuse
    trace is currently active on this async task.

    Wrapped in try/except so a Langfuse hiccup (network blip, misconfigured
    keys, SDK error) can never take down the actual agent response — monitoring
    is a secondary concern here, never allowed to break the primary feature,
    the same principle already followed elsewhere in this codebase (e.g.
    aggregate_cases' invoice-fetch try/except not taking down the case report).
    """
    try:
        get_tracer().score_current_trace(name=name, value=value, data_type="CATEGORICAL", comment=comment)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracing_score_failed", name=name, error=str(exc))
