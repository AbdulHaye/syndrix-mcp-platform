from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from sqlalchemy import select, text

from app.storage.db import get_db
from app.storage.models import KnowledgeDocument, _VECTOR_AVAILABLE

logger = structlog.get_logger(__name__)


class VectorStore:
    """
    Manages vector similarity search using pgvector and SQLAlchemy.

    If pgvector is not available the store falls back to returning all
    documents ordered by created_at (useful for development without
    a pgvector-enabled PostgreSQL instance).
    """

    async def store(
        self,
        title: str,
        source: str,
        content: str,
        embedding: list[float],
    ) -> KnowledgeDocument:
        """Persist a document chunk and its embedding to the database."""
        stored_embedding: Any = embedding if _VECTOR_AVAILABLE else json.dumps(embedding)
        async with get_db() as session:
            doc = KnowledgeDocument(
                id=uuid.uuid4(),
                title=title,
                source=source,
                content=content,
                chunk_index=0,
                embedding=stored_embedding,
            )
            session.add(doc)
            await session.flush()
            logger.info("vector_stored", title=title, source=source, dims=len(embedding))
            return doc

    async def search(
        self,
        query_embedding: list[float],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Return the `limit` most similar documents to the query embedding.

        Uses cosine distance (<=> operator) when pgvector is available.
        Falls back to returning the most recently created documents.
        """
        async with get_db() as session:
            try:
                # Build a pgvector cosine distance query using raw SQL
                embedding_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"
                stmt = text(
                    "SELECT id, title, source, content, chunk_index, created_at, "
                    "       embedding <=> :vec AS distance "
                    "FROM knowledge_documents "
                    "ORDER BY distance ASC "
                    "LIMIT :lim"
                )
                result = await session.execute(
                    stmt,
                    {"vec": embedding_literal, "lim": limit},
                )
                rows = result.fetchall()
                return [
                    {
                        "id": str(row.id),
                        "title": row.title,
                        "source": row.source,
                        "content": row.content,
                        "chunk_index": row.chunk_index,
                        "distance": float(row.distance),
                    }
                    for row in rows
                ]
            except Exception as exc:  # noqa: BLE001
                # pgvector not available or query failed — fall back to recency order
                logger.warning(
                    "vector_search_fallback",
                    error=str(exc),
                    msg="pgvector may not be installed; returning recent docs",
                )
                await session.rollback()
                stmt_fallback = (
                    select(KnowledgeDocument)
                    .order_by(KnowledgeDocument.created_at.desc())
                    .limit(limit)
                )
                result_fallback = await session.execute(stmt_fallback)
                docs = result_fallback.scalars().all()
                return [
                    {
                        "id": str(doc.id),
                        "title": doc.title,
                        "source": doc.source,
                        "content": doc.content,
                        "chunk_index": doc.chunk_index,
                        "distance": None,
                    }
                    for doc in docs
                ]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
vector_store = VectorStore()
