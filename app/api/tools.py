from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.bearer import TeamIdentity, require_auth
from app.auth.rbac import can_use_tool

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/tools", tags=["tools"])


class InvokeRequest(BaseModel):
    tool: str
    args: dict[str, Any] = {}


@router.post("/invoke", summary="Invoke an MCP tool by name")
async def invoke_tool(
    body: InvokeRequest,
    identity: TeamIdentity = Depends(require_auth),
) -> dict[str, Any]:
    if not can_use_tool(body.tool, identity):
        return {
            "success": False,
            "tool": body.tool,
            "error": f"Role '{identity.role.value}' is not permitted to use tool '{body.tool}'",
        }

    logger.info("tool_invoke", tool=body.tool, team=identity.team_name, args_keys=list(body.args.keys()))

    from app.main import mcp_server

    # Look up the tool in the FastMCP registry and call it.
    # FastMCP stores tools in _tool_manager._tools (dict[name, Tool]) where Tool.fn is the callable.
    # The dict may be keyed by name or by arbitrary key depending on SDK version, so we match by .name attr.
    try:
        tool_fn = None

        # Primary: _tool_manager._tools keyed by name (mcp >= 1.0)
        try:
            tm = mcp_server._tool_manager  # type: ignore[attr-defined]
            tools_dict: dict = tm._tools  # type: ignore[attr-defined]
            # Keys may be the tool name directly
            if body.tool in tools_dict:
                tool_fn = getattr(tools_dict[body.tool], "fn", tools_dict[body.tool])
            else:
                # Keys may differ from name; scan by .name attribute
                for tool_obj in tools_dict.values():
                    if getattr(tool_obj, "name", None) == body.tool:
                        tool_fn = getattr(tool_obj, "fn", tool_obj)
                        break
        except AttributeError:
            pass

        if tool_fn is None:
            return {"success": False, "tool": body.tool, "error": f"Tool '{body.tool}' not found in registry"}

        result = await tool_fn(**body.args)
        return {"success": True, "tool": body.tool, "result": result}

    except TypeError as exc:
        logger.warning("tool_invoke_bad_args", tool=body.tool, error=str(exc))
        return {"success": False, "tool": body.tool, "error": f"Invalid arguments: {exc}"}
    except Exception as exc:  # noqa: BLE001
        logger.error("tool_invoke_failed", tool=body.tool, error=str(exc))
        return {"success": False, "tool": body.tool, "error": str(exc)}
