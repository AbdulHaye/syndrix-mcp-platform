from __future__ import annotations

import hashlib
import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_CHUNK_SIZE = 150   # words per chunk
_DEFAULT_CHUNK_OVERLAP = 20  # words of overlap between chunks
_CACHE_NS = "rag"


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


def _query_cache_key(query: str, limit: int) -> str:
    digest = hashlib.sha256(f"{query}:{limit}".encode()).hexdigest()[:16]
    return f"search:{digest}"


def _keyword_score(text: str, query_tokens: list[str]) -> float:
    """Simple keyword overlap score: fraction of query tokens found in text."""
    if not query_tokens:
        return 0.0
    text_lower = text.lower()
    matches = sum(1 for tok in query_tokens if tok in text_lower)
    return matches / len(query_tokens)


def _hybrid_rank(
    results: list[dict[str, Any]],
    query: str,
    vector_weight: float = 0.7,
    keyword_weight: float = 0.3,
) -> list[dict[str, Any]]:
    """
    Re-rank vector results by blending vector similarity with keyword overlap.
    Vector similarity is derived from position (rank 0 = score 1.0).
    """
    tokens = [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]
    n = len(results)
    for i, doc in enumerate(results):
        vector_sim = 1.0 - (i / max(n, 1)) * 0.5  # rank-based proxy: 1.0 → 0.5
        kw_sim = _keyword_score(doc.get("content", "") + " " + doc.get("title", ""), tokens)
        doc["_score"] = round(vector_weight * vector_sim + keyword_weight * kw_sim, 4)
    return sorted(results, key=lambda d: d["_score"], reverse=True)


class RAGService:
    """
    Retrieval-Augmented Generation service.

    Handles document ingestion (chunking + embedding + storage)
    and hybrid semantic + keyword search over the knowledge base.
    Caches search results in Redis via CacheService.
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

        # Invalidate cached search results since the KB changed
        try:
            from app.services.cache import cache_service
            await cache_service.invalidate_prefix(_CACHE_NS, "search:")
        except Exception:
            pass

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
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Embed the query, run vector search, apply hybrid re-ranking.
        Results are cached for TTL_MED seconds.
        """
        from app.services.model_gateway import model_gateway
        from app.storage.vector import vector_store
        from app.services.cache import cache_service, CacheService

        logger.info("rag_search_called", query=query[:100], limit=limit)

        # ── Cache check ───────────────────────────────────────────────────────
        cache_key = _query_cache_key(query, limit)
        if use_cache:
            cached = await cache_service.get_json(_CACHE_NS, cache_key)
            if cached is not None:
                logger.info("rag_search_cache_hit", cache_key=cache_key)
                cached["cached"] = True
                return cached

        # ── Embed query ───────────────────────────────────────────────────────
        try:
            query_embedding = await model_gateway.embed(query)
        except Exception as exc:
            logger.error("rag_search_embed_failed", error=str(exc))
            return {"success": False, "query": query, "error": str(exc)}

        # ── Vector search ─────────────────────────────────────────────────────
        try:
            # Fetch a larger pool for re-ranking
            pool_size = min(limit * 3, 30)
            results = await vector_store.search(query_embedding, limit=pool_size)
        except Exception as exc:
            logger.error("rag_search_failed", error=str(exc))
            return {"success": False, "query": query, "error": str(exc)}

        # ── Hybrid re-ranking ─────────────────────────────────────────────────
        results = _hybrid_rank(results, query)
        results = results[:limit]

        payload: dict[str, Any] = {
            "success": True,
            "query": query,
            "total": len(results),
            "results": results,
            "cached": False,
        }

        # ── Cache store ───────────────────────────────────────────────────────
        if use_cache:
            try:
                await cache_service.set_json(
                    _CACHE_NS, cache_key, payload, ttl=CacheService.TTL_MED
                )
            except Exception:
                pass

        return payload


# Singleton
rag_service = RAGService()
