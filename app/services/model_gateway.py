from __future__ import annotations

import json
from typing import Any

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

# Task-type to model routing table
_TASK_MODEL_MAP: dict[str, str] = {
    "chat": "llama3.2",
    "code": "codellama",
    "summary": "llama3.2",
    "embed": "nomic-embed-text",
    "extract": "llama3.2",
    "analysis": "llama3.2",
}


class ModelGateway:
    """Async gateway for Ollama language model calls."""

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._default_model = settings.ollama_default_model
        self._embed_model = settings.ollama_embed_model

    def route_model(self, task_type: str) -> str:
        """Return the model name best suited for the given task type."""
        settings = get_settings()
        mapped = _TASK_MODEL_MAP.get(task_type.lower())
        if mapped:
            return mapped
        return settings.ollama_default_model

    async def generate(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
    ) -> str:
        """Generate a text completion from Ollama. Returns the response string."""
        chosen_model = model or self._default_model
        payload: dict[str, Any] = {
            "model": chosen_model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system

        log = logger.bind(model=chosen_model, task="generate")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._base_url}/api/generate",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                result: str = data.get("response", "")
                log.info("generate_ok", prompt_len=len(prompt), response_len=len(result))
                return result
        except httpx.HTTPStatusError as exc:
            log.error("generate_http_error", status=exc.response.status_code, detail=str(exc))
            raise
        except httpx.RequestError as exc:
            log.error("generate_request_error", detail=str(exc))
            raise

    async def extract_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        """
        Ask the model to return JSON conforming to the provided schema.
        Returns a parsed dict.
        """
        chosen_model = model or self._default_model
        system = (
            "You are a data extraction assistant. "
            "Always respond with valid JSON that matches the provided schema. "
            "Do not include any text outside the JSON object."
        )
        schema_str = json.dumps(schema, indent=2)
        full_prompt = (
            f"Extract structured data according to this JSON schema:\n{schema_str}\n\n"
            f"Input:\n{prompt}"
        )
        raw = await self.generate(prompt=full_prompt, model=chosen_model, system=system)

        # Attempt to parse JSON from the response
        raw = raw.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            # Remove first and last fence lines
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("extract_structured_json_parse_error", error=str(exc), raw=raw[:200])
            return {"raw_response": raw, "parse_error": str(exc)}

    async def embed(
        self,
        text: str,
        model: str | None = None,
    ) -> list[float]:
        """Generate an embedding vector for the given text."""
        chosen_model = model or self._embed_model
        payload = {"model": chosen_model, "prompt": text}

        log = logger.bind(model=chosen_model, task="embed")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self._base_url}/api/embeddings",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                embedding: list[float] = data.get("embedding", [])
                log.info("embed_ok", text_len=len(text), dims=len(embedding))
                return embedding
        except httpx.HTTPStatusError as exc:
            log.error("embed_http_error", status=exc.response.status_code, detail=str(exc))
            raise
        except httpx.RequestError as exc:
            log.error("embed_request_error", detail=str(exc))
            raise

    async def summarize(self, text: str, max_words: int = 150) -> str:
        """Summarize text to at most max_words words."""
        system = (
            "You are a concise summarization assistant. "
            f"Summarize the user's text in at most {max_words} words. "
            "Use plain, clear language."
        )
        return await self.generate(prompt=text, system=system)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
model_gateway = ModelGateway()
