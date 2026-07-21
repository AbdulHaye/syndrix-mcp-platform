from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

# Ollama on CPU can be slow with large tool sets — make the chat timeout tunable.
_OLLAMA_CHAT_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "300"))
# Ollama's default context window (2048) is too small for a large system prompt +
# many tool schemas + multi-step history. Raise it (tunable via env).
_OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))

# Task-type to model routing table
_TASK_MODEL_MAP: dict[str, str] = {
    "chat": "llama3.2",
    "code": "llama3.2",
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
        num_predict: int = 512,
    ) -> str:
        """Generate a text completion from Ollama. Returns the response string."""
        chosen_model = model or self._default_model
        payload: dict[str, Any] = {
            "model": chosen_model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": num_predict},
        }
        if system:
            payload["system"] = system

        log = logger.bind(model=chosen_model, task="generate")
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
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
        except httpx.TimeoutException as exc:
            log.error("generate_timeout", model=chosen_model, detail=str(exc))
            raise RuntimeError(f"Ollama timed out after 180s (model={chosen_model})") from exc
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
            async with httpx.AsyncClient(timeout=60.0) as client:
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

    @staticmethod
    def _split_model(model: str) -> tuple[str, str]:
        """Parse a 'provider:model' string. Defaults to the ollama provider."""
        if ":" in model and model.split(":", 1)[0] in (
            "ollama", "google", "groq", "mistral", "openai", "anthropic", "zai", "openrouter"
        ):
            provider, name = model.split(":", 1)
            return provider, name
        return "ollama", model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        num_predict: int = 512,
    ) -> dict[str, Any]:
        """
        Provider-agnostic multi-turn chat with optional tool calling.

        ``model`` may be ``"ollama:llama3.2"``, ``"google:gemini-2.0-flash"``,
        ``"groq:llama-3.3-70b-versatile"`` or ``"mistral:mistral-large-latest"``
        (a bare name defaults to Ollama). Returns the assistant message in a common
        shape: ``{"role": "assistant", "content": str, "tool_calls": [...]}`` where
        each tool call is ``{"function": {"name", "arguments"}}``.
        """
        provider, name = self._split_model(model or self._default_model)
        if provider == "google":
            return await self._google_chat(messages, tools, name, num_predict)
        if provider == "groq":
            return await self._groq_chat(messages, tools, name, num_predict)
        if provider == "mistral":
            return await self._mistral_chat(messages, tools, name, num_predict)
        if provider == "openai":
            return await self._openai_chat(messages, tools, name, num_predict)
        if provider == "anthropic":
            return await self._anthropic_chat(messages, tools, name, num_predict)
        if provider == "zai":
            return await self._zai_chat(messages, tools, name, num_predict)
        if provider == "openrouter":
            return await self._openrouter_chat(messages, tools, name, num_predict)
        return await self._ollama_chat(messages, tools, name, num_predict)

    async def _ollama_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model_name: str,
        num_predict: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": num_predict, "num_ctx": _OLLAMA_NUM_CTX},
        }
        if tools:
            payload["tools"] = tools

        log = logger.bind(model=model_name, task="chat", provider="ollama", with_tools=bool(tools))
        _RETRY_DELAYS = [1, 2, 4]  # seconds between transient connection-error retries
        for attempt, retry_delay in enumerate([0] + _RETRY_DELAYS):
            if retry_delay:
                log.warning("chat_connection_retry", attempt=attempt, wait=retry_delay)
                await asyncio.sleep(retry_delay)
            try:
                async with httpx.AsyncClient(timeout=_OLLAMA_CHAT_TIMEOUT) as client:
                    response = await client.post(f"{self._base_url}/api/chat", json=payload)
                    response.raise_for_status()
                    data = response.json()
                    message: dict[str, Any] = data.get("message", {}) or {}
                    if "content" in message:
                        message["content"] = self._content_to_text(message.get("content"))
                    log.info("chat_ok", msg_count=len(messages), tool_calls=len(message.get("tool_calls") or []))
                    return message
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500]
                log.error("chat_http_error", status=exc.response.status_code, detail=detail)
                # Surface Ollama's actual message (e.g. "<model> does not support tools").
                raise RuntimeError(f"Ollama API {exc.response.status_code}: {detail}") from exc
            except httpx.TimeoutException as exc:
                log.error("chat_timeout", model=model_name, timeout=_OLLAMA_CHAT_TIMEOUT, detail=str(exc))
                raise RuntimeError(
                    f"Ollama timed out after {int(_OLLAMA_CHAT_TIMEOUT)}s (model={model_name}). "
                    "This local model is slow with many tools — try a Gemini model, or set "
                    "OLLAMA_TIMEOUT higher."
                ) from exc
            except httpx.TransportError as exc:
                if attempt < len(_RETRY_DELAYS):
                    continue
                log.error("chat_connection_error", detail=str(exc))
                raise RuntimeError(f"Could not connect to Ollama at {self._base_url}: {exc}") from exc
        raise RuntimeError("Ollama chat failed after retries")  # unreachable; satisfies type checker

    # ── Google AI Studio (Gemini) ─────────────────────────────────────────────

    _GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

    async def _google_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model_name: str,
        num_predict: int,
    ) -> dict[str, Any]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("google_api_key")
        if not api_key:
            raise RuntimeError("Google API key is not configured (Settings → Google AI Studio).")

        system_text, contents = self._to_gemini_contents(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max(num_predict, 1024)},
        }
        if system_text:
            body["system_instruction"] = {"parts": [{"text": system_text}]}
        if tools:
            decls = [self._sanitize_decl(t["function"]) for t in tools]
            body["tools"] = [{"function_declarations": decls}]

        url = f"{self._GEMINI_BASE}/models/{model_name}:generateContent?key={api_key}"
        log = logger.bind(model=model_name, task="chat", provider="google", with_tools=bool(tools))
        _RETRY_DELAYS = [1, 2, 4]  # seconds between transient connection-error retries
        data: dict[str, Any] = {}
        for attempt, retry_delay in enumerate([0] + _RETRY_DELAYS):
            if retry_delay:
                log.warning("chat_connection_retry", attempt=attempt, wait=retry_delay)
                await asyncio.sleep(retry_delay)
            try:
                async with httpx.AsyncClient(timeout=180.0) as client:
                    response = await client.post(url, json=body)
                    response.raise_for_status()
                    data = response.json()
                    break
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500]
                log.error("chat_http_error", status=exc.response.status_code, detail=detail)
                raise RuntimeError(f"Gemini API {exc.response.status_code}: {detail}") from exc
            except httpx.TimeoutException as exc:
                if attempt < len(_RETRY_DELAYS):
                    continue
                log.error("chat_timeout", model=model_name, detail=str(exc))
                raise RuntimeError(f"Gemini timed out (model={model_name})") from exc
            except httpx.TransportError as exc:
                if attempt < len(_RETRY_DELAYS):
                    continue
                log.error("chat_connection_error", model=model_name, detail=str(exc))
                raise RuntimeError(f"Could not connect to Gemini's API after retries (model={model_name}): {exc}") from exc

        message = self._from_gemini_response(data)
        log.info("chat_ok", msg_count=len(messages), tool_calls=len(message.get("tool_calls") or []))
        return message

    def _to_gemini_contents(self, messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """Convert common-format messages into Gemini system_instruction + contents."""
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        pending_responses: list[dict[str, Any]] = []

        def flush() -> None:
            if pending_responses:
                contents.append({"role": "user", "parts": list(pending_responses)})
                pending_responses.clear()

        for m in messages:
            role = m.get("role")
            if role == "system":
                if m.get("content"):
                    system_parts.append(m["content"])
            elif role == "tool":
                name = m.get("tool_name") or "tool"
                raw = m.get("content", "")
                try:
                    parsed = json.loads(raw)
                    response = parsed if isinstance(parsed, dict) else {"result": parsed}
                except (json.JSONDecodeError, TypeError):
                    response = {"result": raw}
                pending_responses.append({"functionResponse": {"name": name, "response": response}})
            elif role == "assistant":
                flush()
                parts: list[dict[str, Any]] = []
                if m.get("content"):
                    parts.append({"text": m["content"]})
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function", {}) or {}
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    parts.append({"functionCall": {"name": fn.get("name"), "args": args}})
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            else:  # user
                flush()
                contents.append({"role": "user", "parts": [{"text": m.get("content", "")}]})

        flush()
        return "\n\n".join(system_parts), contents

    @staticmethod
    def _from_gemini_response(data: dict[str, Any]) -> dict[str, Any]:
        candidates = data.get("candidates") or []
        parts = ((candidates[0] if candidates else {}).get("content") or {}).get("parts") or []
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for p in parts:
            if "text" in p:
                text_parts.append(p["text"])
            if "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append({"function": {"name": fc.get("name"), "arguments": fc.get("args") or {}}})
        message: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts)}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message

    @classmethod
    def _sanitize_decl(cls, fn: dict[str, Any]) -> dict[str, Any]:
        """Strip JSON-schema keys Gemini's function declarations reject."""
        return {
            "name": fn.get("name"),
            "description": fn.get("description", ""),
            "parameters": cls._sanitize_schema(fn.get("parameters") or {"type": "object", "properties": {}}),
        }

    @classmethod
    def _sanitize_schema(cls, schema: Any) -> Any:
        if isinstance(schema, dict):
            drop = {"$schema", "additionalProperties", "default", "examples", "title", "const", "$id"}
            out = {}
            for k, v in schema.items():
                if k in drop:
                    continue
                out[k] = cls._sanitize_schema(v)
            return out
        if isinstance(schema, list):
            return [cls._sanitize_schema(v) for v in schema]
        return schema

    # ── OpenAI-compatible providers (Groq, Mistral) ────────────────────────────

    _GROQ_BASE = "https://api.groq.com/openai/v1"
    _MISTRAL_BASE = "https://api.mistral.ai/v1"
    _OPENAI_BASE = "https://api.openai.com/v1"
    _ZAI_BASE = "https://api.z.ai/api/paas/v4"
    # z.ai (unlike Groq/Mistral/OpenAI) has no confirmed public /models listing
    # endpoint — used as a fallback in list_zai_models() so a valid key never shows
    # an empty dropdown just because that endpoint 404s.
    _ZAI_KNOWN_MODELS = ("glm-5.2", "glm-5.1", "glm-4.7", "glm-4.7-flash", "glm-4.6", "glm-4.6v-flash")
    # OpenRouter — a single OpenAI-compatible gateway in front of ~300 models from
    # every major provider (Anthropic, OpenAI, Google, Meta, Mistral, DeepSeek,
    # etc.), useful as a fallback/aggregator when a user wants a specific upstream
    # model without adding that provider's own key separately. Model ids are
    # "provider/model" (e.g. "anthropic/claude-3.5-sonnet", "openai/gpt-4o").
    _OPENROUTER_BASE = "https://openrouter.ai/api/v1"

    async def _openai_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model_name: str,
        num_predict: int,
    ) -> dict[str, Any]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("openai_api_key")
        if not api_key:
            raise RuntimeError("OpenAI API key is not configured (Settings → OpenAI (GPT)).")
        return await self._openai_compat_chat(
            messages, tools, model_name, num_predict,
            base_url=self._OPENAI_BASE, api_key=api_key, provider="OpenAI",
        )

    async def _groq_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model_name: str,
        num_predict: int,
    ) -> dict[str, Any]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("groq_api_key")
        if not api_key:
            raise RuntimeError("Groq API key is not configured (Settings → Groq).")
        return await self._openai_compat_chat(
            messages, tools, model_name, num_predict,
            base_url=self._GROQ_BASE, api_key=api_key, provider="Groq",
        )

    async def _mistral_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model_name: str,
        num_predict: int,
    ) -> dict[str, Any]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("mistral_api_key")
        if not api_key:
            raise RuntimeError("Mistral API key is not configured (Settings → Mistral AI).")
        return await self._openai_compat_chat(
            messages, tools, model_name, num_predict,
            base_url=self._MISTRAL_BASE, api_key=api_key, provider="Mistral",
        )

    async def _zai_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model_name: str,
        num_predict: int,
    ) -> dict[str, Any]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("zai_api_key")
        if not api_key:
            raise RuntimeError("Z.ai API key is not configured (Settings → Z.ai (GLM)).")
        return await self._openai_compat_chat(
            messages, tools, model_name, num_predict,
            base_url=self._ZAI_BASE, api_key=api_key, provider="Z.ai",
        )

    async def _openrouter_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model_name: str,
        num_predict: int,
    ) -> dict[str, Any]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("openrouter_api_key")
        if not api_key:
            raise RuntimeError("OpenRouter API key is not configured (Settings → OpenRouter).")
        return await self._openai_compat_chat(
            messages, tools, model_name, num_predict,
            base_url=self._OPENROUTER_BASE, api_key=api_key, provider="OpenRouter",
        )

    async def _openai_compat_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model_name: str,
        num_predict: int,
        *,
        base_url: str,
        api_key: str,
        provider: str,
    ) -> dict[str, Any]:
        """Chat against any OpenAI-compatible /chat/completions endpoint."""
        body: dict[str, Any] = {
            "model": model_name,
            "messages": self._to_openai_messages(messages),
            "max_tokens": max(num_predict, 1024),
        }
        if tools:
            # Our tool specs are already OpenAI-shaped: {"type": "function", "function": {...}}.
            body["tools"] = tools
            body["tool_choice"] = "auto"

        headers = {"Authorization": f"Bearer {api_key}"}
        log = logger.bind(model=model_name, task="chat", provider=provider.lower(), with_tools=bool(tools))
        _RETRY_DELAYS = [2, 5, 15]  # seconds between 429 retries
        data: dict[str, Any] = {}
        for attempt, retry_delay in enumerate([0] + _RETRY_DELAYS):
            if retry_delay:
                log.warning("chat_rate_limited_retry", attempt=attempt, wait=retry_delay)
                await asyncio.sleep(retry_delay)
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        f"{base_url}/chat/completions", json=body, headers=headers
                    )
                    if response.status_code == 429 and attempt < len(_RETRY_DELAYS):
                        continue  # retry after backoff
                    response.raise_for_status()
                    data = response.json()
                    break
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500]
                log.error("chat_http_error", status=exc.response.status_code, detail=detail)
                raise RuntimeError(f"{provider} API {exc.response.status_code}: {detail}") from exc
            except httpx.TimeoutException as exc:
                if attempt < len(_RETRY_DELAYS):
                    continue
                log.error("chat_timeout", model=model_name, detail=str(exc))
                raise RuntimeError(f"{provider} timed out (model={model_name})") from exc
            except httpx.TransportError as exc:
                if attempt < len(_RETRY_DELAYS):
                    continue
                log.error("chat_connection_error", model=model_name, detail=str(exc))
                raise RuntimeError(f"Could not connect to {provider}'s API after retries (model={model_name}): {exc}") from exc

        message = self._from_openai_response(data)
        log.info("chat_ok", msg_count=len(messages), tool_calls=len(message.get("tool_calls") or []))
        return message

    @staticmethod
    def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert our common message format into strict OpenAI chat messages.

        OpenAI requires assistant ``tool_calls`` to carry an ``id`` and stringified
        ``arguments``, and each following ``tool`` message to reference that id via
        ``tool_call_id``. Our common format omits ids, so we synthesise them and
        match assistant calls to tool results in FIFO order (the agent loop always
        appends one tool message per call, in order). IDs are 9 alphanumeric chars
        because Mistral requires exactly that (Groq accepts any string).
        """
        out: list[dict[str, Any]] = []
        pending_ids: list[str] = []
        counter = 0

        for m in messages:
            role = m.get("role")
            if role == "assistant" and m.get("tool_calls"):
                tcs: list[dict[str, Any]] = []
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {}) or {}
                    args = fn.get("arguments")
                    if isinstance(args, (dict, list)):
                        args = json.dumps(args)
                    elif args is None:
                        args = "{}"
                    call_id = f"tc{counter:07d}"  # 9 alphanumeric chars (Mistral-safe)
                    counter += 1
                    pending_ids.append(call_id)
                    tcs.append({
                        "id": call_id,
                        "type": "function",
                        "function": {"name": fn.get("name"), "arguments": args},
                    })
                out.append({
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": tcs,
                })
            elif role == "tool":
                call_id = pending_ids.pop(0) if pending_ids else "tcorphan0"
                entry: dict[str, Any] = {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": m.get("content", ""),
                }
                if m.get("tool_name"):
                    entry["name"] = m["tool_name"]
                out.append(entry)
            else:  # system / user / plain assistant
                content = m.get("content") or ""
                # Mistral rejects empty-string content on assistant messages — use None.
                if role == "assistant" and not content:
                    content = None
                out.append({"role": role, "content": content})

        return out

    @staticmethod
    def _from_openai_response(data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices") or []
        msg = (choices[0] if choices else {}).get("message") or {}
        tool_calls: list[dict[str, Any]] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {}) or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append({"function": {"name": fn.get("name"), "arguments": args or {}}})
        message: dict[str, Any] = {
            "role": "assistant",
            "content": ModelGateway._content_to_text(msg.get("content")),
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message

    @staticmethod
    def _content_to_text(content: Any) -> str:
        """Flatten an LLM ``content`` value to plain text.

        Most providers return a string, but some reasoning models return a LIST of
        content blocks (e.g. a ``thinking`` block + a ``text`` block). We keep the
        answer text and drop reasoning blocks so the reply renders as a string.
        """
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for p in content:
                if isinstance(p, dict):
                    if p.get("type") in ("thinking", "reasoning"):
                        continue
                    if isinstance(p.get("text"), str):
                        parts.append(p["text"])
                    elif p.get("text") is not None:
                        parts.append(ModelGateway._content_to_text(p["text"]))
                    elif "content" in p:
                        parts.append(ModelGateway._content_to_text(p["content"]))
                elif isinstance(p, str):
                    parts.append(p)
            return "\n".join(x for x in parts if x)
        if isinstance(content, dict):
            if "text" in content:
                return ModelGateway._content_to_text(content["text"])
            if "content" in content:
                return ModelGateway._content_to_text(content["content"])
            return ""
        return str(content)

    # ── Anthropic (Claude) ─────────────────────────────────────────────────────

    _ANTHROPIC_BASE = "https://api.anthropic.com/v1"
    _ANTHROPIC_VERSION = "2023-06-01"

    async def _anthropic_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model_name: str,
        num_predict: int,
    ) -> dict[str, Any]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("anthropic_api_key")
        if not api_key:
            raise RuntimeError("Anthropic API key is not configured (Settings → Anthropic (Claude)).")

        system_text, a_messages = self._to_anthropic_messages(messages)
        body: dict[str, Any] = {
            "model": model_name,
            "max_tokens": max(num_predict, 1024),
            "messages": a_messages,
        }
        # Prompt caching: the system prompt and tool schemas are identical across
        # every step of a multi-step tool-calling loop (only the growing message
        # history changes) — marking them cacheable means a 10-step run pays full
        # price once instead of on every step. Anthropic processes tools -> system
        # -> messages internally, so a breakpoint at the end of each caches
        # everything up to and including it; two separate breakpoints because the
        # tools list is stable across an entire conversation while the system
        # prompt's date header changes turn-to-turn (still stable WITHIN one
        # multi-step turn, which is where the repetition actually happens).
        if system_text:
            body["system"] = [
                {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
            ]
        if tools:
            tool_defs = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": self._sanitize_schema(
                        t["function"].get("parameters") or {"type": "object", "properties": {}}
                    ),
                }
                for t in tools
            ]
            tool_defs[-1]["cache_control"] = {"type": "ephemeral"}
            body["tools"] = tool_defs

        headers = {
            "x-api-key": api_key,
            "anthropic-version": self._ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        log = logger.bind(model=model_name, task="chat", provider="anthropic", with_tools=bool(tools))
        _RETRY_DELAYS = [1, 2, 4]  # seconds between transient connection-error retries
        data: dict[str, Any] = {}
        for attempt, retry_delay in enumerate([0] + _RETRY_DELAYS):
            if retry_delay:
                log.warning("chat_connection_retry", attempt=attempt, wait=retry_delay)
                await asyncio.sleep(retry_delay)
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(f"{self._ANTHROPIC_BASE}/messages", json=body, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    break
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500]
                log.error("chat_http_error", status=exc.response.status_code, detail=detail)
                raise RuntimeError(f"Anthropic API {exc.response.status_code}: {detail}") from exc
            except httpx.TimeoutException as exc:
                if attempt < len(_RETRY_DELAYS):
                    continue
                log.error("chat_timeout", model=model_name, detail=str(exc))
                raise RuntimeError(f"Anthropic timed out (model={model_name})") from exc
            except httpx.TransportError as exc:
                if attempt < len(_RETRY_DELAYS):
                    continue
                log.error("chat_connection_error", model=model_name, detail=str(exc))
                raise RuntimeError(f"Could not connect to Anthropic's API after retries (model={model_name}): {exc}") from exc

        message = self._from_anthropic_response(data)
        usage = data.get("usage") or {}
        log.info(
            "chat_ok",
            msg_count=len(messages),
            tool_calls=len(message.get("tool_calls") or []),
            cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            input_tokens=usage.get("input_tokens", 0),
        )
        return message

    @staticmethod
    def _to_anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """Convert common messages → (system string, Anthropic messages).

        Anthropic puts system text at the top level, represents assistant tool calls
        as ``tool_use`` content blocks, and tool results as ``tool_result`` blocks in
        a following user message. We synthesise tool_use ids and match results FIFO.
        """
        system_parts: list[str] = []
        out: list[dict[str, Any]] = []
        pending_results: list[dict[str, Any]] = []
        pending_ids: list[str] = []
        counter = 0

        def flush_results() -> None:
            nonlocal pending_results
            if pending_results:
                out.append({"role": "user", "content": list(pending_results)})
                pending_results = []

        for m in messages:
            role = m.get("role")
            if role == "system":
                txt = ModelGateway._content_to_text(m.get("content"))
                if txt:
                    system_parts.append(txt)
            elif role == "tool":
                tool_use_id = pending_ids.pop(0) if pending_ids else "toolu_orphan"
                pending_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": ModelGateway._content_to_text(m.get("content")) or "",
                })
            elif role == "assistant":
                flush_results()
                blocks: list[dict[str, Any]] = []
                txt = ModelGateway._content_to_text(m.get("content"))
                if txt:
                    blocks.append({"type": "text", "text": txt})
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function", {}) or {}
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    tid = f"toolu_{counter:08d}"
                    counter += 1
                    pending_ids.append(tid)
                    blocks.append({"type": "tool_use", "id": tid, "name": fn.get("name"), "input": args})
                if not blocks:
                    blocks = [{"type": "text", "text": "(no content)"}]
                out.append({"role": "assistant", "content": blocks})
            else:  # user
                flush_results()
                user_txt = ModelGateway._content_to_text(m.get("content"))
                out.append({
                    "role": "user",
                    "content": [{"type": "text", "text": user_txt if user_txt.strip() else "(no content)"}],
                })

        flush_results()
        return "\n\n".join(system_parts), out

    @staticmethod
    def _from_anthropic_response(data: dict[str, Any]) -> dict[str, Any]:
        blocks = data.get("content") or []
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for b in blocks:
            if b.get("type") == "text":
                text_parts.append(b.get("text", ""))
            elif b.get("type") == "tool_use":
                tool_calls.append({"function": {"name": b.get("name"), "arguments": b.get("input") or {}}})
        message: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts)}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message

    # ── Model discovery ────────────────────────────────────────────────────────

    async def list_ollama_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
            return [m["name"] for m in data.get("models", []) if m.get("name")]
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_ollama_models_failed", error=str(exc))
            return []

    async def list_google_models(self) -> list[str]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("google_api_key")
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{self._GEMINI_BASE}/models?key={api_key}&pageSize=200")
                resp.raise_for_status()
                data = resp.json()
            names: list[str] = []
            for m in data.get("models", []):
                methods = m.get("supportedGenerationMethods") or []
                name = (m.get("name") or "").removeprefix("models/")
                if "generateContent" in methods and name.startswith("gemini"):
                    names.append(name)
            return names
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_google_models_failed", error=str(exc))
            return []

    async def list_groq_models(self) -> list[str]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("groq_api_key")
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self._GROQ_BASE}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
            names: list[str] = []
            for m in data.get("data", []):
                mid = m.get("id")
                if not mid:
                    continue
                # Skip non-chat models (speech/embeddings/moderation) — the agent
                # needs tool-capable chat LLMs.
                low = mid.lower()
                if any(x in low for x in ("whisper", "tts", "embed", "guard")):
                    continue
                names.append(mid)
            return sorted(names)
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_groq_models_failed", error=str(exc))
            return []

    async def list_mistral_models(self) -> list[str]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("mistral_api_key")
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self._MISTRAL_BASE}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
            names: list[str] = []
            for m in data.get("data", []):
                mid = m.get("id")
                if not mid:
                    continue
                # Skip non-chat models (embeddings/OCR/moderation) — the agent
                # needs tool-capable chat LLMs.
                low = mid.lower()
                if any(x in low for x in ("embed", "ocr", "moderation")):
                    continue
                names.append(mid)
            return sorted(set(names))
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_mistral_models_failed", error=str(exc))
            return []

    async def list_zai_models(self) -> list[str]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("zai_api_key")
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self._ZAI_BASE}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            if resp.status_code == 404:
                # No models-listing endpoint at this path — fall back to the known
                # current GLM chat/tool-calling model ids rather than an empty
                # dropdown that gives no signal why (same failure shape as the
                # truncated-OpenAI-key incident — see CLAUDE.md session log).
                return list(self._ZAI_KNOWN_MODELS)
            resp.raise_for_status()
            data = resp.json()
            names = [m.get("id") for m in data.get("data", []) if m.get("id")]
            return sorted(set(names)) if names else list(self._ZAI_KNOWN_MODELS)
        except Exception as exc:  # noqa: BLE001
            # A genuine auth/network failure — stay empty like every other provider
            # (an invalid key must NOT show models it can't actually call).
            logger.warning("list_zai_models_failed", error=str(exc))
            return []

    async def list_openrouter_models(self) -> list[str]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("openrouter_api_key")
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self._OPENROUTER_BASE}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
            names: list[str] = []
            for m in data.get("data", []):
                mid = m.get("id")
                if not mid:
                    continue
                # OpenRouter's catalog spans ~300 models across every modality
                # (text, vision, audio, embeddings) from every upstream provider —
                # this app's agents all require function-calling, so keep the
                # dropdown to models that actually declare "tools" support in their
                # supported_parameters rather than dumping the entire catalog
                # (most of which would just fail if picked for an agent).
                if "tools" not in (m.get("supported_parameters") or []):
                    continue
                names.append(mid)
            return sorted(set(names))
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_openrouter_models_failed", error=str(exc))
            return []

    async def list_openai_models(self) -> list[str]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("openai_api_key")
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self._OPENAI_BASE}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
            names: list[str] = []
            for m in data.get("data", []):
                mid = m.get("id")
                if not mid:
                    continue
                low = mid.lower()
                if any(x in low for x in (
                    "embedding", "whisper", "tts", "audio", "image", "dall-e",
                    "moderation", "realtime", "transcribe", "search", "babbage", "davinci",
                )):
                    continue
                if low.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
                    names.append(mid)
            return sorted(set(names))
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_openai_models_failed", error=str(exc))
            return []

    async def list_anthropic_models(self) -> list[str]:
        from app.services.settings_service import get_setting

        api_key = await get_setting("anthropic_api_key")
        if not api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self._ANTHROPIC_BASE}/models?limit=100",
                    headers={"x-api-key": api_key, "anthropic-version": self._ANTHROPIC_VERSION},
                )
                resp.raise_for_status()
                data = resp.json()
            names = [m["id"] for m in data.get("data", []) if m.get("id")]
            return sorted(set(names), reverse=True)  # newest-looking ids first
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_anthropic_models_failed", error=str(exc))
            return []


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
model_gateway = ModelGateway()
