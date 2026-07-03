"""Adapter that lets the agent use our custom podio-files MCP server in-process.

The official MCP ``FastMCP`` object exposes ``list_tools()`` / ``call_tool()`` that
run the tools directly in this process — so the agent talks to a real MCP server
without an HTTP transport or a second lifespan to manage. ``call_tool`` returns a
``(content_blocks, structured_dict)`` tuple; we surface the structured dict.
"""
from __future__ import annotations

from typing import Any

import structlog

from app.mcp_servers.podio_files import files_mcp
from app.services.podio_rest import podio_rest

logger = structlog.get_logger(__name__)


class PodioFilesClient:
    async def is_connected(self) -> bool:
        return await podio_rest.is_connected()

    async def list_tools(self) -> list[dict[str, Any]]:
        tools = await files_mcp.list_tools()
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.inputSchema or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await files_mcp.call_tool(name, arguments)
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
            return result[1]
        if isinstance(result, dict):
            return result
        return {"content": str(result)}


podio_files = PodioFilesClient()
