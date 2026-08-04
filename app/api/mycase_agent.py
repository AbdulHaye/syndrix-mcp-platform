from __future__ import annotations

import uuid as uuid_lib
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from langfuse import propagate_attributes
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.bearer import TeamIdentity, TeamRole, require_auth

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["mycase-agent"])

_ALLOWED_ROLES = {TeamRole.BD, TeamRole.ADMIN}


class AgentTurn(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str = Field(..., description="The message text")


class MyCaseAgentRequest(BaseModel):
    message: str = Field(..., description="The user's natural-language instruction")
    history: list[AgentTurn] = Field(default_factory=list, description="Prior conversation turns for context")
    session_id: str | None = Field(
        None,
        description=(
            "The frontend's chat-session UUID (same id used for GET/PUT "
            "/agent/mycase/sessions/{id}) — passed through purely for Langfuse "
            "tracing so every turn of one conversation groups under one session "
            "in the dashboard instead of showing as unrelated traces. Optional; "
            "has no effect on the agent's own behavior."
        ),
    )


def _require_bd_or_admin(identity: TeamIdentity) -> None:
    if identity.role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="The MyCase Agent is available to BD and Admin roles only.")


@router.get("/mycase/status", summary="MyCase connection status, including token expiry")
async def mycase_status(identity: TeamIdentity = Depends(require_auth)) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.mycase_rest import mycase_rest

    return await mycase_rest.connection_status()


@router.post("/mycase", summary="Run the MyCase agent (LLM + MyCase read-only tools)")
async def mycase_agent(
    body: MyCaseAgentRequest,
    identity: TeamIdentity = Depends(require_auth),
) -> dict[str, Any]:
    _require_bd_or_admin(identity)

    from app.services.mycase_agent import run_mycase_agent

    logger.info(
        "mycase_agent_request", team=identity.team_name,
        message_len=len(body.message), history_len=len(body.history),
    )
    try:
        history = [turn.model_dump() for turn in body.history]
        with propagate_attributes(
            user_id=identity.team_name, session_id=body.session_id, tags=["mycase-agent"],
            metadata={"team": identity.team_name, "role": identity.role},
        ):
            return await run_mycase_agent(body.message, history, team=identity.team_name)
    except Exception as exc:  # noqa: BLE001
        logger.error("mycase_agent_failed", error=str(exc))
        return {"success": False, "reply": "", "steps": [], "error": str(exc)}


@router.get("/mycase/reports/{report_id}", summary="Download a generated MyCase Excel report")
async def download_mycase_report(
    report_id: str,
    identity: TeamIdentity = Depends(require_auth),
) -> Response:
    """Serve an .xlsx built earlier in a chat turn by the `build_report` tool.

    Same response shape as the Podio export endpoint (app/api/podio_files.py) so
    the frontend's existing authed-blob-download helper works unchanged.
    """
    _require_bd_or_admin(identity)
    from app.services.report_store import report_store

    record = await report_store.get(report_id)
    if record is None:
        # Reports live ~1h. An expired id is the common case here, and saying so
        # is more useful than a bare 404 — the fix is to re-run the request.
        raise HTTPException(
            status_code=404,
            detail="That report has expired or does not exist. Ask the agent to build it again.",
        )
    # A report id is effectively a bearer capability, so scope it to the team that
    # created it (admins excepted) rather than letting any leaked id be redeemed.
    if record["team"] and record["team"] != identity.team_name and identity.role != TeamRole.ADMIN:
        raise HTTPException(status_code=403, detail="This report belongs to another team.")

    safe_name = record["filename"].replace('"', "'")
    return Response(
        content=record["payload"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


# ── Chat session persistence (mirrors app/api/agent.py's Podio session CRUD) ────

def _owner_key(identity: TeamIdentity) -> str:
    return f"user:{identity.user_id}" if identity.user_id else f"team:{identity.team_name}"


class ChatSessionSummary(BaseModel):
    id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str


class ChatSessionDetail(ChatSessionSummary):
    messages: list[dict[str, Any]]


class SaveChatSessionRequest(BaseModel):
    title: str = "New chat"
    messages: list[dict[str, Any]] = Field(default_factory=list)


def _to_summary(row: Any) -> ChatSessionSummary:
    return ChatSessionSummary(
        id=str(row.id), title=row.title, message_count=len(row.messages or []),
        created_at=row.created_at.isoformat(), updated_at=row.updated_at.isoformat(),
    )


def _parse_session_id(session_id: str) -> uuid_lib.UUID:
    try:
        return uuid_lib.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session id") from None


@router.get("/mycase/sessions", summary="List the current user's MyCase Agent chat sessions")
async def list_chat_sessions(identity: TeamIdentity = Depends(require_auth)) -> list[ChatSessionSummary]:
    _require_bd_or_admin(identity)
    from app.storage.db import get_db
    from app.storage.models import MyCaseChatSession

    owner = _owner_key(identity)
    async with get_db() as session:
        result = await session.execute(
            select(MyCaseChatSession)
            .where(MyCaseChatSession.owner_key == owner)
            .order_by(MyCaseChatSession.created_at.desc())
        )
        rows = result.scalars().all()
    return [_to_summary(r) for r in rows]


@router.get("/mycase/sessions/{session_id}", summary="Get one MyCase chat session with full message history")
async def get_chat_session(session_id: str, identity: TeamIdentity = Depends(require_auth)) -> ChatSessionDetail:
    _require_bd_or_admin(identity)
    from app.storage.db import get_db
    from app.storage.models import MyCaseChatSession

    sid = _parse_session_id(session_id)
    owner = _owner_key(identity)
    async with get_db() as session:
        row = await session.get(MyCaseChatSession, sid)
    if row is None or row.owner_key != owner:
        raise HTTPException(status_code=404, detail="Session not found")
    return ChatSessionDetail(**_to_summary(row).model_dump(), messages=row.messages or [])


@router.put("/mycase/sessions/{session_id}", summary="Create or update a MyCase chat session (upsert)")
async def save_chat_session(
    session_id: str,
    body: SaveChatSessionRequest,
    identity: TeamIdentity = Depends(require_auth),
) -> ChatSessionSummary:
    _require_bd_or_admin(identity)
    from app.storage.db import get_db
    from app.storage.models import MyCaseChatSession

    sid = _parse_session_id(session_id)
    owner = _owner_key(identity)
    async with get_db() as session:
        row = await session.get(MyCaseChatSession, sid)
        if row is None:
            row = MyCaseChatSession(
                id=sid, owner_key=owner, team_name=identity.team_name,
                title=body.title, messages=body.messages,
            )
            session.add(row)
        else:
            if row.owner_key != owner:
                raise HTTPException(status_code=404, detail="Session not found")
            row.title = body.title
            row.messages = body.messages
        await session.flush()
        summary = _to_summary(row)
    return summary


@router.delete("/mycase/sessions/{session_id}", summary="Delete a MyCase chat session")
async def delete_chat_session(session_id: str, identity: TeamIdentity = Depends(require_auth)) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.storage.db import get_db
    from app.storage.models import MyCaseChatSession

    sid = _parse_session_id(session_id)
    owner = _owner_key(identity)
    async with get_db() as session:
        row = await session.get(MyCaseChatSession, sid)
        if row is not None and row.owner_key == owner:
            await session.delete(row)
    return {"success": True}
