from __future__ import annotations

import asyncio
import logging
from typing import Any

import structlog
from celery import Celery
from celery.utils.log import get_task_logger

from app.config import get_settings

logger = structlog.get_logger(__name__)
task_logger = get_task_logger(__name__)

settings = get_settings()

# ---------------------------------------------------------------------------
# Celery application
# ---------------------------------------------------------------------------
celery_app = Celery(
    "mcp_workers",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    # Retry defaults
    task_default_retry_delay=30,
    task_max_retries=3,
)


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from a synchronous Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _backoff(retries: int, base: int = 30, cap: int = 600) -> int:
    """Exponential backoff: base * 2^retries, capped at `cap` seconds."""
    return min(base * (2 ** retries), cap)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@celery_app.task(name="jobs.sync_crm_contacts", bind=True, max_retries=3)
def sync_crm_contacts(self: Any, team_name: str = "all") -> dict[str, Any]:
    """
    Synchronise CRM contacts from Podio and store summaries in pgvector.
    """
    task_logger.info("sync_crm_contacts started, team=%s", team_name)

    async def _sync() -> dict[str, Any]:
        from app.adapters.podio import podio_adapter
        from app.services.model_gateway import model_gateway
        from app.storage.vector import vector_store

        contacts = await podio_adapter.search_leads({})
        synced = 0
        for contact in contacts:
            item_id = str(contact.get("item_id", contact.get("id", "unknown")))
            title_val = contact.get("title", f"Contact {item_id}")
            summary = f"Contact: {title_val} (id={item_id})"
            try:
                embedding = await model_gateway.embed(summary)
                await vector_store.store(
                    title=summary,
                    source=f"podio/contact/{item_id}",
                    content=summary,
                    embedding=embedding,
                )
                synced += 1
            except Exception as emb_exc:  # noqa: BLE001
                task_logger.warning("embed failed for %s: %s", item_id, emb_exc)

        return {
            "status": "ok",
            "task": "sync_crm_contacts",
            "team": team_name,
            "contacts_found": len(contacts),
            "contacts_synced": synced,
        }

    try:
        result = _run_async(_sync())
        task_logger.info("sync_crm_contacts completed: %s", result)
        return result
    except Exception as exc:  # noqa: BLE001
        task_logger.error("sync_crm_contacts failed: %s", exc)
        raise self.retry(exc=exc, countdown=_backoff(self.request.retries))


@celery_app.task(name="jobs.process_document", bind=True, max_retries=3)
def process_document(self: Any, title: str, content: str, source: str = "manual") -> dict[str, Any]:
    """
    Embed a document and store it in pgvector.
    """
    task_logger.info("process_document started, title=%s", title)

    async def _process() -> dict[str, Any]:
        from app.services.model_gateway import model_gateway
        from app.storage.vector import vector_store

        try:
            embedding = await model_gateway.embed(content)
        except Exception as emb_exc:  # noqa: BLE001
            return {
                "status": "error",
                "task": "process_document",
                "title": title,
                "error": f"Embedding failed: {emb_exc}",
            }

        doc = await vector_store.store(
            title=title,
            source=source,
            content=content,
            embedding=embedding,
        )
        return {
            "status": "ok",
            "task": "process_document",
            "title": title,
            "document_id": str(doc.id),
            "embedding_dims": len(embedding),
        }

    try:
        result = _run_async(_process())
        task_logger.info("process_document completed: %s", result)
        return result
    except Exception as exc:  # noqa: BLE001
        task_logger.error("process_document failed: %s", exc)
        raise self.retry(exc=exc, countdown=_backoff(self.request.retries, base=15))


