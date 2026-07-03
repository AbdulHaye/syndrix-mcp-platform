from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.bearer import TeamIdentity, TeamRole, require_auth

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

_ALLOWED_ROLES = {TeamRole.BD, TeamRole.ADMIN}


class AgentTurn(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str = Field(..., description="The message text")


class PodioAgentRequest(BaseModel):
    message: str = Field(..., description="The user's natural-language instruction")
    history: list[AgentTurn] = Field(
        default_factory=list,
        description="Prior conversation turns for context",
    )


def _require_bd_or_admin(identity: TeamIdentity) -> None:
    if identity.role not in _ALLOWED_ROLES:
        raise HTTPException(
            status_code=403,
            detail="The Podio Agent is available to BD and Admin roles only.",
        )


@router.post("/podio", summary="Run the Podio agent (LLM + Podio MCP tools)")
async def podio_agent(
    body: PodioAgentRequest,
    identity: TeamIdentity = Depends(require_auth),
) -> dict[str, Any]:
    _require_bd_or_admin(identity)

    from app.services.podio_agent import run_podio_agent

    logger.info(
        "podio_agent_request",
        team=identity.team_name,
        message_len=len(body.message),
        history_len=len(body.history),
    )
    try:
        history = [turn.model_dump() for turn in body.history]
        return await run_podio_agent(body.message, history)
    except Exception as exc:  # noqa: BLE001
        logger.error("podio_agent_failed", error=str(exc))
        return {"success": False, "reply": "", "steps": [], "error": str(exc)}
