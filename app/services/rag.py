from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_CHUNK_SIZE = 150  # words per chunk
_DEFAULT_CHUNK_OVERLAP = 20  # words of overlap between chunks


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into overlapping word-based chunks."""
    words = re.split(r"\s+", text.strip())
    if not words:
        return []
    chunks: list[str] = []
    step = max(1, chunk_size - overlap)
    for start in range(0, len(words), step):
        chunk_words = words[start : start + chunk_size]
        chunks.append(" ".join(chunk_words))
        if start + chunk_size >= len(words):
            break
    return chunks


class RAGService:
    """
    Retrieval-Augmented Generation service.

    Handles document ingestion (chunking + embedding + storage)
    and semantic search over the knowledge base.
    """

    async def ingest(
        self,
        title: str,
        content: str,
        source: str = "manual",
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
    ) -> dict[str, Any]:
        """
        Chunk content, embed each chunk, and store in pgvector.
        Returns summary with document IDs and chunk count.
        """
        from app.services.model_gateway import model_gateway
        from app.storage.vector import vector_store

        chunks = _chunk_text(content, chunk_size, chunk_overlap)
        if not chunks:
            return {"success": False, "error": "No content to ingest"}

        logger.info("rag_ingest_started", title=title, source=source, chunks=len(chunks))

        doc_ids: list[str] = []
        failed = 0

        for idx, chunk in enumerate(chunks):
            try:
                embedding = await model_gateway.embed(chunk)
                doc = await vector_store.store(
                    title=f"{title} [chunk {idx + 1}/{len(chunks)}]",
                    source=source,
                    content=chunk,
                    embedding=embedding,
                )
                doc_ids.append(str(doc.id))
            except Exception as exc:
                logger.warning("rag_chunk_failed", chunk_idx=idx, error=str(exc))
                failed += 1

        logger.info("rag_ingest_done", title=title, stored=len(doc_ids), failed=failed)
        return {
            "success": True,
            "title": title,
            "source": source,
            "chunks_total": len(chunks),
            "chunks_stored": len(doc_ids),
            "chunks_failed": failed,
            "document_ids": doc_ids,
        }

    async def search(
        self,
        query: str,
        limit: int = 5,
    ) -> dict[str, Any]:
        """
        Embed the query and return the most similar document chunks.
        """
        from app.services.model_gateway import model_gateway
        from app.storage.vector import vector_store

        logger.info("rag_search_called", query=query[:100], limit=limit)

        try:
            query_embedding = await model_gateway.embed(query)
        except Exception as exc:
            logger.error("rag_search_embed_failed", error=str(exc))
            return {"success": False, "query": query, "error": str(exc)}

        try:
            results = await vector_store.search(query_embedding, limit=limit)
            return {
                "success": True,
                "query": query,
                "total": len(results),
                "results": results,
            }
        except Exception as exc:
            logger.error("rag_search_failed", error=str(exc))
            return {"success": False, "query": query, "error": str(exc)}


# Singleton
rag_service = RAGService()