@celery_app.task(name="jobs.rebuild_vector_index", bind=True)
def rebuild_vector_index(self: Any) -> dict[str, Any]:
    """
    Rebuild the pgvector IVFFlat index on knowledge_documents after bulk inserts.
    """
    task_logger.info("rebuild_vector_index started")

    async def _rebuild() -> dict[str, Any]:
        from app.storage.db import _get_engine
        from sqlalchemy import text

        engine = _get_engine()
        async with engine.begin() as conn:
            await conn.execute(text(
                "DROP INDEX IF EXISTS knowledge_documents_embedding_idx"
            ))
            try:
                await conn.execute(text(
                    "CREATE INDEX knowledge_documents_embedding_idx "
                    "ON knowledge_documents "
                    "USING ivfflat (embedding vector_cosine_ops) "
                    "WITH (lists = 100)"
                ))
            except Exception as idx_exc:  # noqa: BLE001
                task_logger.warning("vector index create failed: %s", idx_exc)
                return {
                    "status": "skipped",
                    "task": "rebuild_vector_index",
                    "reason": str(idx_exc),
                }
            await conn.execute(text("ANALYZE knowledge_documents"))
        return {
            "status": "ok",
            "task": "rebuild_vector_index",
            "index": "knowledge_documents_embedding_idx",
        }

    try:
        result = _run_async(_rebuild())
        task_logger.info("rebuild_vector_index completed: %s", result)
        return result
    except Exception as exc:  # noqa: BLE001
        task_logger.error("rebuild_vector_index failed: %s", exc)
        return {"status": "error", "task": "rebuild_vector_index", "error": str(exc)}


@celery_app.task(name="jobs.generate_daily_report", bind=True, max_retries=2)
def generate_daily_report(self: Any) -> dict[str, Any]:
    """
    Pre-generate and cache the daily team report so dashboards load instantly.
    Runs nightly via Celery Beat.
    """
    task_logger.info("generate_daily_report started")

    async def _generate() -> dict[str, Any]:
        from app.services.audit import audit_service
        from app.services.cache import cache_service, CacheService
        from collections import Counter, defaultdict
        from datetime import datetime, timezone

        entries = await audit_service.recent(limit=1000)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_entries = [
            e for e in entries if e.timestamp.strftime("%Y-%m-%d") == today_str
        ]

        team_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "invocations": 0, "successes": 0, "failures": 0,
            "tools": Counter(), "latency_ms": [],
        })
        for e in today_entries:
            s = team_stats[e.team_name]
            s["invocations"] += 1
            s["successes" if e.success else "failures"] += 1
            s["tools"][e.tool_name] += 1
            s["latency_ms"].append(e.latency_ms)

        report = {
            "report_date": today_str,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "celery",
            "teams": {
                team: {
                    "invocations": s["invocations"],
                    "successes": s["successes"],
                    "failures": s["failures"],
                    "top_tools": dict(s["tools"].most_common(5)),
                    "avg_latency_ms": round(
                        sum(s["latency_ms"]) / len(s["latency_ms"]), 1
                    ) if s["latency_ms"] else 0.0,
                }
                for team, s in team_stats.items()
            },
        }
        await cache_service.set_json(
            "reports", "daily", report, ttl=CacheService.TTL_DAY
        )
        return {"status": "ok", "task": "generate_daily_report", "date": today_str}

    try:
        result = _run_async(_generate())
        task_logger.info("generate_daily_report completed: %s", result)
        return result
    except Exception as exc:  # noqa: BLE001
        task_logger.error("generate_daily_report failed: %s", exc)
        raise self.retry(exc=exc, countdown=_backoff(self.request.retries, base=60))


@celery_app.task(name="jobs.cleanup_audit_logs", bind=True)
def cleanup_audit_logs(self: Any, keep: int = 5000) -> dict[str, Any]:
    """
    Trim the Redis audit log list to `keep` most-recent entries.
    Runs weekly via Celery Beat.
    """
    task_logger.info("cleanup_audit_logs started, keep=%d", keep)

    async def _cleanup() -> dict[str, Any]:
        import redis.asyncio as aioredis
        from app.config import get_settings as _gs

        s = _gs()
        client = aioredis.from_url(s.redis_url, encoding="utf-8", decode_responses=True)
        try:
            current_len = await client.llen("audit:log")
            if current_len > keep:
                await client.ltrim("audit:log", 0, keep - 1)
                trimmed = current_len - keep
            else:
                trimmed = 0
            return {
                "status": "ok",
                "task": "cleanup_audit_logs",
                "previous_length": current_len,
                "trimmed": trimmed,
            }
        finally:
            await client.aclose()

    try:
        result = _run_async(_cleanup())
        task_logger.info("cleanup_audit_logs completed: %s", result)
        return result
    except Exception as exc:  # noqa: BLE001
        task_logger.error("cleanup_audit_logs failed: %s", exc)
        return {"status": "error", "task": "cleanup_audit_logs", "error": str(exc)}
