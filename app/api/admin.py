from __future__ import annotations

import time
import uuid
from collections import Counter
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.bearer import TeamRole, TeamIdentity, require_role
from app.registries.tool_registry import tool_registry
from app.registries.resource_registry import resource_registry
from app.registries.prompt_registry import prompt_registry
from app.services.audit import audit_service
from app.storage.db import get_db
from app.storage.models import User

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

_admin_dep = require_role(TeamRole.ADMIN)

# Track server start time for uptime calculation
_START_TIME = time.time()


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


class CreateUserRequest(BaseModel):
    email: str
    password: str
    full_name: str | None = None
    team_name: str
    role: str = "dev"


class UpdateUserRequest(BaseModel):
    full_name: str | None = None
    team_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str | None
    team_name: str
    role: str
    is_active: bool
    created_at: str


@router.get("/users", summary="List all users")
async def list_users(
    identity: TeamIdentity = Depends(_admin_dep),
) -> dict[str, Any]:
    async with get_db() as session:
        result = await session.execute(select(User).order_by(User.created_at.desc()))
        users = result.scalars().all()
    return {
        "count": len(users),
        "users": [
            UserOut(
                id=str(u.id),
                email=u.email,
                full_name=u.full_name,
                team_name=u.team_name,
                role=u.role,
                is_active=u.is_active,
                created_at=u.created_at.isoformat(),
            ).model_dump()
            for u in users
        ],
    }


@router.post("/users", summary="Create a new user", status_code=201)
async def create_user(
    body: CreateUserRequest,
    identity: TeamIdentity = Depends(_admin_dep),
) -> dict[str, Any]:
    from app.api.auth import hash_password

    if body.role not in ("bd", "dev", "mgmt", "admin"):
        raise HTTPException(status_code=400, detail="role must be one of: bd, dev, mgmt, admin")

    async with get_db() as session:
        existing = await session.execute(select(User).where(User.email == body.email))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="Email already registered")

        user = User(
            id=uuid.uuid4(),
            email=body.email,
            hashed_password=hash_password(body.password),
            full_name=body.full_name,
            team_name=body.team_name,
            role=body.role,
            is_active=True,
        )
        session.add(user)

    logger.info("user_created", email=body.email, role=body.role, by=identity.team_name)
    return {
        "success": True,
        "user": UserOut(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            team_name=user.team_name,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at.isoformat(),
        ).model_dump(),
    }


@router.patch("/users/{user_id}", summary="Update user role, status, or password")
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    identity: TeamIdentity = Depends(_admin_dep),
) -> dict[str, Any]:
    from app.api.auth import hash_password

    if body.role is not None and body.role not in ("bd", "dev", "mgmt", "admin"):
        raise HTTPException(status_code=400, detail="role must be one of: bd, dev, mgmt, admin")

    async with get_db() as session:
        result = await session.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user: User | None = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        if body.full_name is not None:
            user.full_name = body.full_name
        if body.team_name is not None:
            user.team_name = body.team_name
        if body.role is not None:
            user.role = body.role
        if body.is_active is not None:
            user.is_active = body.is_active
        if body.password is not None:
            user.hashed_password = hash_password(body.password)

        session.add(user)

    logger.info("user_updated", user_id=user_id, by=identity.team_name)
    return {"success": True, "user_id": user_id}


@router.delete("/users/{user_id}", summary="Deactivate (soft-delete) a user")
async def delete_user(
    user_id: str,
    identity: TeamIdentity = Depends(_admin_dep),
) -> dict[str, Any]:
    async with get_db() as session:
        result = await session.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user: User | None = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        user.is_active = False
        session.add(user)

    logger.info("user_deactivated", user_id=user_id, by=identity.team_name)
    return {"success": True, "user_id": user_id}


@router.get("/metrics", summary="Live platform metrics from the audit log")
async def get_metrics(
    identity: TeamIdentity = Depends(_admin_dep),
) -> dict[str, Any]:
    tools = tool_registry.all()
    resources = resource_registry.all()
    prompts_reg = prompt_registry.all()

    # Pull last 1000 audit entries for aggregation
    entries = await audit_service.recent(limit=1000)

    total = len(entries)
    failures = sum(1 for e in entries if not e.success)
    successes = total - failures
    error_rate = round(failures / total * 100, 2) if total else 0.0

    avg_latency = (
        round(sum(e.latency_ms for e in entries) / total, 1) if total else 0.0
    )

    tool_counter: Counter[str] = Counter(e.tool_name for e in entries)
    team_counter: Counter[str] = Counter(e.team_name for e in entries)

    uptime_s = int(time.time() - _START_TIME)

    return {
        "uptime_seconds": uptime_s,
        "tools_registered": len(tools),
        "resources_registered": len(resources),
        "prompts_registered": len(prompts_reg),
        "audit_entries_tracked": total,
        "requests_total": total,
        "successes": successes,
        "failures": failures,
        "error_rate_pct": error_rate,
        "avg_latency_ms": avg_latency,
        "top_tools": dict(tool_counter.most_common(5)),
        "top_teams": dict(team_counter.most_common(5)),
    }
