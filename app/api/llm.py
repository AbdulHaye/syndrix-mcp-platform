from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.bearer import TeamIdentity, require_auth

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/llm", tags=["llm"])

_DEFAULT_MODEL = "ollama:llama3.2"

# Each agent persists its own selected model under its own settings key, so picking
# a model for one agent (e.g. MyCase) never affects another (e.g. Podio).
_AGENT_SETTING_KEYS = {
    "podio": "agent_model",
    "mycase": "mycase_agent_model",
}


def _model_setting_key(agent: str) -> str:
    key = _AGENT_SETTING_KEYS.get(agent)
    if key is None:
        raise HTTPException(status_code=400, detail=f"Unknown agent '{agent}'. Expected one of {sorted(_AGENT_SETTING_KEYS)}.")
    return key


@router.get("/models", summary="List available LLM models (all providers) and the selected one for an agent")
async def list_models(
    agent: str = "podio",
    identity: TeamIdentity = Depends(require_auth),
) -> dict[str, Any]:
    from app.services.model_gateway import model_gateway
    from app.services.settings_service import get_setting

    setting_key = _model_setting_key(agent)
    ollama = await model_gateway.list_ollama_models()
    google = await model_gateway.list_google_models()
    groq = await model_gateway.list_groq_models()
    mistral = await model_gateway.list_mistral_models()
    openai = await model_gateway.list_openai_models()
    anthropic = await model_gateway.list_anthropic_models()
    zai = await model_gateway.list_zai_models()
    selected = (await get_setting(setting_key)) or _DEFAULT_MODEL
    return {
        "ollama": [f"ollama:{m}" for m in ollama],
        "google": [f"google:{m}" for m in google],
        "groq": [f"groq:{m}" for m in groq],
        "mistral": [f"mistral:{m}" for m in mistral],
        "openai": [f"openai:{m}" for m in openai],
        "anthropic": [f"anthropic:{m}" for m in anthropic],
        "zai": [f"zai:{m}" for m in zai],
        "selected": selected,
    }


class SelectModelRequest(BaseModel):
    model: str = Field(..., description="e.g. 'ollama:llama3.2', 'google:gemini-2.0-flash', 'groq:llama-3.3-70b-versatile' or 'mistral:mistral-large-latest'")
    agent: str = Field("podio", description="Which agent this selection applies to: 'podio' or 'mycase'")


@router.post("/model", summary="Set the active LLM model for an agent")
async def set_model(
    body: SelectModelRequest,
    identity: TeamIdentity = Depends(require_auth),
) -> dict[str, Any]:
    from app.services.settings_service import upsert_setting

    setting_key = _model_setting_key(body.agent)
    await upsert_setting(setting_key, body.model, updated_by=identity.team_name)
    logger.info("agent_model_set", model=body.model, agent=body.agent, team=identity.team_name)
    return {"success": True, "model": body.model, "agent": body.agent}
