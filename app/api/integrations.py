from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth.bearer import TeamIdentity, TeamRole, require_auth

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/integrations/podio", tags=["integrations"])

_ALLOWED_ROLES = {TeamRole.BD, TeamRole.ADMIN}
_FRONTEND_REDIRECT_DEFAULT = "http://localhost:3000/dashboard/podio-agent"


def _require_bd_or_admin(identity: TeamIdentity) -> None:
    if identity.role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="BD and Admin roles only.")


@router.get("/connect", summary="Start Podio MCP OAuth — returns the authorize URL")
async def connect(identity: TeamIdentity = Depends(require_auth)) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.podio_mcp import podio_mcp

    try:
        url = await podio_mcp.build_authorize_url()
        return {"success": True, "authorize_url": url}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


@router.get("/callback", summary="OAuth redirect target — exchanges the code for a token")
async def callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
) -> RedirectResponse:
    # No auth dependency: this is the OAuth provider's browser redirect, validated
    # by the PKCE `state` we issued.
    from app.services.podio_mcp import podio_mcp
    from app.services.settings_service import get_setting

    frontend = (await get_setting("podio_mcp_frontend_redirect")) or _FRONTEND_REDIRECT_DEFAULT

    if error:
        logger.warning("podio_mcp_oauth_error", error=error, detail=error_description)
        return RedirectResponse(f"{frontend}?podio=error&reason={error}")
    if not code or not state:
        return RedirectResponse(f"{frontend}?podio=error&reason=missing_code")

    try:
        await podio_mcp.exchange_code(code, state)
        return RedirectResponse(f"{frontend}?podio=connected")
    except Exception as exc:  # noqa: BLE001
        logger.error("podio_mcp_exchange_failed", error=str(exc))
        return RedirectResponse(f"{frontend}?podio=error&reason=exchange_failed")


@router.get("/status", summary="Podio MCP connection status")
async def status(identity: TeamIdentity = Depends(require_auth)) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.podio_mcp import podio_mcp

    connected = await podio_mcp.is_connected()
    selected = await podio_mcp.get_selected_workspace() if connected else None
    return {"connected": connected, "workspace": selected}


@router.get("/organizations", summary="List Podio organizations (via MCP)")
async def organizations(identity: TeamIdentity = Depends(require_auth)) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.podio_mcp import podio_mcp

    try:
        orgs = await podio_mcp.list_organizations()
        return {"success": True, "organizations": orgs}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "organizations": [], "error": str(exc)}


@router.get("/spaces", summary="List workspaces in an organization (via MCP)")
async def spaces(
    org_id: int = Query(..., description="Podio organization ID"),
    identity: TeamIdentity = Depends(require_auth),
) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.podio_mcp import podio_mcp

    try:
        result = await podio_mcp.list_spaces(org_id)
        return {"success": True, "spaces": result}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "spaces": [], "error": str(exc)}


class SelectWorkspaceRequest(BaseModel):
    space_id: int = Field(..., description="The Podio space_id to operate within")
    name: str | None = Field(None, description="Workspace display name")
    org_name: str | None = Field(None, description="Organization display name")


@router.post("/workspace", summary="Set the active Podio workspace for the agent")
async def set_workspace(
    body: SelectWorkspaceRequest,
    identity: TeamIdentity = Depends(require_auth),
) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.podio_mcp import podio_mcp

    await podio_mcp.set_selected_workspace(body.space_id, body.name, body.org_name)
    return {"success": True, "space_id": body.space_id, "name": body.name}


@router.get("/tools", summary="List Podio MCP tool names (verifies the live connection)")
async def tools(identity: TeamIdentity = Depends(require_auth)) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.podio_mcp import podio_mcp

    try:
        specs = await podio_mcp.list_tools()
        names = [s["function"]["name"] for s in specs]
        return {"success": True, "count": len(names), "tools": names}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc), "tools": []}


@router.post("/disconnect", summary="Disconnect Podio MCP (clear stored tokens)")
async def disconnect(identity: TeamIdentity = Depends(require_auth)) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.podio_mcp import podio_mcp

    await podio_mcp.disconnect()
    return {"success": True}
