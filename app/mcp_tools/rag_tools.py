from __future__ import annotations

from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

logger = structlog.get_logger(__name__)


def register_rag_tools(mcp: FastMCP) -> None:
    """Register RAG (knowledge base) MCP tools."""

    @mcp.tool(
        name="rag.search",
        description=(
            "Semantic search over the internal knowledge base. "
            "Returns the most relevant document chunks for the query."
        ),
    )
    async def rag_search(query: str, limit: int = 5) -> dict[str, Any]:
        from app.services.rag import rag_service

        logger.info("rag.search_called", query=query[:100], limit=limit)
        return await rag_service.search(query, limit=limit)

    @mcp.tool(
        name="rag.ingest",
        description=(
            "Ingest a text document into the knowledge base. "
            "The text is chunked, embedded, and stored in pgvector for future semantic search."
        ),
    )
    async def rag_ingest(
        title: str,
        content: str,
        source: str = "manual",
        chunk_size: int = 400,
    ) -> dict[str, Any]:
        from app.services.rag import rag_service

        logger.info("rag.ingest_called", title=title, source=source, content_len=len(content))
        return await rag_service.ingest(
            title=title,
            content=content,
            source=source,
            chunk_size=chunk_size,
        )
