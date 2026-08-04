"""Short-lived storage for generated .xlsx reports, keyed by an opaque id.

A generated workbook is too big to hand back through the chat response (the whole
point of this feature is to STOP shipping megabytes of rows to the browser), so
the agent returns a `report_id` and the browser fetches the bytes from a
dedicated endpoint.

Redis is the primary store because it is the only thing here that is shared
across containers. The fallback is a temp DIRECTORY, not an in-process dict —
that distinction was learned the hard way about ten minutes after this file was
first written: a report built successfully, then 404'd on download. Cause: this
dev box runs Redis 5.0.14, but redis-py 8.x negotiates RESP3 via `HELLO`, which
Redis only understands from 6.0 — so the app's Redis connection fails at startup,
every Redis-backed service silently degrades, and an in-memory report then
evaporated the next time `uvicorn --reload` restarted on a file save. Files on
disk survive a reload, survive multiple workers on one host, and cost nothing.

Note the base64 hop on the Redis path: the app's shared Redis client is created
with `decode_responses=True` (app/main.py), so it returns str, not bytes —
writing raw xlsx through it would corrupt the file. Encoding costs ~33% size for
at most an hour, which is cheaper than a second Redis connection just for this.
"""
from __future__ import annotations

import base64
import json
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_TTL_SECONDS = 3600  # 1 hour — long enough to click Download, short enough to not accumulate
_KEY_PREFIX = "syndrix:report:"
_ID_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def _spool_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "syndrix_reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_id(report_id: str) -> str | None:
    """Reject anything that isn't one of our own generated ids — the id reaches
    here straight from a URL path, so it must never be able to walk the
    filesystem."""
    rid = (report_id or "").strip()
    if not rid or len(rid) > 64 or not set(rid) <= _ID_OK:
        return None
    return rid


class ReportStore:
    def __init__(self) -> None:
        self._redis: Any = None

    def set_redis(self, redis_client: Any) -> None:
        """Injected at startup, same pattern as audit/memory/cache services."""
        self._redis = redis_client

    def _prune_spool(self) -> None:
        cutoff = time.time() - _TTL_SECONDS
        try:
            for path in _spool_dir().glob("*.report"):
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("report_store_prune_failed", error=str(exc))

    async def put(self, payload: bytes, filename: str, team: str) -> str:
        report_id = secrets.token_urlsafe(18)
        record = {
            "filename": filename,
            "team": team,
            "b64": base64.b64encode(payload).decode("ascii"),
        }
        blob = json.dumps(record)

        stored_in_redis = False
        if self._redis is not None:
            try:
                await self._redis.setex(f"{_KEY_PREFIX}{report_id}", _TTL_SECONDS, blob)
                stored_in_redis = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("report_store_redis_write_failed", error=str(exc))
        if not stored_in_redis:
            self._prune_spool()
            try:
                (_spool_dir() / f"{report_id}.report").write_text(blob, encoding="utf-8")
            except OSError as exc:
                logger.error("report_store_write_failed", error=str(exc))
                raise RuntimeError(f"Could not save the generated report: {exc}") from exc

        logger.info("report_stored", report_id=report_id, bytes=len(payload), redis=stored_in_redis)
        return report_id

    async def get(self, report_id: str) -> dict[str, Any] | None:
        """Returns {"payload": bytes, "filename": str, "team": str} or None if the
        id is unknown or its hour has elapsed."""
        rid = _safe_id(report_id)
        if rid is None:
            return None

        record: dict[str, Any] | None = None
        if self._redis is not None:
            try:
                raw = await self._redis.get(f"{_KEY_PREFIX}{rid}")
                if raw:
                    record = json.loads(raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("report_store_redis_read_failed", error=str(exc))

        if record is None:
            path = _spool_dir() / f"{rid}.report"
            try:
                if path.exists() and path.stat().st_mtime >= time.time() - _TTL_SECONDS:
                    record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("report_store_read_failed", report_id=rid, error=str(exc))

        if record is None:
            return None
        return {
            "payload": base64.b64decode(record["b64"]),
            "filename": record.get("filename") or "report.xlsx",
            "team": record.get("team") or "",
        }


report_store = ReportStore()
