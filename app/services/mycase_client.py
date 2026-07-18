"""Adapter that lets the agent use our custom mycase MCP server in-process.

Mirrors app/services/podio_files_client.py's structure: FastMCP exposes list_tools()
/ call_tool() that run directly in this process, so the agent talks to a real MCP
server without an HTTP transport or a second lifespan to manage.

Result parsing is deliberately NOT a bare tuple-check (see _normalise_call_result) —
FastMCP's call_tool() only returns the (content, structured_dict) tuple form when a
tool declares an explicit output schema via its return type annotation. None of our
tools do (they return plain ``dict``), so in this MCP SDK version it always returns a
list of content blocks instead — a single TextContent whose .text is the JSON-encoded
dict. A naive tuple-check silently falls through to str(result) (the Python repr of
the content-block list, e.g. "[TextContent(type='text', text='{...}', ...)]"), which
pollutes the agent's context with object-repr noise instead of the real JSON.
"""
from __future__ import annotations

import json
from typing import Any

import structlog
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from app.mcp_servers.mycase import mycase_mcp
from app.services.mycase_rest import mycase_rest

logger = structlog.get_logger(__name__)


def _describe_validation_error(tool_name: str, exc: ValidationError) -> str:
    """Turn a raw pydantic ValidationError (e.g. 'search_casesArguments\\nquery\\n
    Field required [type=missing, ...]') into a short, actionable message — both for
    the model (which needs to self-correct the next call) and because this text can
    now reach the user directly (failed tool calls are shown in the chat UI)."""
    parts: list[str] = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err.get("loc", ())) or "argument"
        kind = err.get("type", "")
        if kind == "missing":
            parts.append(f"'{field}' is required but was not provided")
        else:
            parts.append(f"'{field}' is invalid ({err.get('msg', kind)})")
    detail = "; ".join(parts) or str(exc)
    return f"Invalid arguments for {tool_name}: {detail}."


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


class MyCaseClient:
    async def is_connected(self) -> bool:
        return await mycase_rest.is_connected()

    async def list_tools(self) -> list[dict[str, Any]]:
        tools = await mycase_mcp.list_tools()
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
        try:
            result = await mycase_mcp.call_tool(name, arguments)
        except ToolError as exc:
            if isinstance(exc.__cause__, ValidationError):
                message = _describe_validation_error(name, exc.__cause__)
                logger.warning("mycase_tool_invalid_args", tool=name, arguments=arguments, error=message)
                return {"success": False, "error": message}
            raise
        return _normalise_call_result(result)


mycase_client = MyCaseClient()
