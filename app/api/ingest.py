from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.bearer import TeamIdentity, require_auth

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/ingest", tags=["rag"])


class IngestRequest(BaseModel):
    title: str = Field(..., description="Document title")
    content: str = Field(..., description="Full text content to ingest")
    source: str = Field("manual", description="Source identifier (URL, filename, etc.)")
    chunk_size: int = Field(400, ge=50, le=2000, description="Words per chunk")


@router.post("", summary="Ingest a document into the knowledge base")
async def ingest_document(
    body: IngestRequest,
    identity: TeamIdentity = Depends(require_auth),
) -> dict[str, Any]:
    from app.services.rag import rag_service

    logger.info(
        "ingest_request",
        team=identity.team_name,
        title=body.title,
        source=body.source,
        content_len=len(body.content),
    )
    result = await rag_service.ingest(
        title=body.title,
        content=body.content,
        source=body.source,
        chunk_size=body.chunk_size,
    )
    return result
