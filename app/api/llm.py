from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.bearer import TeamIdentity, require_auth

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/llm", tags=["llm"])

_DEFAULT_MODEL = "ollama:llama3.2"


@router.get("/models", summary="List available LLM models (Ollama + Google) and the selected one")
async def list_models(identity: TeamIdentity = Depends(require_auth)) -> dict[str, Any]:
    from app.services.model_gateway import model_gateway
    from app.services.settings_service import get_setting

    ollama = await model_gateway.list_ollama_models()
    google = await model_gateway.list_google_models()
    groq = await model_gateway.list_groq_models()
    mistral = await model_gateway.list_mistral_models()
    openai = await model_gateway.list_openai_models()
    anthropic = await model_gateway.list_anthropic_models()
    selected = (await get_setting("agent_model")) or _DEFAULT_MODEL
    return {
        "ollama": [f"ollama:{m}" for m in ollama],
        "google": [f"google:{m}" for m in google],
        "groq": [f"groq:{m}" for m in groq],
        "mistral": [f"mistral:{m}" for m in mistral],
        "openai": [f"openai:{m}" for m in openai],
        "anthropic": [f"anthropic:{m}" for m in anthropic],
        "selected": selected,
    }


class SelectModelRequest(BaseModel):
    model: str = Field(..., description="e.g. 'ollama:llama3.2', 'google:gemini-2.0-flash', 'groq:llama-3.3-70b-versatile' or 'mistral:mistral-large-latest'")


@router.post("/model", summary="Set the active LLM model for the agent")
async def set_model(
    body: SelectModelRequest,
    identity: TeamIdentity = Depends(require_auth),
) -> dict[str, Any]:
    from app.services.settings_service import upsert_setting

    await upsert_setting("agent_model", body.model, updated_by=identity.team_name)
    logger.info("agent_model_set", model=body.model, team=identity.team_name)
    return {"success": True, "model": body.model}
