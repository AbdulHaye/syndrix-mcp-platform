"""Tests for the retry/timeout budget in MyCaseREST._request.

Flagged from the hosted logs as repeated "/clients attempt 1 / 2 / 3" lines: the
retry loop had no overall ceiling, so ONE call could run 4 attempts x 30s plus
1+2+4s of backoff — ~127s, measured 135.2s live against /clients, which 504s
reliably on this account. Anything with a read timeout in front (nginx defaults
to 60s) gives up long before that, so the caller never sees a real answer.

These use a fake transport so they run in milliseconds and never touch MyCase.
"""
from __future__ import annotations

import time

import httpx
import pytest

from app.services.mycase_rest import MyCaseREST


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


def _rest(monkeypatch, *, status: int | None = None, raises: Exception | None = None):
    """A MyCaseREST whose HTTP layer always returns `status` (or raises)."""
    rest = MyCaseREST()

    async def _headers():
        return {"Authorization": "Bearer test"}

    rest._headers = _headers  # type: ignore[method-assign]

    calls: list[float] = []

    class _FakeClient:
        def __init__(self, *a, **kw):
            self.timeout = kw.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **kw):
            calls.append(self.timeout if isinstance(self.timeout, (int, float)) else 0.0)
            if raises is not None:
                raise raises
            return httpx.Response(status, request=httpx.Request("GET", "http://x"), json={})

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    return rest, calls


@pytest.mark.anyio
async def test_a_gateway_timeout_is_retried_once_not_three_times(monkeypatch):
    """A 504 means MyCase's own gateway already burned a full timeout, so each
    retry costs another one for an error that is rarely transient."""
    rest, calls = _rest(monkeypatch, status=504)
    with pytest.raises(RuntimeError, match="504"):
        await rest._request("GET", "/clients")
    assert len(calls) == rest._MAX_GATEWAY_TIMEOUT_ATTEMPTS == 2


@pytest.mark.anyio
async def test_rate_limiting_still_gets_the_full_retry_run(monkeypatch):
    """429 is cheap and genuinely transient — it must keep all its retries."""
    rest, calls = _rest(monkeypatch, status=429)
    with pytest.raises(RuntimeError, match="429"):
        await rest._request("GET", "/cases")
    assert len(calls) == 4, "initial attempt + 3 retries"


@pytest.mark.anyio
async def test_each_attempt_is_capped_by_the_per_attempt_timeout(monkeypatch):
    rest, calls = _rest(monkeypatch, status=503)
    with pytest.raises(RuntimeError):
        await rest._request("GET", "/cases")
    assert all(rest._MIN_ATTEMPT_SECONDS <= t <= rest._ATTEMPT_TIMEOUT_SECONDS for t in calls)


@pytest.mark.anyio
async def test_the_attempt_timeout_shrinks_as_the_budget_runs_out(monkeypatch):
    """The last attempt must not be free to run a full timeout past the deadline —
    otherwise the real total overshoots the budget it was supposed to respect."""
    rest, calls = _rest(monkeypatch, status=503)
    rest._TOTAL_BUDGET_SECONDS = 8.0  # smaller than the 25s per-attempt timeout
    with pytest.raises(RuntimeError):
        await rest._request("GET", "/cases")
    assert calls, "at least one attempt was made"
    assert max(calls) <= rest._TOTAL_BUDGET_SECONDS, (
        "no single attempt may be allowed to run longer than the whole budget"
    )


@pytest.mark.anyio
async def test_the_whole_call_is_bounded_in_wall_clock_time(monkeypatch):
    """Backoff sleeps are skipped once the budget is spent, so a failing endpoint
    cannot keep a caller waiting indefinitely."""
    rest, _ = _rest(monkeypatch, status=502)
    rest._TOTAL_BUDGET_SECONDS = 3.0  # keep the test fast; the logic is the same
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        await rest._request("GET", "/cases")
    assert time.monotonic() - started < 6.0


@pytest.mark.anyio
async def test_a_read_timeout_is_reported_as_a_timeout_not_a_connection_failure(monkeypatch):
    """"Could not connect" sends you looking at the network; the connection was
    fine, the endpoint just never answered."""
    rest, _ = _rest(monkeypatch, raises=httpx.ReadTimeout("timed out"))
    with pytest.raises(RuntimeError) as exc:
        await rest._request("GET", "/clients")
    assert "did not respond" in str(exc.value)
    assert "/clients" in str(exc.value)
    assert "Could not connect" not in str(exc.value)


@pytest.mark.anyio
async def test_a_real_connection_failure_still_says_so(monkeypatch):
    rest, _ = _rest(monkeypatch, raises=httpx.ConnectError("refused"))
    with pytest.raises(RuntimeError, match="Could not connect"):
        await rest._request("GET", "/cases")


@pytest.mark.anyio
async def test_a_healthy_call_returns_immediately_with_no_retries(monkeypatch):
    rest, calls = _rest(monkeypatch, status=200)
    resp = await rest._request("GET", "/cases")
    assert resp.status_code == 200
    assert len(calls) == 1, "no retry overhead on the happy path"


@pytest.mark.anyio
async def test_a_client_error_is_not_retried(monkeypatch):
    """404/422 are permanent — retrying them just wastes the budget."""
    rest, calls = _rest(monkeypatch, status=404)
    with pytest.raises(RuntimeError, match="404"):
        await rest._request("GET", "/cases/1")
    assert len(calls) == 1
