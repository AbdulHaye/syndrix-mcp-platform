"""Adapter that lets the agent use our custom podio-files MCP server in-process.

The official MCP ``FastMCP`` object exposes ``list_tools()`` / ``call_tool()`` that
run the tools directly in this process — so the agent talks to a real MCP server
without an HTTP transport or a second lifespan to manage.

Result parsing (see _normalise_call_result): call_tool() only returns the
``(content_blocks, structured_dict)`` tuple form when a tool declares an EXPLICIT
output schema via its return type annotation. None of these tools do (they return
plain ``dict``), so in the installed MCP SDK version it always returns a list of
content blocks instead — one TextContent whose ``.text`` is the JSON-encoded dict.
Session 23: a bare tuple-check silently fell through to ``str(result)`` (the Python
repr of that content-block list, e.g. "[TextContent(type='text', text='{...}', ...)]")
instead of ever hitting the tuple branch — every create_item/update_item/delete_file/
etc. result was reaching the agent as repr noise instead of clean JSON.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from app.mcp_servers.podio_files import files_mcp
from app.services.podio_rest import podio_rest

logger = structlog.get_logger(__name__)


def _normalise_call_result(result: Any) -> dict[str, Any]:
    """Coerce any shape FastMCP's call_tool() might return into a plain dict."""
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]  # structured-output form (tools with an explicit output schema)
    if isinstance(result, dict):
        return result
    if isinstance(result, (list, tuple)):
        text_parts = [t for t in (getattr(block, "text", None) for block in result) if t is not None]
        joined = "\n".join(text_parts)
        if joined:
            try:
                parsed = json.loads(joined)
                return parsed if isinstance(parsed, dict) else {"data": parsed}
            except (json.JSONDecodeError, ValueError):
                return {"content": joined}
    return {"content": str(result)}


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
        return _normalise_call_result(result)


podio_files = PodioFilesClient()
