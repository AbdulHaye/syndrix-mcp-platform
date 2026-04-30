from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.auth.bearer import TeamRole, TeamIdentity, require_role
from app.registries.tool_registry import tool_registry
from app.registries.resource_registry import resource_registry
from app.registries.prompt_registry import prompt_registry
from app.services.audit import audit_service

router = APIRouter(prefix="/admin", tags=["admin"])

_admin_dep = require_role(TeamRole.ADMIN)


@router.get("/tools", summary="List all registered tools")
async def list_tools(
    identity: TeamIdentity = Depends(_admin_dep),
) -> dict[str, Any]:
    tools = tool_registry.all()
    return {
        "count": len(tools),
        "tools": [t.model_dump() for t in tools],
    }


@router.get("/resources", summary="List all registered resources")
async def list_resources(
    identity: TeamIdentity = Depends(_admin_dep),
) -> dict[str, Any]:
    resources = resource_registry.all()
    return {
        "count": len(resources),
        "resources": [r.model_dump() for r in resources],
    }


@router.get("/prompts", summary="List all registered prompts")
async def list_prompts(
    identity: TeamIdentity = Depends(_admin_dep),
) -> dict[str, Any]:
    prompts = prompt_registry.all()
    return {
        "count": len(prompts),
        "prompts": [p.model_dump() for p in prompts],
    }


@router.get("/audit", summary="Recent audit log entries")
async def recent_audit(
    limit: int = 50,
    identity: TeamIdentity = Depends(_admin_dep),
) -> dict[str, Any]:
    entries = await audit_service.recent(limit=limit)
    return {
        "count": len(entries),
        "entries": [e.model_dump(mode="json") for e in entries],
    }


@router.get("/metrics", summary="Platform metrics (placeholder)")
async def get_metrics(
    identity: TeamIdentity = Depends(_admin_dep),
) -> dict[str, Any]:
    tools = tool_registry.all()
    resources = resource_registry.all()
    prompts = prompt_registry.all()
    return {
        "tools_registered": len(tools),
        "resources_registered": len(resources),
        "prompts_registered": len(prompts),
        "uptime_seconds": 0,
        "requests_total": 0,
        "requests_per_second": 0.0,
        "error_rate": 0.0,
        "note": "Full metrics collection not yet wired — these are placeholder values.",
    }
