from __future__ import annotations

import asyncio
import logging
from typing import Any

import structlog
from celery import Celery

from app.config import get_settings

logger = structlog.get_logger(__name__)

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
)


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from a synchronous Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@celery_app.task(name="jobs.sync_crm_contacts", bind=True, max_retries=3)
def sync_crm_contacts(self: Any, team_name: str = "all") -> dict[str, Any]:
    """
    Synchronise CRM contacts from Podio and store summaries in pgvector.

    Fetches all contacts from the Podio adapter, generates text embeddings
    for each contact summary, and stores them in the knowledge_documents table.
    """
    logger.info("sync_crm_contacts_started", team=team_name)

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
                logger.warning("sync_contact_embed_failed", item_id=item_id, error=str(emb_exc))

        return {
            "status": "ok",
            "task": "sync_crm_contacts",
            "team": team_name,
            "contacts_found": len(contacts),
            "contacts_synced": synced,
        }

    try:
        result = _run_async(_sync())
        logger.info("sync_crm_contacts_completed", result=result)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.error("sync_crm_contacts_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="jobs.process_document", bind=True, max_retries=3)
def process_document(self: Any, title: str, content: str, source: str = "manual") -> dict[str, Any]:
    """
    Process a document by generating an embedding and storing it in pgvector.

    Steps:
      1. Call model_gateway.embed(content) to obtain the embedding vector.
      2. Call vector_store.store(title, source, content, embedding).
      3. Return confirmation with the document ID.
    """
    logger.info("process_document_started", title=title, source=source)

    async def _process() -> dict[str, Any]:
        from app.services.model_gateway import model_gateway
        from app.storage.vector import vector_store

        try:
            embedding = await model_gateway.embed(content)
        except Exception as emb_exc:  # noqa: BLE001
            logger.warning("process_document_embed_failed", error=str(emb_exc))
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
        logger.info("process_document_completed", result=result)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.error("process_document_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="jobs.rebuild_vector_index", bind=True)
def rebuild_vector_index(self: Any) -> dict[str, Any]:
    """
    Rebuild the pgvector index on the knowledge_documents table.

    This is useful after bulk inserts to re-cluster the HNSW/IVFFlat index
    for optimal query performance.

    Steps:
      1. Connect to the database engine.
      2. Drop and recreate the vector index with current parameters.
      3. ANALYZE the table to update planner statistics.
    """
    logger.info("rebuild_vector_index_started")

    async def _rebuild() -> dict[str, Any]:
        from app.storage.db import _get_engine
        from sqlalchemy import text

        engine = _get_engine()
        async with engine.begin() as conn:
            # Drop existing index if it exists
            await conn.execute(text(
                "DROP INDEX IF EXISTS knowledge_documents_embedding_idx"
            ))
            # Recreate using IVFFlat (adjust lists based on dataset size)
            try:
                await conn.execute(text(
                    "CREATE INDEX knowledge_documents_embedding_idx "
                    "ON knowledge_documents "
                    "USING ivfflat (embedding vector_cosine_ops) "
                    "WITH (lists = 100)"
                ))
            except Exception as idx_exc:  # noqa: BLE001
                # pgvector may not be installed in this environment
                logger.warning("vector_index_create_failed", error=str(idx_exc))
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
        logger.info("rebuild_vector_index_completed", result=result)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.error("rebuild_vector_index_failed", error=str(exc))
        return {
            "status": "error",
            "task": "rebuild_vector_index",
            "error": str(exc),
        }
