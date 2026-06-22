from __future__ import annotations

from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

logger = structlog.get_logger(__name__)


def register_memory_tools(mcp: FastMCP) -> None:
    """Register memory MCP tools on the provided FastMCP server."""

    @mcp.tool(
        name="memory.store",
        description=(
            "Store a conversation summary or fact in team memory. "
            "team: the team name; summary: text to remember; "
            "metadata: optional dict with extra context (e.g. contact_id, source)."
        ),
    )
    async def memory_store(
        team: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from app.services.memory import memory_service

        logger.info("memory.store_called", team=team, summary_len=len(summary))
        try:
            await memory_service.store_conversation(
                team=team,
                summary=summary,
                metadata=metadata or {},
            )
            return {"success": True, "team": team, "stored": True}
        except Exception as exc:
            logger.error("memory.store_failed", error=str(exc))
            return {"success": False, "error": str(exc)}

    @mcp.tool(
        name="memory.retrieve",
        description=(
            "Semantically search team memory for entries related to the query. "
            "Returns the most relevant past conversation summaries."
        ),
    )
    async def memory_retrieve(query: str, limit: int = 5) -> dict[str, Any]:
        from app.services.memory import memory_service

        logger.info("memory.retrieve_called", query=query[:80], limit=limit)
        try:
            results = await memory_service.retrieve_similar(query=query, limit=limit)
            return {
                "success": True,
                "query": query,
                "total": len(results),
                "results": results,
            }
        except Exception as exc:
            logger.error("memory.retrieve_failed", error=str(exc))
            return {"success": False, "query": query, "error": str(exc)}

    @mcp.tool(
        name="memory.recent",
        description=(
            "Get the most recent memory entries for a specific team. "
            "Useful for building context before starting a conversation."
        ),
    )
    async def memory_recent(team: str, limit: int = 10) -> dict[str, Any]:
        from app.services.memory import memory_service

        logger.info("memory.recent_called", team=team, limit=limit)
        try:
            entries = await memory_service.get_recent(team=team, limit=limit)
            return {
                "success": True,
                "team": team,
                "count": len(entries),
                "entries": entries,
            }
        except Exception as exc:
            logger.error("memory.recent_failed", error=str(exc))
            return {"success": False, "team": team, "error": str(exc)}
