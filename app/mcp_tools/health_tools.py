from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

logger = structlog.get_logger(__name__)


def register_health_tools(mcp: FastMCP) -> None:
    """Register health-check MCP tools on the provided FastMCP server."""

    @mcp.tool(name="health.ping", description="Ping the MCP platform to verify liveness.")
    async def health_ping() -> dict[str, Any]:
        """Returns a pong response with the current UTC timestamp."""
        ts = datetime.now(timezone.utc).isoformat()
        logger.debug("health.ping_called", timestamp=ts)
        return {
            "pong": True,
            "timestamp": ts,
            "message": "MCP platform is alive and responding.",
        }

    @mcp.tool(name="health.status", description="Return a summary of the MCP server status.")
    async def health_status() -> dict[str, Any]:
        """Returns server status, registered tool count, and uptime information."""
        from app.registries.tool_registry import tool_registry
        from app.registries.resource_registry import resource_registry
        from app.registries.prompt_registry import prompt_registry
        from app.config import get_settings

        settings = get_settings()
        ts = datetime.now(timezone.utc).isoformat()

        tools = tool_registry.all()
        resources = resource_registry.all()
        prompts = prompt_registry.all()

        logger.debug("health.status_called", timestamp=ts)
        return {
            "status": "running",
            "timestamp": ts,
            "environment": settings.app_env,
            "version": "0.1.0",
            "registry": {
                "tools": len(tools),
                "resources": len(resources),
                "prompts": len(prompts),
            },
            "ollama_model": settings.ollama_default_model,
        }
