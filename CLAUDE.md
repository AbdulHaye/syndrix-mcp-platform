# Syndrix — Project Handbook

> **Rule:** Every Claude session MUST read this file first. Do not rely on terminal history or prior conversation. After completing work, append a brief entry to the [Session Log](#session-log) at the bottom.

---

## What This Project Is

An internal MCP server acting as a **shared AI capability hub** for three teams:

| Team | Role key | Typical tools |
|------|----------|---------------|
| BD (Business Development) | `bd` | CRM contacts, leads, notes, email, Podio Agent |
| Software Dev | `dev` | Repo search, tickets, spec gen, bug triage |
| Management | `mgmt` | Daily reports, client health scores |

**Design rule:** The MCP server is a capability backend, NOT an agent. Business rules stay above the tool layer. Adapters go in `app/adapters/`, tools go in `app/mcp_tools/`.

---

## Stack

| Layer | Technology |
|-------|-----------|
| API framework | FastAPI + FastMCP (MCP Python SDK) |
| Language | Python 3.11 |
| Database | PostgreSQL + pgvector (asyncpg + SQLAlchemy async) |
| Cache / broker | Redis |
| Local LLM | Ollama (`llama3.2` default, `nomic-embed-text` embeddings) |
| Background jobs | Celery (broker = Redis) |
| Runtime | Local (uvicorn + native Postgres/Redis/Ollama installs) |
| Migrations | Alembic |
| Frontend | Next.js 14 (App Router), Bootstrap 5, TypeScript |

---

## Critical File Map

```
app/
├── config.py                    ← pydantic-settings, get_settings(), load_dotenv() at import
├── main.py                      ← FastAPI + FastMCP mount + lifespan (bootstraps admin user)
├── auth/
│   ├── bearer.py                ← verify_token(): tries JWT first, falls back to DEV_TOKENS
│   ├── jwt_utils.py             ← create_access_token() / decode_access_token()
│   └── rbac.py                  ← TOOL_PERMISSIONS map + can_use_tool()
├── api/
│   ├── auth.py                  ← POST /auth/login, GET /auth/me
│   ├── admin.py                 ← /admin/users CRUD + /admin/metrics
│   ├── tools.py                 ← POST /tools/invoke (UI tool runner, RBAC enforced)
│   ├── ingest.py                ← POST /ingest (RAG document ingestion)
│   ├── agent.py                 ← POST /agent/podio (BD+Admin only) + /agent/podio/sessions CRUD (DB-backed chat history)
│   ├── mycase_agent.py          ← POST /agent/mycase (BD+Admin only) + /agent/mycase/sessions CRUD + /agent/mycase/status
│   ├── integrations.py          ← /integrations/podio/* (OAuth PKCE for hosted MCP)
│   ├── podio_files.py           ← /integrations/podio-files/* (REST OAuth + file upload)
│   └── llm.py                   ← GET /llm/models?agent=podio|mycase, POST /llm/model (agent-scoped selection)
├── adapters/                    ← Thin HTTP wrappers (podio, ghl, slack, github, email)
│   └── podio.py                 ← LEGACY REST adapter — NOT used by agent (client_credentials 403s)
├── mcp_tools/                   ← MCP tool registrations (crm, dev, mgmt, rag, prompt, memory)
├── mcp_servers/
│   ├── podio_files.py           ← Standalone FastMCP server: 50+ Podio REST tools
│   └── mycase.py                ← Standalone FastMCP server: 46 MyCase GET/read tools
├── services/
│   ├── podio_mcp.py             ← OAuth PKCE + MCP client for mcp.podio.com (hosted MCP)
│   ├── podio_rest.py            ← Full Podio REST API client (60+ methods, auth_code OAuth)
│   ├── podio_files_client.py    ← In-process adapter: calls podio_files FastMCP server
│   ├── podio_agent.py           ← Agent loop: LLM + Podio tools → reply
│   ├── mycase_rest.py           ← MyCase REST client (46 GET methods, bearer-token auth)
│   ├── mycase_client.py         ← In-process adapter: calls mycase FastMCP server
│   ├── mycase_agent.py          ← Agent loop: LLM + MyCase read-only tools → reply
│   ├── model_gateway.py         ← Multi-provider LLM: chat(), generate(), embed()
│   ├── settings_service.py      ← DB-backed key-value settings (integration_settings table)
│   ├── rag.py                   ← RAGService: chunk + embed + pgvector store + hybrid search
│   ├── audit.py                 ← AuditService
│   └── memory.py                ← MemoryService (Redis short-term + pgvector long-term)
├── storage/
│   ├── db.py                    ← Async SQLAlchemy engine + session
│   ├── models.py                ← ORM: AuditLog, KnowledgeDocument, TeamToken, User, IntegrationSetting, PodioChatSession, MyCaseChatSession
│   └── vector.py                ← VectorStore: store() + search()
└── workers/
    ├── jobs.py                  ← Celery tasks with exponential backoff
    └── periodic.py              ← Celery Beat schedule

frontend/
├── app/
│   ├── page.tsx                 ← Dark landing page; redirects auth'd users to /dashboard
│   ├── login/page.tsx           ← Email + password login form (JWT)
│   ├── landing.css              ← Landing page styles (extracted from page.tsx to fix hydration)
│   └── dashboard/
│       ├── layout.tsx           ← Sidebar + AuthGuard + ToastProvider
│       ├── page.tsx             ← Overview with stats + tool grid
│       ├── podio-agent/page.tsx ← Podio Agent chat UI (main active feature)
│       ├── mycase-agent/page.tsx← MyCase Agent chat UI (read-only; no OAuth-connect/workspace UI)
│       ├── settings/page.tsx    ← Credentials UI (always-visible integrations + opt-in LLM providers)
│       ├── crm/page.tsx, dev/page.tsx, mgmt/page.tsx, rag/page.tsx, admin/page.tsx
│       └── prompts/page.tsx     ← Prompt Packs UI
├── components/
│   ├── Sidebar.tsx              ← Dark fixed sidebar; Podio Agent + MyCase Agent are top-level under Team Tools
│   ├── Markdown.tsx             ← Dependency-free Markdown renderer for agent replies
│   └── JsonTree.tsx             ← Collapsible JSON tree for tool-call step cards
└── lib/
    ├── api.ts                   ← All backend API calls (all errors prefixed "status: detail")
    └── auth.ts                  ← localStorage + cookie token management
```

---

## Auth

- **JWT:** `POST /auth/login` → JWT (24h). Default admin: `admin@syndrix.local` / `changeme`.
- **DEV_TOKENS fallback:** `bearer.py` tries JWT first, then `DEV_TOKENS` env var (format `team:token,...`).
- **Roles:** `bd`, `dev`, `mgmt`, `admin` (enum `TeamRole`).
- **RBAC:** `app/auth/rbac.py` → `TOOL_PERMISSIONS` maps prefix → allowed roles (`crm.*` → BD+ADMIN, `repo.*`/`ticket.*` → DEV+ADMIN, `report.*` → MGMT+ADMIN, `agent.*` → BD+ADMIN, `health.*` → all).

---

## Environment Variables

```
APP_ENV=development
SECRET_KEY=...
DATABASE_URL=postgresql+asyncpg://mcp_user:mcp_pass@localhost:5432/syndrix
REDIS_URL=redis://localhost:6379/0
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=llama3.2
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_TIMEOUT=300          # agent uses long tool chains
OLLAMA_NUM_CTX=8192         # must be >2048 for system prompt + tool schemas to fit
PGVECTOR_ENABLED=false      # set true only if pgvector extension is installed
DEV_TOKENS=bd_team:bd-secret-token-123,...
JWT_SECRET_KEY=...
ADMIN_EMAIL=admin@syndrix.local
ADMIN_PASSWORD=changeme
PODIO_CLIENT_ID=            # used by both hosted MCP OAuth and REST OAuth (shared)
PODIO_CLIENT_SECRET=
GHL_API_KEY=, SLACK_BOT_TOKEN=, GITHUB_TOKEN=, GITHUB_ORG=, SMTP_HOST/PORT/USER/PASSWORD/FROM=
```

---

## Running Locally

```bash
# One-time DB setup
# psql: CREATE USER mcp_user WITH PASSWORD 'mcp_pass'; CREATE DATABASE syndrix OWNER mcp_user;
pip install -r requirements.txt
alembic upgrade head                              # creates all tables incl. users
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
cd frontend && npm install && npm run dev         # http://localhost:3000
celery -A app.workers.jobs worker --loglevel=info # optional background jobs
```

---

## ⭐ Podio Agent — The Main Active Feature

### Architecture in one paragraph
A chat page at `/dashboard/podio-agent` where users type natural language and an LLM decides which Podio tools to call. There are **two separate Podio connections** running in parallel:

1. **Podio Hosted MCP** (`mcp.podio.com`) — read/filter/search tools via `podio_mcp.py`. Uses OAuth authorization-code + PKCE (browser login). Required for `get_items`, `get_app`, `search_globally`, etc.
2. **Custom REST layer** (`podio_rest.py` + `mcp_servers/podio_files.py`) — write operations, file upload/download, flows, webhooks, tasks, workspace management, conversations, calendar. Uses its own OAuth authorization-code flow (separate "Connect Files" button). Required for `create_item`, `update_item`, `delete_item`, `create_flow`, `attach_file`, etc.

**Why two connections?** `client_credentials` OAuth (our original approach) authenticates only the API key, not a user — every data call returns `403 "Authentication as None is not allowed"`. Both connections need browser login.

### Connection setup (do this when it "doesn't work")
1. Restart backend after any code change.
2. **Settings → Podio (MCP):** enter Client ID + Secret. Redirect URI: `http://localhost:8000/integrations/podio/callback`.
3. **Podio Agent → Connect Podio** → browser login → connected (hosted MCP active).
4. **Podio Agent → Connect Files** → browser login → connected (REST layer active). Redirect URI: `http://localhost:8000/integrations/podio-files/callback`.
5. **Pick workspace** (org → workspace cascade). Stored as `podio_mcp_space_id`; auto-injected.
6. **Pick model** — strong models required: Groq `llama-3.3-70b-versatile`, Mistral Large, Gemini, or Claude. `llama3.2` (3B local) is too weak for multi-step tool calling. `llama3` does NOT support tools (Ollama returns 400).

### `app/services/podio_agent.py` — agent loop
`run_podio_agent(message, history)`:
1. Reads `agent_model` setting → dispatches to `model_gateway.chat()`.
2. Gets tools from **both** sources (hosted MCP + files client) and deduplicates by name (REST tools override same-named hosted MCP tools when Files is connected).
3. **Write-gating:** `_wants_write()` checks message + recent history for write-intent keywords. If no write intent, write tools are excluded entirely. `_FILES_READ_ONLY` set lets file-read tools (download, get_item_files) through without full write-intent.
4. **Ollama tool limiting:** `_filter_tools_for_ollama()` caps tool count at 18 for local models, using keyword-based priority (`_OLLAMA_TOOL_PRIORITY` dict maps intent keywords → relevant tool names).
5. Loops up to `_MAX_STEPS` (12): `model_gateway.chat()` → handle tool calls → append results → repeat.
6. **Tool name-embedding bug fix:** some models jam args JSON into the tool name string (e.g. `get_app {"app_id": 123}`); code splits on first space and recovers real name + args.
7. **Content stripping:** when model returns both `tool_calls` AND content, content is hallucinated (side effect of reasoning) — strip it before appending to history.
8. **Truncation guard:** truncated JSON causes models to emit `}}}}}` closing brackets — replaced with plain-text prefix note instructing model not to emit closers.
9. `_sanitize_tool_args()` drops null values + coerces types; `_clean_fields()` strips numeric/empty keys from write-tool field payloads.

### `app/services/model_gateway.py` — multi-provider LLM
`chat(messages, tools, model, num_predict)` dispatches on `"provider:model"` string:
- `ollama:*` → `_ollama_chat()` (POST `/api/chat`, function calling)
- `google:*` → `_google_chat()` (Gemini, `_to_gemini_contents` / `_from_gemini_response`, `_sanitize_schema`)
- `groq:*` → `_groq_chat()` → `_openai_compat_chat("https://api.groq.com/openai/v1", ...)`
- `mistral:*` → `_mistral_chat()` → `_openai_compat_chat("https://api.mistral.ai/v1", ...)` (tool_call IDs must be exactly 9 alphanumeric chars for Mistral)
- `openai:*` → `_openai_chat()` → `_openai_compat_chat("https://api.openai.com/v1", ...)`
- `anthropic:*` → `_anthropic_chat()` (POST `/v1/messages`, `_to_anthropic_messages`, `_from_anthropic_response`)
- `zai:*` → `_zai_chat()` → `_openai_compat_chat("https://api.z.ai/api/paas/v4", ...)` (Z.ai GLM models, e.g. `glm-5.2`)
- `_content_to_text()` flattens list-of-blocks content (reasoning models) to plain string

### `app/services/podio_rest.py` — custom REST client (60+ methods)
Auth: `Authorization: OAuth2 <token>` header (NOT Bearer). Token endpoint: `https://api.podio.com/oauth/token/v2` with **JSON body** (not form-encoded).

Key method groups and their verified correct endpoints:
- **Files:** `POST /file/v2/` (upload), `POST /file/{id}/attach` (attach), `DELETE /file/{id}` (delete), `GET /file/{id}` → download link → fetch bytes (download)
- **Items:** `GET /item/{id}`, `PUT /item/{id}` (update fields dict), `DELETE /item/{id}?silent=true`, `POST /item/{id}/clone/`, `POST /item/app/{app_id}/delete/` (bulk), `GET /item/{id}/revision/`, `DELETE /item/{id}/revision/{rev_id}/` (revert), `GET /item/{id}/reference/` (backlinks), `PUT /item/{id}/value/{field_id}` (single field), `GET /item/app/{app_id}/xlsx/` (export), view filter: GET view → POST `/item/app/{id}/filter/`
- **Tasks:** `GET /task/{id}/`, `DELETE /task/{id}/`, `POST /task/{id}/assign` (reassign — body `{"responsible": user_id}`), `POST /task/{id}/incomplete` (uncomplete — NOT DELETE /complete), `GET /task/summary/`, `GET /task/total/` (returns nested `{own:{...}, reassigned:{...}}` buckets — no `completed` filter param), `DELETE /task/{id}/ref/` (remove reference), `GET /task/{ref_type}/{ref_id}/` (get reference tasks — deprecated but functional)
- **Task labels:** `GET /task/label/`, `POST /task/label/` (`{text, color}`), `PUT /task/label/{id}/`, `DELETE /task/label/{id}/`
- **Flows:** `POST /flow/app/{app_id}/` (create — NOT `POST /flow/` which 404s). Body: `{type, name, ref_type:"app", ref_id, effects:[{type, attributes:{...}}], config?:{field_ids:[]}}` — create uses `"attributes"`. `PUT /flow/{id}/` — update uses `"values"`. `GET /flow/{id}/`, `GET /flow/app/{app_id}/`, `DELETE /flow/{id}/`. ⚠️ `GET /flow/effect/{type}/attribute/app/{app_id}/` → 404 (unavailable); skip it for comment.create effects (use plain text in attributes directly).
- **Webhooks:** `POST /hook/{ref_type}/{ref_id}/` (`{url, type}`; port 80/443 only), `GET /hook/{ref_type}/{ref_id}/`, `DELETE /hook/{id}/`, `POST /hook/{id}/verify/request/`, `POST /hook/{id}/verify/validate/` (`{code}`)
- **Workspace:** `POST /space/org/{org_id}/`, `PUT /space/{id}/`, `POST /space/{id}/archive/`, `POST /space/{id}/restore/`, `DELETE /space/{id}/`, `POST /space/{id}/member/` (`{mails:[...]}` or `{users:[...]}`), `DELETE /space/{id}/member/{user_id}/`, `PUT /space/{id}/member/{user_id}/` (`{role}`)
- **Conversations:** `POST /conversation/` (`{subject, text, participants:[int,...]}`), `POST /conversation/{id}/reply/` (`{text}`), `GET /conversation/`, `GET /conversation/{id}/`
- **Calendar:** `GET /calendar/`, `GET /calendar/space/{id}/`, `GET /calendar/app/{id}/`
- **Activity stream:** `GET /stream/space/{space_id}/`, `GET /stream/app/{app_id}/`, `GET /stream/` (global) — newest-first activity (items created/edited, comments, files, tasks) spanning ALL apps. Params `limit` (max 100), `offset`. ⚠️ Adding a comment or attaching a file does NOT change an item's `last_edit_on`, so a `get_items` sort misses that activity — the stream is the correct source for "what changed / what did I do today / the recently updated item across a workspace".
- **Reminders:** `GET /reminder/{ref_type}/{ref_id}/`, `PUT /reminder/{ref_type}/{ref_id}/` (`{remind_at}`), `DELETE /reminder/{ref_type}/{ref_id}/`
- **Recurrence:** `GET /recurrence/{ref_type}/{ref_id}/`, `PUT` (same body as create), `DELETE`
- **App creation:** `POST /app/` (`{space_id, config:{name, item_name, fields?:[]}}`)

⚠️ **Known Podio API limitations (confirmed via official docs):**
- `POST /task/{id}/rank/` → HTTP 410 Gone — task ranking removed from Podio API
- `task/total/` does NOT accept `completed`/`app_id` filters — it returns time-bucket counts only
- Podio's hosted MCP server has NO `delete_item` tool; our custom REST server provides it
- Hosted MCP's `update_item` returns 403; use our REST `update_item` instead (requires Files connection)

### Settings keys (DB `integration_settings` table)
| Key | Set by |
|-----|--------|
| `podio_mcp_client_id`, `podio_mcp_client_secret` | Settings UI |
| `google_api_key`, `groq_api_key`, `mistral_api_key`, `openai_api_key`, `anthropic_api_key`, `zai_api_key` | Settings UI |
| `podio_rest_client_id`, `podio_rest_client_secret` | Settings UI (optional — falls back to `podio_mcp_*`) |
| `podio_mcp_access_token`, `podio_mcp_refresh_token`, `podio_mcp_token_expiry` | OAuth callback |
| `podio_rest_access_token`, `podio_rest_refresh_token`, `podio_rest_token_expiry` | OAuth callback |
| `podio_mcp_space_id`, `podio_mcp_space_name`, `podio_mcp_org_name` | Workspace picker |
| `agent_model` | Model dropdown |

### Flow API cheat-sheet (confirmed correct)
```python
# Create flow — effects use "attributes" (ARRAY of {attribute_id, value} objects)
# ⚠️  POST /flow/ returns 404 — correct endpoint is POST /flow/app/{app_id}/
POST /flow/app/{app_id}/
{
  "type": "item.create"|"item.update"|"item.delete",
  "name": "My Flow",
  "ref_type": "app",
  "ref_id": <app_id>,
  "effects": [{"type": "comment.create", "attributes": [{"attribute_id": "comment.value", "value": "text here"}]}],
  "config": {"field_ids": [<numeric_field_id>]}  # item.update only, optional
}

# Update flow — PUT /flow/{flow_id}/ (Session 17: corrected — old "use values" was WRONG)
#   • Effects use "attributes" (SAME as create) — a "values" key (array OR dict) → HTTP 500.
#   • PUT is a FULL REPLACE and REQUIRES "name"; any omitted config/effects are WIPED
#     (a name-only PUT clears the field_ids trigger filter). update_flow() now fetches the
#     current flow and MERGES, so callers may pass only the parts they want changed.
PUT /flow/{flow_id}/
{"name": "...", "config": {"field_ids": [<field_id>]},
 "effects": [{"type": "comment.create", "attributes": [{"attribute_id": "comment.value", "value": "text"}]}]}

# Trigger types: item.create, item.update, item.delete
# Effect types and their official attribute_id strings (developers.podio.com/doc/flows):
#   comment.create      → "comment.value"
#   status.create       → "status.value"
#   task.create         → "task.text", "task.due" (days), "task.responsible"
#   conversation.create → "conversation.subject", "conversation.text", "conversation.participant"
# ⚠️  EVERY effect attribute value must be a STRING — task.due="7" not 7, task.responsible="<id>".
#     Podio 400s ("Invalid value N (integer): must be string") otherwise. create_flow now stringifies
#     all values automatically (Session 17), but pass strings if calling the API directly.
# For a filtered item.update trigger, config.field_ids may be a numeric field_id, external_id, or
#     label — create_flow resolves & validates each against the live app (a bad id → clear error, not 404).
#     NOTE: field_ids only fires on "field X changed"; it CANNOT restrict to a specific option value
#     ("only when Category=Books") — that needs GlobiFlow. Real Products Category field_id = 223289103.
# ⚠️  NO "update a field" effect — an item.update effect / "item.field.{external_id}" attribute is
#     REJECTED by Podio ("Unknown attribute item.field.X"; values must also be strings). The ONLY
#     working flow effects are task.create, comment.create, status.create. Setting/changing a field
#     value (even a fixed value) needs GlobiFlow (manual, in Podio) — create_flow now raises a clear
#     RuntimeError when given an item.update / item.field.* effect (Session 16 follow-up 6).
# field_ids (config, for filtered item.update TRIGGERS): numeric field.field_id from get_app — NOT external_id
# ⚠️  GET /flow/effect/{type}/attribute/app/{id}/ returns 404 — use hardcoded attribute_ids above
```

---

## Key Patterns

1. New adapters/REST methods: `httpx.AsyncClient` with `async with`, timeout 30–120s.
2. New MCP tools: `@mcp.tool(name="prefix.action")` inside `register_*_tools(mcp)`, registered in `main.py`.
3. Tool name prefixes must match `TOOL_PERMISSIONS` in `rbac.py` or they're unrestricted.
4. No business logic in adapters — thin HTTP wrappers only.
5. Adapters raise exceptions; tools catch and return `{"success": False, "error": ...}`.
6. Logging: `structlog.get_logger(__name__)` → `logger.info("event_name", key=val)`.
7. Settings UI (frontend): integrations always-rendered; LLM providers opt-in, persisted in `localStorage` under `syndrix_added_llms`.

---

## Session Log

### Sessions 1–4 (2026-04-23 to 2026-04-29)
Phase 1 skeleton (all 46 files), Phase 2 real adapters (Podio REST, GHL, Slack, GitHub, SMTP), RAG pipeline, full Next.js UI with Bootstrap 5, rich result rendering, toasts, skeletons, drag-and-drop file upload.

### Session 5 (2026-04-29)
Fixed `/tools/invoke` 404, GitHub user search, removed Docker. Project is fully local-only.

### Session 6 (2026-05-05)
Backend bug fixes (spec generator model, Alembic interpolation, pgvector Windows workaround, RAG ingest failures). Full frontend redesign: indigo design system, new sidebar with accordion, dark sidebar + light page, landing page, removed dark mode.

### Session 7 (2026-05-06)
Phase 3 (BD/Dev prompt packs, memory tools, CRM AI summary, live mgmt reports) and Phase 4 (hybrid RAG search, Redis caching, exponential-backoff Celery, Celery Beat schedule, live admin metrics).

### Session 8 (2026-06-22)
Environment bring-up and DB repair (Alembic stamp + upgrade). Fixed bcrypt incompatibility (pinned `bcrypt==4.0.1`). Fixed toast API mismatch. Built initial Podio Agent (LLM + REST adapter tools). Discovered `client_credentials` always returns 403 → pivoted to Podio's hosted MCP server (`mcp.podio.com`). Built `podio_mcp.py` (OAuth PKCE + MCP client), `app/api/integrations.py`, rewrote agent loop. Added workspace picker (org → workspace cascade). Fixed `call_tool` to return `structuredContent` (real records) not just text summary.

### Session 9 (2026-06-23)
Added Google Gemini as second LLM provider (full function-calling path). Added `GET /llm/models`, `POST /llm/model`. Added model selector dropdown in Podio Agent UI.

### Session 10 (2026-06-23–24)
Added Groq, Mistral, OpenAI, Anthropic LLM providers. Settings page rework: integrations always-visible, LLM providers opt-in with Add/Remove. Custom Podio file-upload capability (`podio_rest.py`, `mcp_servers/podio_files.py`, `podio_files_client.py`, `/integrations/podio-files/*`). Major agent reliability pass: `_SYSTEM_PROMPT` with FIELD VALUE FORMATS + CREATE/WRITE WORKFLOW, tool trimming for Ollama, write-gating improvements, `_clean_fields()` guard. Chat upgrades: Markdown rendering, JSON tree step cards, auto-grow textarea, file attach (paperclip), chat history with sessions in localStorage. Sidebar: Podio Agent moved to top-level under Team Tools. Hydration bug fixes. Item image field support (`set_item_image`).

### Session 11 (2026-06-25)
Fixed critical file-vs-item deletion bug (agent was calling `delete_item` when user said "delete this file"). Added `get_item_files` + `delete_file` REST methods and MCP tools. Added `update_item` + `delete_item` to custom REST server (hosted MCP's versions are broken). Fixed homepage hydration error (extracted inline CSS to `landing.css`). Fixed Settings credentials disappearing (made integrations always-rendered; `localStorage` persistence for LLM providers; `api.ts` now prefixes errors with HTTP status for 401 redirect). Raised JWT expiry to 24h. Added `download_file` (REST + MCP tool + API proxy endpoint + auto-download in UI).

### Session 12 (2026-06-26–27)
**Agent wiring audit + system prompt overhaul + Podio REST API audit.**

**Agent improvements (podio_agent.py):**
- Expanded `_WRITE_WORDS` to cover all write/manage operations (flow, webhook, conversation, task, clone, bulk, etc.)
- Added `_FILES_READ_ONLY` set — file-read tools (download, get_item_files, flows read, webhooks read, etc.) bypass full write-gating
- Added `_OLLAMA_MAX_TOOLS = 18` cap with `_filter_tools_for_ollama()` — keyword-based tool priority selection for local models (20 keyword → tool-set mappings via `_OLLAMA_TOOL_PRIORITY`)
- Fixed tool name-embedding bug (models jam args JSON into tool name string)
- Fixed content stripping (models emit hallucinated content alongside tool_calls → strip it)
- Fixed truncation garbage (truncated JSON causes `}}}}}` → use plain-text prefix note)
- `_NUM_PREDICT_FINAL = 1500` for the closing reply (was 500 → models cut off)
- System prompt: expanded TOOL SELECTION table to 42 rows; FLOW WORKFLOW rewritten as strict two-step gather-then-call; 7 new workflow sections; ITEM CREATE/UPDATE WORKFLOW renamed to avoid ambiguity with flows

**Flow API fixes:**
- `create_flow` in `podio_rest.py`: added `config` param (`{"field_ids": [...]}` for field-specific item.update triggers); added `item.delete` as valid trigger type
- `create_flow` MCP tool: added `field_ids` parameter; description warns against hallucinated `{{item.field_name}}` variables (use `get_flow_possible_attributes` for real expressions)

**Podio REST API audit (vs official developers.podio.com):**
- **FIXED** `uncomplete_task`: was `DELETE /task/{id}/complete/` → correct is `POST /task/{id}/incomplete`
- **FIXED** `reassign_task`: was `PUT /task/{id}` with `{"responsible": {"user_id": N}}` → correct is `POST /task/{id}/assign` with `{"responsible": N}` (scalar, not nested object)
- **FIXED** `rank_task`: endpoint removed from Podio API (HTTP 410 Gone) → now raises clear RuntimeError; MCP tool description warns agents not to call it
- **FIXED** `get_task_count`: `GET /task/total/` returns nested `{own:{...}, reassigned:{...}}` time-bucket dicts — old code looked for a `"count"` top-level key (always returned 0) and passed unsupported `completed`/`app_id` query params; fixed to sum all bucket values and removed bad params
- All other endpoints verified correct against official docs (items, files, flows, webhooks, workspaces, conversations, calendar, reminders, recurrence, app creation)

### Session 13 (2026-07-01)
**Custom REST MCP tool testing + flow API discovery + agent keyword gate fix.**

**`get_tasks` completed-tasks fix (`podio_rest.py`):**
- Podio `/task/` uses `space` (not `space_id`) as the workspace filter param — confirmed working for incomplete tasks
- Completed tasks (`completed=true`) also require a filter; `space=<space_id>` is valid per error message listing accepted filters
- Updated `get_tasks` MCP tool description: incomplete tasks → always pass `space_id`; completed tasks → pass `space_id` too (Podio accepts `space` filter for both)

**`update_task` added (`podio_rest.py` + `mcp_servers/podio_files.py`):**
- New `PUT /task/{id}/` method supporting `text`, `description`, `due_on`, `label_id` fields
- New `update_task` MCP tool — enables assigning labels to tasks: call `get_task_labels` first to get label_id, then `update_task(task_id, label_id=<id>)`
- System prompt updated with "assign label to task" two-step workflow

**Flow API — endpoint and format discovery (all confirmed via testing + official docs):**
- **FIXED create endpoint**: `POST /flow/` returns 404; correct is `POST /flow/app/{app_id}/`
- **FIXED effect attributes endpoint**: `GET /flow/effect/{type}/attribute/app/{id}/` returns 404 — removed from agent workflow; hardcoded attribute_ids instead
- **FIXED effect format**: `attributes` must be an ARRAY of `{attribute_id, value}` objects — plain dict rejected ("must be array"); individual element must have `attribute_id` field
- **FIXED attribute_id values** (from developers.podio.com/doc/flows): string names "text"/"value"/"content" all rejected as "Unknown attribute"; integer `1` rejected as "must be ascii only string"; **correct values**: `comment.create` → `"comment.value"`, `status.create` → `"status.value"`, `task.create` → `"task.text"`/`"task.due"`/`"task.responsible"`, `item.update` → `"item.field.{external_id}"`, `conversation.create` → `"conversation.subject"`/`"conversation.text"`/`"conversation.participant"`
- Added normalization in `create_flow` (`podio_rest.py`): converts dict attributes → array format, extracts comment text regardless of model format, always forces `attribute_id="comment.value"` for comment effects
- Updated CLAUDE.md flow cheat-sheet, system prompt FLOW WORKFLOW, and MCP tool description with all correct attribute_ids

**Agent keyword gate fix (`podio_agent.py`):**
- Root cause of "Unknown tool 'get_app_flows'" after restart: `"automation"` was absent from both `_WRITE_WORDS` and the `wants_read_tools` keyword list — the files tool gate was never opened for automation-related messages
- **FIXED**: added `"automation"` to `_WRITE_WORDS` and to `wants_read_tools`; added `"task"` to `wants_read_tools` as well
- Added step card entry when files client gate fails so errors are visible to the user instead of silently disappearing

**Agent ID accuracy fix (`podio_agent.py`):**
- Models consistently transposed 10-digit IDs (e.g. `3328348768` → `3328348868`)
- Added CRITICAL system prompt rule: copy user-provided numeric IDs exactly digit-for-digit; repeat ID back if unsure before calling tool

### Session 14 (2026-07-01–02)
**Calendar + reminder correctness, agent date-awareness, multi-step reliability, deterministic ID guard.**

**Tool-name recovery hardening (`podio_agent.py`):**
- Root cause of "tool not called": models mangle the tool-name field (e.g. `get_calendar Räikk{}`) with a NON-ASCII/unicode separator — the old recovery only split on a literal ASCII space (`" " in name`), so it never fired.
- Replaced the brittle 3-branch inline recovery with `_recover_tool_name()`: recovers embedded `{...}` args, normalises by replacing every non-`[A-Za-z0-9_]` char with a space (preserves word boundaries instead of ascii-strip gluing tokens), then longest-first whole-word match, then longest-first prefix match for the glued-no-separator case. Disambiguates `get_item_files` > `get_item`, `get_items` > `get_item`.

**Calendar `date_from`/`date_to` fix (`podio_rest.py` + `mcp_servers/podio_files.py`):**
- Podio `/calendar/*` endpoints return `HTTP 400 "must be to_date"` unless BOTH `date_from` and `date_to` (YYYY-MM-DD) are sent. All three calendar methods sent none.
- Added `_calendar_range()` helper → defaults to an upcoming window **today → +90 days**; `get_calendar`/`get_space_calendar`/`get_app_calendar` (REST + MCP tools) now accept optional `date_from`/`date_to`.

**External ("Add Calendar") linked-account calendars (`podio_rest.py` + `podio_files.py` + `podio_agent.py`):**
- Calendars added via Podio's "Add Calendar" are external **linked accounts** (Google/Exchange/Live), served from a SEPARATE endpoint — `get_calendar`/`get_space_calendar`/`get_app_calendar` only return Podio-native items + tasks, so they were invisible.
- Added `list_linked_accounts(capability="calendar")` → `GET /linked_account/` and `get_linked_account_calendar(linked_account_id)` → `GET /calendar/linked_account/{id}/` (REST + MCP tools). Wired both into `_FILES_READ_ONLY`, the ollama `"calendar"` priority set, and the system prompt CALENDAR workflow.
- Linked-account calendars are USER-scoped, not workspace-scoped. Improved `_normalise_calendar_event` to carry `source` (podio/google/exchange), `location`, `description`, and tolerate `start_utc`/`start_date` field variants.

**Agent date-awareness (`podio_agent.py`):**
- Model defaulted to its training-data date ("today is 16 July 2024") → computed all relative dates + passed 2024 dates to tools.
- Inject a **CURRENT DATE AND TIME** header (recomputed per request via `datetime.now().astimezone()`) at the top of the system prompt: day-of-week, ISO date, timezone; instructs the model to use it (never training data) and to answer "what's today's date" with it. Updated CALENDAR workflow to compute windows from it.

**Reminders — RELATIVE model fix (`podio_rest.py` + `podio_files.py` + `podio_agent.py`):**
- Podio `PUT /reminder/{ref_type}/{ref_id}/` requires `remind_delta` (minutes BEFORE the object's due date), NOT `remind_at` — old code sent `remind_at` → `HTTP 400 missing required properties: ['remind_delta']`.
- `set_reminder` now sends `remind_delta`. Preferred param `remind_delta`; convenience `remind_at` (absolute) is converted using the object's due date (`_ref_due_datetime` reads task `due_date` or the item's first date field) with clear errors when no due date exists or the time is after the due date.
- **Item reminders require the item's app to have a date field.** Contacts/TestWork apps have none → item reminders impossible; agent falls back to a linked task (tasks have native due dates).
- `set_reminder` now reads the reminder back (`get_reminder`) and computes the absolute fire time → returns `remind_at`, `verified`, and a human `content` string (a bare success was invisible in the UI as "No data returned").
- Prompt: "remind at <time>" → set due to that exact time + `remind_delta=0` (fire exactly then); never invent an arbitrary lead. State clearly when a reminder lands on a TASK vs the item.

**Multi-step partial execution (`podio_agent.py`):**
- Agent halted a whole multi-step request and asked "Would you like me to proceed?" when ONE step was blocked (reminder on a no-date-field item), skipping independent steps (the comment).
- Added PARTIAL EXECUTION rules: steps are independent unless one needs another's output; do every step you can, report per-step ✓done/✗blocked; don't pause to ask for non-destructive steps already requested; don't turn a scope-changing workaround into a blocking question.

**Deterministic ID anti-hallucination guard (`podio_agent.py`):**
- Model called `set_reminder` with a fabricated `task_id` (item-style number) right after `create_task` returned the real one → `HTTP 404 Object not found`.
- Agent loop now tracks ID provenance: `user_provided_ids` (from message+history), `seen_task_ids`/`seen_item_ids` (harvested recursively from every tool result via `_collect_ids`), `created_task_ids`/`created_item_ids`. `_guard_object_ids()` runs before each tool call: if a `ref_id`/`task_id`/`item_id` was never given by the user AND never returned by a tool, and exactly one object of that type was created/seen this run, it substitutes the real id (conservative — ambiguous multi-object cases are left alone). Corrections logged (`podio_agent_id_corrected`) and attached to the step as `note`.

**Frontend (`podio-agent/page.tsx`):**
- Chat History: added `createdAt` to `ChatSession`; History now sorts newest-CREATED first (was `updatedAt`, which reordered as you typed) and HIDES chats with no messages. `createdAt` preserved on write-back and backfilled for pre-existing sessions.
- Added step-card `TOOL_META` for all calendar tools (`get_calendar`, `get_space_calendar`, `get_app_calendar`, `list_linked_accounts`, `get_linked_account_calendar`).

**Also diagnosed (no code change):** 401s on all authed endpoints = expired/rejected JWT (frontend `isAuthenticated()` only checks token presence, not `exp`) → fix is re-login. `get_tasks` errors need the exact Result text to fix. Agent-side `due-date` filter hallucination self-resolves once calendar tools work.

### Session 15 (2026-07-03) — Infra only, no app code changed
**Fixed `/auth/login` 500 `asyncpg CannotConnectNowError` (recovery mode → shutting down).**

**Root cause: TWO PostgreSQL servers competing for `localhost:5432`.**
- The app's real database is the **native Windows PostgreSQL 18** service (`postgresql-x64-18`, data dir `C:\Program Files\PostgreSQL\18\data`). It holds the real schema — all 6 tables incl. `users` — and accounts `admin@syndrix.local` (admin) + `crm@syndrix.com` (bd).
- There is ALSO a Docker container `syndrix-postgres` (pgvector/pgvector:pg16) mapping host 5432. It is a **stale duplicate**: behind on migrations, MISSING the `users` table. The app never really used it. (Redis, by contrast, IS used from Docker: `syndrix-redis`.)
- The two servers fighting over 5432 left the native postmaster hung in a permanent **"shutting down"** state with 6 orphaned `postgres.exe` processes still holding the port. Every app connection then failed.
- ⚠️ The "in recovery mode" message was a **red herring** — this DB is tiny (WAL LSN ~`0/1B22xxx`), so crash recovery finishes in ~1 ms (`redo starts` → `redo done` → `ready`, same millisecond). The `invalid record length … got 0` line is the normal end-of-WAL marker, not corruption. The real problem was the port conflict + stuck cluster.

**Fix applied (elevated / UAC required):**
1. `Get-Process postgres | Stop-Process -Force` — killed the 6 hung native processes.
2. Removed stale `C:\Program Files\PostgreSQL\18\data\postmaster.pid`.
3. `Start-Service postgresql-x64-18` — clean start, recovery completed in ms → Running (StartType Automatic).
4. `docker stop syndrix-postgres` — removed the port competitor.
5. Verified end-to-end: `POST /auth/login` (admin@syndrix.local / changeme) → valid JWT.

**Operational notes / how to avoid recurrence:**
- Postgres now runs as the **native Windows service** on `0.0.0.0:5432` + `:::5432` (auto-starts on reboot). Keep the Docker `syndrix-postgres` container **stopped** — do NOT `docker compose up` its postgres service or the 5432 conflict + flapping returns.
- Service control (Stop/Start-Service, killing service-owned `postgres.exe`) requires an **elevated shell**; a normal session shell gets `Cannot open service` access-denied.
- If native hangs again: elevated → kill `postgres` processes, delete stale `postmaster.pid`, `Start-Service postgresql-x64-18`.
- If consolidating onto Docker is ever desired, it's a real migration (pg_dump native `syndrix` → restore into container, incl. `integration_settings` Podio OAuth tokens) — not just a container swap.
- Note: CLAUDE.md's Stack/runtime already says "native Postgres"; the Docker duplicate is the anomaly, not the intended DB.

### Session 16 (2026-07-08) — Podio Agent reliability sweep (create / files / recent / tasks / flows)
**TL;DR of the day's work** (details in the numbered follow-ups below; files touched: `app/services/podio_agent.py`, `app/services/podio_rest.py`, `app/mcp_servers/podio_files.py`, plus CLAUDE.md + memory):
1. **Stop unrequested cloning** — `clone_item` gated behind explicit clone intent; system prompt forbids clone as a create fallback.
2. **create_item field schema** — `_summarise_get_app_result` gives the model a compact, complete field schema (external_id/type/required/options) + a display `form`; value-format wrappers documented (money/date/category…).
3. **Field form on create** — always show all fields + dropdown option values and wait, before creating.
4. **No fake successes** — `_is_hallucinated_write` now requires a SUCCEEDED write (errored call ≠ done); attach/image phrases added; forces the real call or an honest failure. Fixed the "file attached" lie.
5. **"Recent / today / recently updated" across a workspace** — new `get_activity_stream` tool (spans all apps, includes comments/files that don't bump last_edit_on).
6. **"Tasks in an app"** — new `get_app_tasks` (enumerate items → collect their tasks); Podio has no server-side app-task filter and `get_reference_tasks(ref_type="app")` returns 0.
7. **Flow (automation) UX** — one-question-at-a-time wizard (trigger → show fields → action → name); get_app REST fallback when hosted returns empty; "fetched-but-didn't-show" field-list append; degenerate "row of dots" reply detector.
8. **Flow effect limits (confirmed via live probe)** — the ONLY supported flow effects are task/comment/status; there is NO "update a field" effect (field updates need GlobiFlow); `create_flow` now fails fast with a clear message.
9. **Weak-model robustness** — recover & execute tool calls the model emits as garbled TEXT (reads auto-run so the wizard progresses; writes re-prompted). Underlying cause is a weak agent model — recommend Groq 70B / Mistral Large / Gemini / Claude.
- **Left for the user (live cleanup, needs the running agent):** delete the stray clone **item 3332089924**; the image (file 2475282811) is attached to the WRONG item (taha 2342215820) — should move to the intended one.

**Reported bug:** "Create a new item in Products titled 'Agentic Phone'" — the agent made 8 failed `create_item` calls then, unprompted, called `clone_item(IPhone)` and passed the clone off as the new item. Two faults: (a) it cloned when only asked to create, (b) create never succeeded.

**Root causes:**
1. **Unrequested clone** — `clone_item` was in `_FILES_READ_ONLY`, so it was exposed on *every* request. "create" flips `wants_write=True`, so the whole files toolset (incl. clone) was available, and the model grabbed clone as a workaround when create kept failing.
2. **Wrong external_ids / "No field found"** — model guessed keys like `"sku"`. Hosted `get_app` only returns a text summary ("Products (16 fields)"); when the real field schema is in structuredContent it can be truncated at `_MAX_TOOL_OUTPUT_CHARS` (6000), so the model never sees all external_ids/types/required flags.
3. **"Invalid value null (null): must be Range"** — a required **date** field (e.g. Stock Purchase Date) was omitted or sent as a bare string instead of `{"start":"YYYY-MM-DD HH:MM:SS"}`.

**Fixes:**
- Removed `clone_item` from `_FILES_READ_ONLY`. Added `_wants_clone()` (`_CLONE_WORDS`: clone/duplicate/make a copy/copy of/copy the); `clone_item` is now dropped from `files_specs` unless the message shows explicit clone intent — it can no longer be a create fallback.
- Added `_summarise_get_app_result()` (+ `_find_app_fields` shape-agnostic recursive locator, `_compact_field`). Applied to every non-error `get_app` result before it reaches the model: replaces the verbose/truncatable payload with a compact COMPLETE schema — each field's `external_id`, `field_id`, `type`, `required`, and category/status `options` (deleted options dropped) — plus a `note` spelling out value formats (money `{"value","currency"}`, date `{"start":...}`, category=option id, relationship=item_id, contact=profile_id). Unknown shapes pass through unchanged. Includes `field_id` so the FLOW workflow's numeric-field_id lookup still works.
- **Field-form presentation on create (user request):** `_summarise_get_app_result` also builds a ready-to-display Markdown `form` string via `_render_app_form()` (+ `_FIELD_TYPE_HINT`): fields grouped Required/Optional, each dropdown (category/status) rendered as "**Label** (choose one): opt1, opt2, …" so the user sees the exact selectable values; `calculation` fields excluded (auto-computed); deleted options hidden. System prompt CREATE step 3 now REQUIRES showing the `form` verbatim and waiting — the only skip is when the user already supplied every required field; never invent/guess/default a dropdown value.
- System prompt: new CORE RULE forbidding `clone_item`/duplicate as a create_item workaround (only on explicit user request). ITEM CREATE/UPDATE WORKFLOW step 4 now lists exact value wrappers and "provide every required=true field"; step 6 = read error → fix named field → retry once → report exact error + missing required fields, NEVER clone/fabricate.
- Verified: `python ast` compile OK; unit-tested `_summarise_get_app_result` (nested structuredContent extraction, required flags, deleted-option filtering, pass-through on no-fields) and `_wants_clone` (create≠clone).

**Not done (needs the live agent, not code):** the wrongly-created clone **item 3332089924** still exists in Podio — ask the agent to `delete item 3332089924`. Could not re-run the live create from here (no OAuth session); the fixes are logic/prompt-level and unit-verified, but the end-to-end create should be re-tested in the app with a strong model.

**Follow-up — false "file attached" success (same item 3332089924):** user asked to add an image; `set_item_image` errored ("app has no image field"), the model claimed "✅ Attached successfully" and later "already attached" — but `attach_file_to_item` was NEVER called. Root cause: `_is_hallucinated_write()` only checked whether a write tool was *called*, not whether it *succeeded* — `set_item_image` errored yet counted as a write, so the false claim passed; and the phrase list had no attach/image terms.
- Added `_step_errored(step)` (detects `isError` / `error` / `success:false`) and `_successful_write_tools(steps)`. `_is_hallucinated_write` now flags a success claim when NO write tool *succeeded* this turn (a call that errored no longer counts). Added attach/image/upload phrases to `_HALLUCINATED_SUCCESS_PHRASES`.
- On a `hallucination` verdict the loop no longer just discards + weakly re-prompts: it injects a correction naming the failed tool call(s) and forces the model to either make the correct call (e.g. `attach_file_to_item` after `set_item_image` fails) or report the failure plainly, then `continue`s. Bounded by `_MAX_HALLUCINATION_RETRIES=2`.
- System prompt: new CORE RULE "CHECK EVERY TOOL RESULT" (error markers = FAILED; never report failed/never-attempted as success; verify "already attached/exists" via a read tool). FILE ATTACHMENT section: if `set_item_image` fails (no image field) you MUST then call `attach_file_to_item` and confirm ITS success before saying attached; "add it to the files" → call `attach_file_to_item` + verify, never claim "already attached" without `get_item_files`.
- Unit-tested `_is_hallucinated_write` across 5 scenarios (reported bug, no-tool claim, legit successful attach, read-only reply, `success:false` create) — all pass. NOTE: the image was NOT actually attached to 3332089924 — re-issue "attach images.jfif (file 2475282811) to item 3332089924" after restart.

**Follow-up — "recently updated item" found the wrong item / "no item created today":** agent answered "the recently updated item" by taking the top of ONE arbitrarily-chosen app (Testing Podio 28248576) sorted by `last_edit_on` → returned a Jan-2023 item ("taha" 2342215820) and attached the file to it; then insisted nothing was created today even though the user created items + comments today. Two root causes: (a) it never searched the whole workspace (single app only), (b) `last_edit_on` does NOT bump on comment/file activity, and there was no cross-app recent-activity primitive.
- **New capability — Podio activity stream.** Added `PodioREST.get_activity_stream(space_id?, app_id?, limit, offset)` → `GET /stream/space/{id}/` | `/stream/app/{id}/` | `/stream/` (newest-first, spans all apps, includes comments/files). `_normalise_stream_event()` flattens each event to `{type, ref_id, title, app, app_id, created_on, last_edit_on, created_by}` (reads fields at top level OR under `data`). Registered MCP tool `get_activity_stream` in `mcp_servers/podio_files.py` (verified: 69 tools, params space_id/app_id/limit/offset).
- Wired into `podio_agent.py`: added to `_FILES_READ_ONLY`, to `wants_read_tools` keywords (recent/recently/latest/today/activity/stream/last updated/did i/this week/…), and `_OLLAMA_TOOL_PRIORITY` (recent/today/activity/latest). `space_id` auto-injected by the existing workspace auto-fill.
- System prompt: new "RECENT ACTIVITY / WHAT CHANGED TODAY / THE RECENTLY UPDATED ITEM" workflow — use `get_activity_stream(space_id)` (NOT one app's get_items sort); trust the stream over `last_edit_on` (which comments/files don't bump); fall back to get_apps_in_space + per-app get_items if the stream errors; and CONFIRM the identified item (name + item_id) before any write (attach/comment/update) to a "recent" item — attaching to the wrong record is hard to undo. Added a TOOL SELECTION table row too.
- Verified: 3 files syntax-OK; `_normalise_stream_event` unit-tested across 3 event shapes (top-level item, data-nested comment, task); tool registration confirmed. NOTE: the `/stream/` endpoint shape/params are per Podio docs but were NOT exercised live from here — re-test in the app ("what did I create today", "the recently updated item"). If it errors, the Session-16 honesty fix now surfaces the error instead of hallucinating, and the prompt fallback (app enumeration) kicks in.

**Follow-up — "get all tasks in the Products app" returned WORKSPACE tasks:** agent used `get_tasks(space_id=7532914)` (whole-workspace) and mislabelled the result as the Products app's tasks (they were linked to several different apps/items). Root cause: agent routed app-scoped task requests to the wrong tool. The right tool already existed: `get_reference_tasks(ref_type="app", ref_id=<app_id>)` → `GET /task/app/{app_id}/` (deprecated-but-functional). Fixes:
- `_normalise_task` now also returns `ref_app_id` / `ref_app_name` (from `ref.data.app`) so every task shows WHICH app it belongs to and workspace tasks can be filtered by app. Defensive — null when absent.
- `get_reference_tasks` MCP tool description rewritten to state it IS the tool for "all tasks in the X app" (ref_type="app", ref_id=app_id) and that `get_tasks(space_id)` is workspace-wide, not per-app.
- System prompt: TOOL SELECTION now distinguishes "incomplete tasks in the WHOLE workspace" (get_tasks space_id) vs "all tasks in a specific APP" (get_reference_tasks ref_type="app") vs "tasks on one record" (ref_type="item"); TASK MANAGEMENT WORKFLOW spells out: resolve app_id → get_reference_tasks(ref_type="app", ref_id=app_id); never use get_tasks(space_id) for a single app.
- Verified: 3 files syntax-OK; `_normalise_task` unit-tested (app info extracted; null-safe when absent).

**Follow-up 2 — `get_reference_tasks(ref_type="app")` returns 0 (WRONG for app tasks):** live test proved `GET /task/app/{app_id}/` returns 0 even when the app's items have tasks — it only matches tasks referencing the app OBJECT, not tasks on the app's ITEMS. Podio has NO server-side "tasks in an app" filter. Implemented the correct approach (enumerate items → collect each item's tasks):
- New `PodioREST.get_app_tasks(app_id, completed=False, max_items=300)` with two strategies: (1) fast-path — pull workspace tasks (`_all_space_tasks`, paginated) and keep those whose `ref_app_id`==app_id, used only when the task list actually carries app info; (2) thorough fallback — enumerate the app's item_ids (`_app_item_ids` via `POST /item/app/{id}/filter/`, paginated, capped at max_items with a `truncated` flag) then gather `get_reference_tasks("item", id)` per item (asyncio.Semaphore(6)), dedup by task_id, filter by completed. Added `import asyncio`.
- Registered MCP tool `get_app_tasks` (verified: 70 tools, params app_id/completed/max_items). Wired into `podio_agent` `_FILES_READ_ONLY`, the `"task"` ollama-priority set.
- System prompt: "tasks in the X app" now routes to `get_app_tasks(app_id)`, explicitly NOT `get_tasks(space_id)` (whole workspace) and NOT `get_reference_tasks(ref_type="app")` (returns 0). Instructed to surface `items_truncated`.
- Verified: 3 files syntax-OK; tool registers; `get_app_tasks` unit-tested for BOTH strategies (workspace_filter by app; per_item fallback with cross-item dedup + completed filtering). NOTE: the workspace fast-path depends on the task-list response carrying `ref.data.app`; if absent it auto-falls back to per_item. `POST /item/app/{id}/filter/` item enumeration is used elsewhere (get_items_by_view) so the endpoint is proven; re-test live with "get all the tasks in the Products app".

**Follow-up 3 — "create an automation on Products" UX + degenerate output:** when the user asked "give me all the fields I can set/trigger on", the model emitted `get_app {"app_id":...}` as text followed by a long row of dots (generation collapse) and never showed the fields. Root causes: (a) FLOW prompt said "Do NOT call get_app for field definitions when creating a flow" — contradicting the need to SHOW fields for specific-field triggers / update-field actions; (b) no detector for degenerate repeated-char runs.
- `_is_garbage_reply` now also flags a run of ≥15 identical punctuation chars (`_REPEAT_RUN_RE`), >65% bracket/pipe noise, OR >60% punctuation — so a "row of dots" reply is discarded and the model re-prompted. Unit-tested (dots/dashes caught; markdown tables + "..." ellipsis NOT false-flagged).
- Rewrote FLOW (AUTOMATION) WORKFLOW: strictly ONE QUESTION AT A TIME (Trigger → Action → Name; name may be asked first), each with a brief explanation. get_app IS now used inside the flow to PRESENT the field list (name/type + dropdown option values) when the trigger is a specific field or the action is "update a field"; numeric `field_id` → `field_ids`, `external_id` → `item.field.{external_id}`. Removed the contradictory "don't call get_app" line.
- Softened `_summarise_get_app_result`'s note so the field schema serves the CURRENT task (create/edit record OR build a flow) and does not push the model into `create_item` during flow building.
- Verified: syntax OK; garbage-detector tests pass. Re-test "create an automation on Products" — should walk trigger→action→name one at a time and list real fields/options.

**Follow-up 4 — "it said I fetched the fields but it didn't":** during flow creation the model called get_app (success) then replied "I fetched the full list of fields… now tell me which one" WITHOUT actually listing any field. Deterministic fix (doesn't rely on model compliance):
- Track the most recent get_app compact schema per turn (`last_app_fields`, `last_app_name`). When the model's accepted final reply `_claims_to_present_fields()` (phrases: "fetched the", "which field", "so you can choose", …) but `_count_field_labels_shown()` < 2 of the actual field labels appear, append a neutral display-ready list via `_render_field_list()` (field name + type; dropdowns show option values; calculation fields excluded). If the reply already lists ≥2 fields, nothing is appended (no dup).
- Also added CORE RULE "ACTUALLY SHOW WHAT YOU FETCH" — never say "I fetched the list / choose which one" without printing it.
- Verified: syntax OK; unit-tested — the exact reported reply triggers the append (0 labels shown) while a reply that already lists fields does not; `_render_field_list` excludes calculation fields and shows dropdown options.

**Follow-up 5 — get_app returns "No data returned" (hosted MCP has no field schema):** the real reason the field list never appeared: hosted MCP `get_app` returned an empty result (no structuredContent, empty content) → `_summarise_get_app_result` found no fields → nothing to show/append. **Hosted `get_app` cannot be relied on for the field schema.** Fix: in the agent's get_app handling, when the (non-error) hosted result has no `fields`, transparently fall back to `podio_rest.get_app(app_id)` (REST returns the full Podio app object incl. the `fields` array) and run `_summarise_get_app_result({"data": raw})` on that. Requires the Files/REST OAuth connection (present whenever the user does writes/flows). If REST isn't connected it logs `podio_agent_get_app_rest_fallback_failed` and keeps the empty hosted result. Verified: syntax OK; `_summarise_get_app_result` extracts fields from a raw REST app object `{"data": raw}`. **Takeaway for future work: for anything needing an app's field definitions (create/update/flow field pickers), the REST `get_app` is the source of truth — the hosted MCP `get_app` only returns a text summary at best, empty at worst.**

**Follow-up 6 — "decrease Progress by 10" flow refused with bogus "field_id missing":** user designed an automation (trigger: Category updated → action: decrease Progress by 10); at "go ahead" the model refused, claiming field_id was missing. Real cause: **Podio's basic flow API (`/flow/`) `item.update` effect can only set a field to a FIXED static value — it cannot do arithmetic ("decrease by 10"), conditional logic ("if ≤10 set 0"), or derive/compute values. Those need Podio GlobiFlow / Workflow Automation (a separate product, configured manually in Podio), which we do NOT integrate.** The model masked an unsupported-feature situation as a field_id error. Fixes (prompt only):
- FLOW workflow now has an explicit "WHAT THIS FLOW API CAN AND CANNOT DO" block: CAN = create task / add comment / post status / set field to a FIXED value; CANNOT = arithmetic/relative field changes, conditional logic, copy-from-another-field/computed values, email/SMS/webhook effects, branching. On a CANNOT request: state plainly it's not possible via the API, explain it needs GlobiFlow set up manually in Podio, and offer a supported alternative — do NOT fake it or invent a field_id excuse.
- STEP 4 clarified: every field in the get_app schema HAS a `field_id`; never claim it's missing (only a truly empty get_app is a schema-fetch problem to report).
- Verified syntax OK. (No code path can make Podio do field arithmetic — this is an inherent flow-API limit; correct behavior is an honest "not supported" + alternative.)

**Follow-up 7 — even SETTING a field (fixed value) via flow fails; confirmed field-update effects unsupported:** the model tried `create_flow` with an `item.update` effect anyway → Podio returned `Invalid value 50 (integer): must be string` then `Unknown attribute item.field.progress`. **Confirmed: Podio's basic flow API has NO working "update a field" effect — the ONLY supported effects are task.create, comment.create, status.create.** Our long-standing `item.field.{external_id}` attribute format was never valid. Fixes:
- `create_flow` (podio_rest.py) now fails FAST with a clear RuntimeError when any effect is `type=item.update`/`item.field.update` or has an `attribute_id` starting with `item.field` — tells the user only task/comment/status are supported and field updates need GlobiFlow. (Prevents the cryptic Podio 400 from reaching the user.)
- Prompt: FLOW ACTION step now lists ONLY task/comment/status; the CAN/CANNOT block moves "set a field (even fixed)" to CANNOT; STEP 4 effect reference drops the item.update-field line and warns against it.
- CLAUDE.md flow cheat-sheet corrected (removed the bogus `item.update field → item.field.{external_id}` line).
- Verified: syntax OK; `create_flow` guard unit-tested (item.update blocked; a sneaky field attribute on status.create also blocked; comment.create passes the guard). ⚠️ The comment.create test actually CREATED a real flow on live Podio (flow_id 2339035) because the REST token here is valid — I DELETED it immediately (delete_flow 2339035 → success). Lesson: `create_flow`/other write methods hit LIVE Podio from this environment; don't call them in tests without cleanup.
- **CONFIRMED via exhaustive live probe (user-authorised):** created+deleted test flows on Products (app 26071589) trying the `item.update` effect across 4 field types (text/number/category/progress) × 4 attribute formats (`str(field_id)`, `external_id`, `item.field.{field_id}`, `field.{external_id}`) = 16 combos — ALL returned HTTP 400 `Unknown attribute <X>`. So the `item.update` effect has NO valid field attribute for ANY field type/format: Podio's flow API truly has no "update a field" effect. The only working effects are task.create / comment.create / status.create; field updates require GlobiFlow. This is now settled — do NOT re-investigate. (All probe flows deleted; cleanup sweep confirmed 0 PROBE flows and 0 total flows remaining on the app.)

**Follow-up 8 — weak model emits tool calls as garbled TEXT, wizard stalls:** during flow creation the model output `get_appriendschappelijk{"app_id":26071589}`, `create_flow{...}`, `get_appმწ{...}` as plain-text content (not structured tool_calls), so get_app never ran and the field list never showed. Two fixes in `podio_agent.py`:
- **Parse-and-execute text tool calls.** New `_extract_text_tool_calls()` (+ `_extract_balanced_json()`) finds `<name>{...json...}` / `<name>({...})` / `<name>:{...}` spans in the reply, recovers the real tool name via `_recover_tool_name` (handles glued/unicode-garbled names like `get_appriendschappelijk`→`get_app`), and parses the JSON args. In the loop, when the model returns no structured tool_calls, the READ/discovery ones (NOT `_WRITE_TOOL_NAMES`) are executed — `messages[-1]` is rewritten as a proper assistant tool-call turn (provider-valid, FIFO-matched) and the loop runs them. Write tools are NOT auto-executed from text (a text `create_flow` could be the model merely describing intent) — they fall through to the correction re-prompt.
- Broadened `_TEXT_TOOL_CALL_RE` to also catch the GLUED, separator-less form `name{"key"...` (the `{` + quoted key is the signal) so a text-emitted WRITE call is caught and re-prompted instead of leaking raw text to the user.
- Verified: syntax + import OK; `_extract_text_tool_calls` unit-tested on the exact garbled transcript cases (recovers get_app/create_flow, multi-line, and does NOT match `money {"value":..}` prose or `item.field.{external_id}`); broadened detector unit-tested (glued write caught; plain prose / `{a,b,c}` not). NOTE: the underlying trigger is a WEAK agent model — these are robustness backstops; a strong model (Groq 70B / Mistral Large / Gemini / Claude) avoids the garbled-tool-call behavior entirely.

### Session 17 (2026-07-09) — Flow create: two real bugs fixed + `active` red herring (verified live)
**Reported (Mistral-large):** "create automation on Products: when Category updated → create task 'Task for inspection…' deadline next week, name 'Product Category Updated'" failed twice: (1) `HTTP 400 Invalid value 7 (integer): must be string`, then (2) `HTTP 404 Object not found` on `POST /flow/app/26071589/`. Both are now fixed and the automation was created live (flow_id **2339231**, active).

**Root causes + fixes (all deterministic, code-level — not model-dependent):**
1. **task.due sent as integer** → Podio requires EVERY flow-effect attribute value to be a **string** (also seen in S16 f7: "Invalid value 50: must be string"). `create_flow` (`podio_rest.py`) never coerced values. **Fix:** after normalising each effect, stringify every `attributes[].value` (`str(v)` when not None). Also fixed the system-prompt example that literally taught `{"attribute_id":"task.due","value":0}` (int) → now `"7"` with a note "all effect values must be strings; task.due = days as a string".
2. **Hallucinated field_id** → the model passed `field_ids=[45577653]` for Category, whose REAL `field_id` is **223289103** (confirmed via REST get_app). A non-existent field_id in `config.field_ids` makes Podio 404 "Object not found". **Fix:** new `PodioREST._resolve_flow_field_ids(app_id, refs)` — accepts a numeric field_id, an **external_id**, OR a field label, maps each to the real numeric field_id against the LIVE app schema, and raises a clear error listing valid fields if a ref can't be resolved (instead of the opaque 404). `create_flow` calls it whenever `config.field_ids` is present. MCP `create_flow` tool no longer force-`int()`s field refs (lets `"category"` reach the resolver). System prompt updated: may pass external_id if unsure of the numeric id; NEVER invent a field_id.
3. **`active: false` was a red herring.** Podio's flow object has **NO `active` key** (raw keys: `config, effects, execution_count, flow_id, name, ref, type`); a flow is **live the moment it's created** — there is **no activation endpoint** (`POST /flow/{id}/activate` → 404; `PUT` needs `name`). `create_flow` defaulted the missing key to True, `get_flow`/`get_app_flows` defaulted it to False → looked inactive. **Fix:** `get_flow` + `get_app_flows` now default `active` to **True** (absence ≠ disabled).

**Verified LIVE (user's actual request, kept — not a throwaway):** created flow_id 2339231 on Products (app 26071589) passing `task.due=7` as an INT and the field as the STRING `"category"`; readback confirms `task.due="7"` (stringified) and `config.field_ids=[223289103]` (resolved). `get_app_flows` now lists it `active: True`. Real Category field_id = **223289103**.

⚠️ **API limitation to tell the user:** a filtered item.update trigger's `field_ids` only restricts to "the Category field CHANGED" — it CANNOT restrict to a specific option value ("only when Category = **Books**"). Per-value / conditional triggers need GlobiFlow (manual in Podio). The created flow fires on ANY Category change.

**Follow-up — "replace this automation" DELETED the flow then failed to recreate + falsely reported success.** The agent (Mistral-large) tried `update_flow` (400 then 500), then `delete_flow` (ok — flow gone), then `create_flow` with `field_ids=[1]` (a hallucinated id — correctly rejected by the S17 resolver), yet replied "✅ successfully replaced, Status: Active" → left the app with NO flow. Root causes + fixes (all in `podio_rest.py` / `podio_agent.py` / `podio_files.py`):
1. **`update_flow` used `values`, which 500s.** Live probe on PUT `/flow/{id}/`: `values` (array OR dict) → **500**; `attributes` array → **200**; name-only → **200 but WIPES config.field_ids**. So (a) the long-standing "use values on update" guidance was WRONG — Podio's PUT wants `attributes`, same as create; (b) PUT is a **full replace requiring `name`** — omitted parts are wiped. **Fix:** rewrote `update_flow` to fetch the current flow and **MERGE** (name/config/effects each default to current), normalise effects via the new shared `_normalise_flow_effects` (accepts `attributes` OR legacy `values`, strips to `{attribute_id,value}`, drops nulls, stringifies), and resolve `config.field_ids` (needs app_id → `get_flow` now returns `app_id` from `ref`). MCP tool + system-prompt + CLAUDE.md cheat-sheet all corrected (attributes not values; merge semantics; prefer update over delete+recreate except when the trigger TYPE changes).
2. **Honesty guard let the false "replaced/Active" pass.** `_is_hallucinated_write` (a) didn't have "replaced"/"active"/"saved" phrases so `claims_success` was False, and (b) a successful `delete_flow` satisfied `_successful_write_tools`, so a failed recreate still counted as a write. **Fix:** added the missing phrases; added `_CREATE_WRITE_TOOLS` + `_CREATE_SUCCESS_PHRASES` and a rule — a "created/replaced/now active/attached" claim while a CREATE-type write ERRORED this turn and none succeeded → hallucination, even if an unrelated delete succeeded. Unit-tested (reported bug flagged; legit create/update/delete-only replies not flagged).
- **Live recovery:** the user's flow was gone; recreated it (2339234) and restored its Category trigger filter via the FIXED `update_flow` (verified end-to-end: merge preserved name+effects, resolved "category"→223289103, stringified due="7"). Cleaned up a duplicate (deleted 2339233). Final state: exactly ONE active flow **2339234 "Product Category Updated"** on Products.
- **Extracted `_normalise_flow_effects`** (shared by create_flow + update_flow) — single source of truth for the attributes-array/stringify/field-guard logic. Verified: 3 files syntax-OK; guard + normaliser unit tests pass; live update_flow + get_app_flows confirm one correct flow. ⚠️ REST token is LIVE from this env — the recreate/update/delete all hit real Podio (intended: restoring the user's flow).

**Follow-up 2 — "add a comment" flow: model emits `create_flow` as TEXT/JSON, tool never runs (even on Mistral-large).** User showed the model writing the `create_flow` args as a bare JSON block (`<!-- tool call --> {…}`) instead of a structured call; `get_app` ran ("ok") but `create_flow` was silently discarded. **Reproduced + root-caused (2 agent defects, `podio_agent.py`):**
1. **`_extract_text_tool_calls` only recovered a call when the tool NAME was glued to the `{`** (`create_flow{…}`). When the name is in prose and the JSON stands alone (or is a bare blob), it recovered NOTHING → the call couldn't be run or even classified for recovery. **Fix:** rewrote it to scan TOP-LEVEL JSON objects and attribute each to a tool via new `_tool_name_for_json()` — (a) an identifier glued before `{` (via `_recover_tool_name`, handles garble), else (b) the LAST valid tool name mentioned in the preceding ~240 chars of prose. Now recovers `Calling create_flow …\n{…}` and bare `get_app` blobs. Reads auto-execute; writes still re-prompt (not auto-run from text).
2. **`template_vars` was checked BEFORE `text_tool_call` in the `bad_reason` chain.** The comment value held `{{new_category}}`, so `_has_template_vars` fired first → the reply was classified `template_vars`, which just **breaks the loop with empty final_text** (NO re-prompt) → `create_flow` silently dropped. **Fix:** reordered so `text_tool_call` precedes `template_vars`/`non_ascii`; a serialised call now re-prompts the model to make the REAL structured call. Also strengthened the text_tool_call correction to forbid `{{placeholder}}` variables and explain a flow comment must be LITERAL text (Podio can't inject the changed field's value — that's GlobiFlow).
- **Also an intrinsic model error:** `{{new_category}}` is invalid — a `comment.create` flow effect posts literal text only; it cannot interpolate the new field value. So even a "successful" create with that value would post the literal string. The prompt already said not to use `{{…}}`; the model ignored it → now backstopped deterministically.
- Verified: syntax OK; `_extract_text_tool_calls` recovers Case B (prose name + JSON blob → create_flow) and bare get_app, still recovers glued names, and does NOT match `money {…}` / `item.field.{external_id}` / a JSON example with no tool name named; guard + normaliser tests still pass. NOTE: root trigger is the model choosing text over function-calling — these are backstops; a strong model reduces it, but the agent now recovers/re-prompts instead of silently dropping the call.

### Session 18 (2026-07-16) — "list all the contacts" claimed a table that was never shown
**Reported:** user asked "list all the contacts"; agent called `get_app` then `get_items` (both `ok`) and replied "I fetched all 3 contacts from your Podio Contacts app and listed them in a simple table showing each contact's name, phone, email, notes, creation date, and Podio item ID" — but no table appeared. User saw no usable result at all.

**Root cause:** Session 16 follow-up 4 added a guard for exactly this failure shape but scoped it to `get_app` field lists only (`_claims_to_present_fields` / `_render_field_list` / `last_app_fields`) — there was no equivalent for `get_items`/`search_globally` record lists. A model claiming "I listed them in a table" after a successful `get_items` had nothing forcing the claim to be backed by actual content, so a plausible-sounding but empty summary could reach the user untouched.

**Fix (`podio_agent.py`):**
- Added `_find_items()` — a shape-agnostic recursive locator for a Podio item-records list in a tool result (mirrors `_find_app_fields`; distinguishes item entries from field-schema entries by the presence of `item_id` rather than `external_id`).
- Added `_field_display_value()` + `_render_items_table()` — best-effort flattening of each item's field values (handles the `{"value":...}` / `{"start":...}` (date) / `{"text":...}` / contact-name value shapes) into a Markdown `| item_id | Title | Details |` table.
- Added `_CLAIMS_RECORDS_PHRASES` / `_claims_to_present_records()` — phrase-based claim detector (mirrors the fields version; "fetched all", "listed them", "in a table", "here are", etc.).
- Added `last_items_result` / `last_items_app_name` tracking (mirrors `last_app_fields`/`last_app_name`) — captured right after a non-error `get_items`/`search_globally` call via `_find_items()`.
- Added `_augment_reply_with_missing_data()` — unifies the fields-guard and the new records-guard into one call; the records check appends the real table when the reply claims to present records but fewer than 100% of the returned `item_id`s actually appear in the reply text (the system prompt already mandates showing `item_id` on every row, so this is a reliable, non-false-positive signal — a reply that genuinely lists everything is left untouched). Wired into BOTH places a final reply is accepted: the normal per-step break AND the `_MAX_STEPS`-exhausted closing-summary fallback.
- Verified: syntax OK; unit-tested against the exact reported scenario (3-contact `get_items` result + the exact hallucinated reply text) — table is appended with all 3 item_ids and titles present; a reply that already lists every item_id is left byte-for-byte unchanged (no duplicate table).

### Session 19 (2026-07-16) — Podio Agent chat history moved to the database
**Ask:** move chat history out of the frontend's `localStorage`-only storage into the database, so it survives across browsers/devices and isn't lost on `localStorage` clear.

**New table `podio_chat_sessions`** (`app/storage/models.py`, migration `f4a7b9c1d2e3_add_podio_chat_sessions.py`, chained after `a1b2c3d4e5f6`): `id` (UUID PK, client-generated), `owner_key` (indexed), `team_name`, `title`, `messages` (JSONB — same shape the frontend already used: `[{role, content, steps?, error?}, ...]`), `created_at`, `updated_at`. One row per conversation; no separate messages table — simplest mapping from the existing localStorage shape.

**Per-user ownership.** `TeamIdentity` (`app/auth/bearer.py`) gained a `user_id` field, populated from the JWT's `sub` claim on real logins. `owner_key` is `user:<user_id>` for real logins or `team:<team_name>` for the `DEV_TOKENS` fallback (that path has no per-user identity, so those quick-login sessions are shared per-team — acceptable for the dev/testing shortcut).

**API (`app/api/agent.py`, all BD+Admin only, all under `/agent/podio/sessions`):**
- `GET /agent/podio/sessions` — list the caller's sessions (id/title/message_count/timestamps only, no message bodies — lightweight for the History dropdown).
- `GET /agent/podio/sessions/{id}` — full detail incl. `messages`. 404s if the id doesn't exist or isn't owned by the caller (ownership check, not just existence).
- `PUT /agent/podio/sessions/{id}` — **upsert** (creates if the id is new, updates if it exists). The frontend always PUTs by a client-generated UUID, so "start a new chat" needs no separate create call — the row appears on the first successful save.
- `DELETE /agent/podio/sessions/{id}` — no-ops silently if not owned (avoids leaking existence via 403 vs 404 timing).

**Frontend (`podio-agent/page.tsx`, `lib/api.ts`, `types/index.ts`):** replaced the `SESSIONS_KEY` localStorage read/write effects and the full-fidelity `ChatSession[]` local state with the lightweight `PodioChatSessionSummary[]` from `GET /sessions` for the History dropdown; full `messages` are fetched on demand (`getPodioChatSession`) only when a session is opened. A `useEffect` on `[messages, currentId]` upserts the active conversation via `savePodioChatSession` (title re-derived from the first user message, same `deriveTitle()` as before) whenever `messages` changes and is non-empty — mirrors the old "write the live conversation back" effect but against the API instead of `localStorage`. `newChat()` just mints a fresh client-side UUID; the row isn't created until the first message saves, so History naturally never shows empty chats (matches the old `.filter(messages.length > 0)` behavior, kept as a defensive `message_count > 0` filter too).

**Verified:** `alembic upgrade head` (table already existed from `init_db()`'s `create_all` firing on a dev-server reload after the model edit — schema matched exactly, so used `alembic stamp head` instead of a destructive re-run); full CRUD lifecycle exercised live against the running backend (create via PUT → appears in list → GET detail returns messages → DELETE → gone from list); `tsc --noEmit` shows zero errors in any changed file (6 pre-existing unrelated errors elsewhere in the frontend, untouched by this change).

### Session 20 (2026-07-16) — "show orders and contacts" / "orders and products": only a summary shown, real data missing (multi-app + pagination gap in the Session 18 fix)
**Reported:** "show all the orders and Contacts" → agent called `get_app`(Orders) then `get_items`(Contacts) then `get_items`(Orders), all `ok`, then replied with only a prose summary ("I fetched and listed all 37 orders... and all 5 contacts... The full tables are above") — no tables anywhere. Same pattern with "show all the orders and products", where Orders was fetched across 4 paginated `get_items` calls (cursor-based, 37 items total) and Products via a separate call, after listing apps with `get_apps_in_space` instead of `get_app`. Ask: a **universal, complete** fix — this class of bug must not recur regardless of phrasing or app combination.

**Root cause: the Session 18 fix only tracked the SINGLE most recently fetched app.** `last_items_result`/`last_items_app_name` were plain variables, overwritten by every `get_items` call. With 2+ apps queried in one turn, only the last-called app's items survived to the completeness check — the first app's real data was gone by the time the reply was checked. Worse, a single app paginated across multiple `get_items` calls (same app_id, different `cursor`) also got overwritten page-by-page, so even a single-app fetch of 37 records only ever had the LAST page (≤10 items) available to backstop against. The guard was also purely **reply-phrase-triggered** (`_claims_to_present_records`) — a model that dodges listing data with wording outside the fixed phrase list would never trigger the check at all, which is inherently incomplete as a sole signal.

**Fix (`podio_agent.py`), all deterministic — no reliance on model behavior:**
1. **Multi-app, pagination-safe accumulation.** Replaced `last_items_result`/`last_items_app_name` with `items_by_app: dict[str, dict[item_id, item]]` (keyed by `app_id`, items keyed by `item_id`) + `app_names: dict[app_id, name]`, both accumulated across the ENTIRE turn. Every `get_items` call merges into its app's bucket (dedup by `item_id`, so repeated/paginated calls to the same app_id combine into the full set instead of overwriting); `search_globally` results resolve each item's own app via new `_item_app_id()` (checks a nested `app.app_id`/`app_id` field) since a global search can span multiple apps in one call.
2. **App names resolved from BOTH `get_app` and `get_apps_in_space`.** New `_find_apps_list()` (shape-agnostic locator, mirrors `_find_app_fields`/`_find_items`) extracts `{app_id, name}` pairs from a `get_apps_in_space` ("List Apps") result — needed because the 2nd reported case never called `get_app` for Products at all, only `get_apps_in_space` then straight to `get_items`.
3. **Primary trigger moved from reply-phrasing to USER INTENT.** New `_wants_record_listing(message)` detects listing intent in the user's OWN request ("show", "list", "display", "give me", "all the", "what are the", "table", etc.) — this is the message BEFORE the model ever replies, so it can't be dodged by clever reply phrasing the way `_claims_to_present_records` could. When the user asked to see records, the completeness check now runs **unconditionally** for every app queried this turn, regardless of what the model's summary says. The old phrase-based check is kept as a secondary OR'd trigger (covers e.g. a follow-up "yes show me" without repeating listing words). `_CLAIMS_RECORDS_PHRASES` also broadened with the exact phrases from this report ("full tables above", "in clear tables above", "showing key details", "for your reference", etc.) as defense in depth.
4. **`_render_multi_app_tables()`** — checks EACH app's item_ids against the reply text independently and renders a table only for the apps/pages actually missing (an app the model already fully listed correctly is left alone, no duplicate table) — then all missing tables are appended together, labelled with the resolved app name (or "these results" for an unresolvable `search_globally` bucket, never a bare "App None").
- Verified: syntax + import OK; reproduced BOTH exact reported scenarios end-to-end (37 orders across 4 simulated paginated pages + 5 contacts in one test; 37 orders + 4 products with names resolved via `_find_apps_list` in the other) — all record ids now present in the augmented reply for every app queried. Regression-tested the Session 18 single-app case (still works via the phrase-detection path). Negative-tested: a reply that already shows everything is left byte-for-byte unchanged (no duplicate tables), and a pure count query ("how many orders are there") does NOT set the force-listing flag (avoids dumping an unwanted full table on a query that only wanted a number).

### Session 21 (2026-07-16) — Self-describing JSON tool-call envelope broke text-call recovery entirely
**Reported:** "Show all the orders and products separately" → the model's raw reply leaked straight to the user, verbatim: two HTML-comment-labelled JSON blocks, `{"name": "get_items", "parameters": {"app_id": 26071589, "limit": 50, "offset": 0}}` (Products) and the same shape for Orders (app_id 26071687) — no step cards, no data, just the model's undigested text.

**Root cause: `_extract_text_tool_calls`/`_tool_name_for_json` had never anticipated a JSON object that names its OWN tool.** Every prior recovery path (Sessions 16–17) assumed the tool name lives OUTSIDE the JSON object — glued to it (`get_app{"app_id":123}`) or mentioned in nearby prose ("Calling create_flow …\n{...}"). Here the model used the much more common (it mirrors the real OpenAI/Anthropic function-call wire format) self-describing shape `{"name": "get_items", "parameters": {...}}`, which broke recovery in TWO independent, compounding ways, confirmed by direct reproduction against the exact reported text:
1. **Silent mis-attribution across sibling blocks.** With no glued prefix or prose mention of "get_items" anywhere in the actual prose, `_tool_name_for_json`'s only remaining fallback — scanning the preceding ~240 chars of text for ANY valid tool name substring — found nothing for the FIRST JSON block (Products), so it was silently dropped entirely. For the SECOND block (Orders), the scan window now included the FIRST block's own text, which happens to contain the literal substring `"get_items"` (as the *value* of its `"name"` key) — so the second call was "recovered", but only by accident, off text belonging to a different call.
2. **Wrong arguments even when a name resolved.** Because nothing looked inside the object for a nested params key, the object's ENTIRE envelope (`{"name": "get_items", "parameters": {...}}`) was used as the tool's arguments verbatim — not the real flat `{"app_id":..., "limit":..., "offset":...}` inside `"parameters"`. `_sanitize_tool_args` doesn't drop unrecognized keys, so even a "successfully recovered" call would have been sent to Podio with a nonsensical `name`/`parameters` payload instead of `app_id`.
Net effect: recovery produced 0 or 1 usable calls (never 2, never with correct args) for every turn using this shape, the `text_tool_call` correction re-prompt has no retry cap and the model kept repeating the same shape, and once `_MAX_STEPS` (25) was exhausted the closing-summary fallback's own output — since it isn't flagged by `_is_garbage_reply` (coherent English + valid JSON, not noise) — became the final reply verbatim.

**Fix (`podio_agent.py`):** new `_self_describing_call(obj, valid_names)`, tried FIRST for every top-level JSON object `_extract_text_tool_calls` finds, before falling back to the old context-guessing path. Checks the object itself for a name under `name`/`tool`/`tool_name`/`function_name` (resolved through the existing `_recover_tool_name` so a garbled tool name inside the envelope is still handled) and args under `arguments`/`parameters`/`args`/`input` (JSON-string-encoded args are parsed too); also recurses into a nested `{"function": {...}}` envelope (the raw OpenAI/Anthropic wire shape). Only returns a match when the name resolves to a REAL tool — a legitimate flat call like `{"name": "My Workspace", "org_id": 5}` (a `create_workspace` args object that happens to have a `name` field) correctly falls through unmatched, since `"My Workspace"` doesn't resolve to any tool name. This fixes both bugs at once: each block is now self-contained (no cross-block text leakage possible) and args come from the real nested params, not the envelope. Write-tool policy is unchanged — a self-describing `create_item`/etc. call is still recovered but NOT auto-executed (falls through to the `text_tool_call` re-prompt, same as before); only reads auto-run.
- Verified: syntax + import OK; reproduced the EXACT reported text — now recovers both calls with correct flat args (`{"app_id": 26071589, "limit": 50, "offset": 0}` and `{"app_id": 26071687, ...}`), each independently. Regression-tested every existing case: glued name (`get_app{...}`), paren-wrapped (`create_flow({...})`), garbled glued name (`get_appriendschappelijk{...}`), prose+bare-blob fallback (Session 17 f2 case B — still works when the object's own `name` key doesn't resolve to a tool, e.g. a flow's `name` field), and the negative tests (`money {...}` prose, `item.field.{external_id}`, non-JSON `{a, b, c}` — none misfire). New tests: nested `{"function": {...}}` envelope, JSON-string-encoded `arguments`, and the write-gating check (a self-describing `create_item` is recovered but excluded from auto-execution, same as a glued-name write call always was).

### Session 22 (2026-07-16) — Added Z.ai (GLM) as a 7th LLM provider
**Ask:** add Z.ai's GLM-5.2 model as a selectable Podio Agent model, same pattern as Groq/Mistral/OpenAI/Anthropic/Gemini.

**Confirmed via live research + a real API call (not guessed):** Z.ai exposes an OpenAI-compatible endpoint at `https://api.z.ai/api/paas/v4` (Bearer auth, `POST /chat/completions`, model id e.g. `"glm-5.2"`) — slots directly into the existing `_openai_compat_chat()` helper already shared by Groq/Mistral/OpenAI, no new request/response translation needed. A live call to `GET /models` with a fake key returned `401 Unauthorized` (not 404), confirming the listing endpoint genuinely exists at that path.

**Backend (`model_gateway.py`):**
- Added `"zai"` to `_split_model()`'s provider list and `chat()`'s dispatch table → `_zai_chat()` → `_openai_compat_chat(base_url=self._ZAI_BASE, ...)`, reading `zai_api_key` from settings (mirrors `_groq_chat`/`_mistral_chat` exactly).
- `list_zai_models()`: calls the real `/models` endpoint; on a genuine 404 (endpoint truly absent) OR an empty-but-200 response, falls back to a small hardcoded `_ZAI_KNOWN_MODELS` list (`glm-5.2`, `glm-5.1`, `glm-4.7`, `glm-4.7-flash`, `glm-4.6`, `glm-4.6v-flash`) so a valid key never shows an empty dropdown just because the listing endpoint is flaky — the same silent-empty-dropdown failure shape as the earlier truncated-OpenAI-key incident this session. A genuine auth/network error (bad key, timeout) still returns `[]`, so an invalid key correctly shows no models (verified live: a fake key → real `401` from Z.ai → `[]`).
- `settings_service.py`: added `zai_api_key` to `_SECRET_KEYS` (masked in the UI) and `ALL_SETTING_KEYS` (persisted/loaded).
- `app/api/llm.py`: `GET /llm/models` now also calls `list_zai_models()` and returns a `"zai": ["zai:glm-5.2", ...]` array.

**Frontend:** `lib/api.ts`'s `listLlmModels()` return type gained `zai: string[]`. `settings/page.tsx`'s opt-in `LLM_PROVIDERS` array gained a "Z.ai (GLM)" entry (`zai_api_key` field) — the add/remove UI is fully generic over this array, no other wiring needed. `podio-agent/page.tsx`'s `ModelSelector` gained a `zai` state slot, included it in the "already-selected-but-undiscovered" fallback check, and added a "Z.ai (GLM)" `<optgroup>` + the "no models found" all-empty check.
- Verified: backend syntax/import OK; `tsc --noEmit` shows zero errors in any changed file; live end-to-end test against the running backend — `GET /llm/models` returns the new `zai` key; saved a fake `zai_api_key` via `upsert_setting`, called `list_zai_models()` live (real `401` from `api.z.ai`, correctly returned `[]`), then cleaned up the test key from the dev DB.

### Session 23 (2026-07-16–17) — MyCase Agent: a second, independent agent (GET-only for now)
**Ask:** build a full second agent — "MyCase Agent" — as its own sidebar item under Podio Agent, its own MCP server, its own LLM model dropdown, and its own DB-backed chat history. Scope: GET/read endpoints only (46 of them, sourced from real docs the user exported from their browser — see `mycaseapi.csv` for the endpoint index and `mycase-get-methods-info.txt` for full request/response schemas, since MyCase's Stoplight docs site is a pure client-rendered SPA behind Cloudflare that no automated tool here can read directly).

**Real API confirmed live:** `https://external-integrations.mycase.com/v1` (distinct from the `mycaseapi.stoplight.io` docs host), bearer-token auth. Confirmed genuinely reachable and correctly wired end-to-end by calling it live with a deliberately-fake token — got a real structured `401 {"errors":[{"description":"Unauthorized"}]}` from MyCase's actual server, not a DNS/404 failure.

**New files (mirror the Podio integration's file-per-layer pattern exactly):**
- `app/services/mycase_rest.py` — thin REST client, one method per GET endpoint (46 total: calls, case_roles, cases +client/individual/folder/documents/notes, case_stages, clients +individual/notes/message_threads, companies +individual, custom_fields +individual/list_options, documents +individual/versions(+all)/downloads, folders, events, expenses +individual, firm, me, invoices, invoice_payments, leads +individual, locations, notes, people_groups, practice_areas, referral_sources, staff +individual, tasks, time_entries +individual, webhook_subscriptions). Generic `_get_one`/`_get_list`/`_download` helpers handle the shared conventions confirmed from the real docs: `filter[updated_after]` incremental sync, `page_size`(≤1000)/cursor `page_token` pagination parsed from the `Link` response header, `Item-Count` header, and download endpoints' 302-redirect-to-signed-URL shape (surfaced as a URL, bytes never touch the LLM). OAuth authorization_code flow is implemented (mirrors `podio_rest.py`) but the authorize/token URLs (`auth.mycase.com/oauth/*`) are a **best-effort, unconfirmed default** — MyCase's docs blocked every attempt to read the real endpoint — overridable via settings. The primary supported path for v1 is pasting an already-obtained access token directly into Settings, which is what this was built and tested against.
- `app/mcp_servers/mycase.py` — standalone FastMCP server (`FastMCP("mycase")`), one `@mcp.tool()` per endpoint, thin dispatch through a shared `_call(method_name, **kwargs)` helper.
- `app/services/mycase_client.py` — in-process `list_tools()`/`call_tool()` adapter (mirrors `podio_files_client.py`).
- `app/services/mycase_agent.py` — agent loop mirroring `podio_agent.py`'s shape but simplified: no write-gating/write-hallucination-guard needed (every tool is a read). Kept the parts proven valuable regardless of read/write: Ollama tool-count capping (46 tools → 16, keyword-priority, same pattern as Podio's 18-cap), the full Session-21 text-tool-call recovery stack (`_self_describing_call`, `_extract_text_tool_calls`, `_recover_tool_name`, garbage/non-ascii detection), and the Session 18/20 "claimed to show records but didn't" completeness guard — adapted to MyCase's much simpler response shape (`{"items":[...], "item_count", "next_page_token"}` directly from our own REST client, vs. Podio's deeply-nested item structure) via `_item_label()` + `_render_items_table()`. `_wants_record_listing(message)` is the primary trigger (independent of model phrasing), same rationale as the Podio fix.
- `app/storage/models.py` → `MyCaseChatSession` (deliberately a SEPARATE table from `PodioChatSession`, not a shared/generalized one — same shape, but keeps the two agents' history and migrations fully independent). Migration `a7c2e9f1b3d4` (down_revision `f4a7b9c1d2e3`).
- `app/api/mycase_agent.py` — `POST /agent/mycase`, `GET /agent/mycase/status`, and the full session CRUD (`GET/PUT/DELETE /agent/mycase/sessions[/{id}]`) — copy of `app/api/agent.py`'s Podio session pattern (ownership via `user_id`-or-`team_name`, ownership-checked 404s) pointed at `MyCaseChatSession`.
- `app/api/llm.py` — generalized to be agent-scoped: `GET /llm/models?agent=podio|mycase` and `POST /llm/model` (body gains an `agent` field) now read/write `agent_model` or `mycase_agent_model` depending on `agent`, so picking a model for one agent never touches the other's selection (verified live: setting one leaves the other's `selected` unchanged).
- `app/auth/rbac.py` — `mycase.` prefix → BD+ADMIN (same tier as Podio's `agent.`).
- `app/services/settings_service.py` — added `mycase_client_id`, `mycase_client_secret` (secret), `mycase_redirect_uri`, `mycase_access_token` (secret).
- Frontend: `frontend/app/dashboard/mycase-agent/page.tsx` (adapted from `podio-agent/page.tsx` — dropped the OAuth connect button, workspace picker, and file-upload/attach UI, which don't apply here; kept chat, step cards with a MyCase-specific `TOOL_META`, model selector, DB-backed history). `components/Sidebar.tsx` gained a "MyCase Agent" top-level item next to Podio Agent (same BD/Admin role gate). `dashboard/settings/page.tsx` gained a "MyCase" credentials card (access token primary field; client id/secret/redirect URI marked optional, for a future OAuth flow). `lib/api.ts` gained the MyCase agent + session functions and `listLlmModels`/`setLlmModel` gained an `agent` parameter. `types/index.ts`'s MyCase types are aliased to the Podio ones (`MyCaseChatMessage = PodioChatMessage`, etc.) rather than duplicated, since the shapes are identical by design.

**Bug found and fixed along the way (affects the EXISTING Podio integration too, not just the new code):** while wiring up `mycase_client.py`'s result parsing (copied from `podio_files_client.py`'s pattern), live testing showed `FastMCP.call_tool()` in the installed MCP SDK version returns a `list[TextContent]` — NOT the `(content_blocks, structured_dict)` tuple both files' old code checked for. That tuple form only materializes when a tool declares an **explicit output schema** via its return type annotation; every tool here and in `podio_files.py` just returns a plain `dict`, so the tuple-check was dead code, and the actual `str(result)` fallback was passing something like `"[TextContent(type='text', text='{...}', annotations=None, meta=None)]"` — Python repr noise, not clean JSON — into the agent's context for **every single podio_files (custom REST) tool call** (create_item, update_item, delete_file, attach_file_to_item, etc.) this whole time. Fixed both `mycase_client.py` and `podio_files_client.py` with a proper `_normalise_call_result()` that parses the JSON text out of the content block(s) when the tuple form isn't present. Verified live: `get_case_stages()` through the full chain now returns a real Python dict (`{'success': False, 'error': '...'}`) instead of the repr string.
**Also fixed:** a `→` character in a raised error message crashed structlog's console renderer under this Windows environment's cp1252 codepage (`UnicodeEncodeError`) — replaced with `-`. The same character exists in a couple of pre-existing `model_gateway.py` error messages (e.g. "Settings → OpenAI (GPT)") which were never actually exercised through `logger.warning(error=str(exc))` before now — flagged here as a latent landmine, not fixed (out of scope for this session; would crash the whole request if a code path ever logs one of those messages on a cp1252 console).

**Verified end-to-end (live, against the running dev server):** all 46 tools register and are individually callable through the real MCP server; `POST /agent/mycase` returns a clean structured error (not a crash) when unconfigured; full session CRUD lifecycle (create via PUT → list → get detail → delete → gone); `GET /llm/models?agent=mycase` and `?agent=podio` (or omitted) return independently-tracked `selected` values; Podio regression checks passed throughout (existing `/agent/podio/sessions`, model selection, and `podio_files_client` behavior all unaffected). `tsc --noEmit` shows zero new errors (same 6 pre-existing unrelated ones). Alembic: `a7c2e9f1b3d4` stamped as head (table already existed from the dev server's `init_db()` auto-`create_all` on reload, matching the pattern from Session 19).

**Not done / left for a follow-up:** write operations (create/update/delete — roughly the other half of the API surface) were explicitly out of scope for this session. The OAuth authorize/token endpoint URLs are unconfirmed guesses; if the user wants the "Connect MyCase" browser flow (vs. pasting a token), those need verifying against real MyCase API support/docs first.

### Session 24 (2026-07-17) — Real MyCase OAuth "Connect" flow (the Session 23 URLs were guesses; now confirmed + implemented)
**User supplied MyCase's actual "Getting Started" doc content** (their Stoplight docs finally rendered for them once logged in with real access — confirms Session 23's guessed OAuth endpoints were wrong):
- Authorize (browser redirect): `GET https://auth.mycase.com/login_sessions/new?client_id=...&redirect_uri=...&response_type=code&state=...` — NOT `/oauth/authorize` as guessed.
- Token exchange **and** refresh both hit the SAME endpoint: `POST https://auth.mycase.com/tokens` — NOT `/oauth/token`, and NOT two different endpoints. Body is **JSON**, not form-encoded (`{client_id, client_secret, code, grant_type:"authorization_code", redirect_uri}` / `{..., refresh_token, grant_type:"refresh_token"}`).
- Token response: `access_token`, `token_type:"Bearer"`, `scope`, `refresh_token`, `expires_in` (86400s = 24h), `firm_uuid`. Refresh tokens last 2 weeks. Rate limit: 25 req/s per client. The redirect_uri is fixed by MyCase support at credential-issuance time — cannot be changed by us, must match exactly.

**Fixes (`app/services/mycase_rest.py`):**
- `DEFAULTS["mycase_authorize_url"]` → `https://auth.mycase.com/login_sessions/new`, `DEFAULTS["mycase_token_url"]` → `https://auth.mycase.com/tokens` (both were wrong guesses in Session 23).
- `exchange_code()` / `_refresh()` now POST with `json=body` instead of `data=body` (was form-encoding a body MyCase expects as JSON — would have failed on first real use).
- `_store_token()` now also captures `firm_uuid` into a new `mycase_firm_uuid` setting.
- Fixed a second leftover `→` character in a raised `ValueError` message (same crash class as the one fixed in Session 23 — this one wasn't caught because it doesn't get hit until `build_authorize_url()` is called with no client_id, which the Session 23 smoke tests never exercised).

**New: the actual "Connect MyCase" browser flow (`app/api/integrations_mycase.py`, new file, mirrors `app/api/integrations.py`'s Podio pattern exactly):**
- `GET /integrations/mycase/connect` — returns the real authorize URL (verified live: correctly builds `https://auth.mycase.com/login_sessions/new?client_id=...&redirect_uri=...&response_type=code&state=...` once a client_id is configured; returns a clean `{"success": false, "error": "..."}` — not a 500 — when it isn't).
- `GET /integrations/mycase/callback` — no auth dependency (this is MyCase's own browser redirect); validates `state`, exchanges the code, redirects to `/dashboard/mycase-agent?mycase=connected` or `?mycase=error&reason=...`.
- `POST /integrations/mycase/disconnect` — clears stored tokens.
- Registered in `main.py`.

**Frontend (`mycase-agent/page.tsx`):** `MyCaseConnection` changed from a read-only status badge into a real Connect/Disconnect control (mirrors Podio's `PodioConnection` component) — redirects the browser to the authorize URL, shows a spinner while starting, and a Disconnect button once connected. Added the OAuth-return toast effect (reads `?mycase=connected`/`?mycase=error` on mount, shows a toast, strips the query params) — same pattern as the Podio page's existing OAuth-return handling. `lib/api.ts` gained `startMyCaseConnect()` / `disconnectMyCase()`.

**Settings UI (`settings/page.tsx`):** MyCase card reordered/reworded now that OAuth is real — Client ID/Secret/Redirect URI are the primary fields (no longer marked "optional/future"), explains the Redirect URI must exactly match what MyCase support registered, Access Token is now the explicitly-secondary "skip the browser flow" option with a note that it won't auto-refresh.

**Verified live:** `GET /integrations/mycase/connect` with no client_id → clean `{"success": false, ...}` error (not a crash, arrow-character bug now fixed here too); with a test client_id configured → returns a correctly-formed authorize URL against the real confirmed endpoint (query params and host match the doc exactly); test credential cleaned up afterward. `tsc --noEmit` — zero new errors. Backend syntax/import OK.

**Still not done:** haven't exercised a REAL full round-trip (actual MyCase login → real code → real token) since that requires the user's actual registered Client ID/Secret and a browser session — the user should try "Connect MyCase" from the UI now that the endpoints are correct. If `POST /tokens` rejects the request, the most likely causes per the doc are: redirect_uri not matching exactly what MyCase support registered, or the authorizing MyCase user lacking the "Manage your firm's preferences, billing, and payment options" permission (explicitly called out in their docs as a cause of a forbidden/403 on step 1).

### Session 25 (2026-07-17) — Error handling, rate-limit retry, and UTBMS code lookup (from 3 more confirmed docs pages)
**User supplied 3 more real MyCase docs pages** (Error Handling, Cursor-based Pagination, Time Zones) plus the full UTBMS Codes reference table. Pagination was already correctly implemented (confirmed the `Link: <...>; rel="next"` format and `page_size`/`page_token` match exactly what was already built in Session 23) — the other two pages exposed real gaps.

**Structured error parsing (`app/services/mycase_rest.py`):** MyCase returns `{"errors": [{"description": "...", "source": {"pointer": "..."} | {"parameter": "..."}}]}` on every 4xx/5xx — the code previously just called `resp.raise_for_status()`, surfacing httpx's generic "400 Bad Request for url ..." instead of MyCase's actual human-readable description. Added `_extract_error_message()` (parses the real structure, falls back to raw text) and a static `_ERROR_CODE_TEXT` map for all 8 documented codes (400/401/403/404/422/429/500/503) so every failure now reads like `"MyCase API 401: Unauthorized (Unauthorized - not authenticated; the access token may be expired, try reconnecting)"` — verified live against the real API with a fake token. Applied to both `_request()` (covers `_get_one`/`_get_list`) and `_download()` (previously used a bare `raise_for_status()` with no parsing at all).

**429 rate-limit retry:** the docs confirm 25 req/s per client. `_request()` now retries up to 3 times (1s/2s/4s backoff) on a 429 before giving up — same pattern already used in `model_gateway.py`'s `_openai_compat_chat` for provider rate limits.

**UTBMS code lookup (`app/services/mycase_utbms.py`, new file):** the full LEDES Code Set II (1999B) reference table (28 activity codes + 245 task codes) transcribed verbatim from the docs, exposed as static dicts + `lookup_utbms_code(code)` (case-insensitive). Registered as MCP tool #47, `lookup_utbms_code` — deliberately kept OUT of the system prompt (would cost real tokens on every single turn for a ~270-entry table that's rarely relevant) and instead reachable on demand, wired into the Ollama keyword-priority map (`utbms`, `ledes`, `time entry` → includes it).

**System prompt additions (`mycase_agent.py`):** a DATES AND TIME ZONES section (always include a UTC offset in any date passed to a tool — MyCase silently assumes UTC if omitted, which can shift a "today" filter by hours for a non-UTC user) and an ERROR HANDLING section (what a 401 vs 403 vs 429 means in plain terms, and to report the real parsed error rather than guessing).

**Verified:** syntax/import OK; live test against the real API with a deliberately-invalid token confirms the new error message is correctly parsed (not the old generic httpx text); `lookup_utbms_code` tool registers (47 tools total now) and resolves both a real code and an unknown one correctly through the full MCP round-trip (exercises the Session 23 `_normalise_call_result` fix too). Podio + MyCase status/session regression checks against the live server unaffected.

### Session 26 (2026-07-17) — "All connection attempts failed" on complex MyCase queries (model_gateway.py bug, affects both agents)
**Reported:** a complex multi-condition MyCase report query ("cases where Practice Area = Immigration and Case Type = Asylum, excluding certain stages, grouped by agent") failed with `"All connection attempts failed"`, while a simpler single-filter query on the same connection/token worked fine. User was on `claude-sonnet-5` (Anthropic).

**Root cause: `httpx.ConnectError`'s exact message IS "All connection attempts failed"** (it's what httpx/anyio emit when every attempted IP for a hostname fails to connect) — and `model_gateway.py`'s `_anthropic_chat()` only caught `httpx.HTTPStatusError` and `httpx.TimeoutException`, not `httpx.ConnectError`/`httpx.TransportError` generally. A transient network blip during the LLM call therefore propagated as a raw, uncaught exception, which the agent API route's blanket `except Exception` caught and returned as `{"success": False, "error": str(exc)}` — surfacing httpx's internal message verbatim to the user with no context. This was far more likely to surface on the complex query than the simple one purely because it needed many more sequential LLM round-trips (get_practice_areas → get_case_stages → get_cases with heavy pagination → analysis) — more calls, more chances to hit one transient blip — not because anything was wrong with the query, the token, or the connection in general.

**This bug existed in ALL FOUR provider paths, not just Anthropic** — `model_gateway.py` is shared infrastructure used by both the Podio and MyCase agents:
- `_google_chat` (Gemini) had NO handling beyond `HTTPStatusError` at all — not even a timeout catch.
- `_openai_compat_chat` (Groq/Mistral/OpenAI) already had a retry loop, but only for HTTP 429 — a `TransportError` inside that loop skipped straight past both existing `except` clauses uncaught.
- `_ollama_chat` caught `httpx.RequestError` (a `ConnectError`'s parent class) but only to log-and-re-raise, no retry — lower real-world risk since Ollama is localhost, but the same gap in principle.

**Fix:** added a `[1, 2, 4]`-second backoff retry (3 attempts) on `httpx.TransportError` to all four provider methods — `_ollama_chat`, `_google_chat`, `_openai_compat_chat` (extended its existing 429-loop to also retry on transport errors, reusing the same loop/backoff rather than a second nested loop), and `_anthropic_chat`. `httpx.TimeoutException` is a subclass of `TransportError`, so each method's existing timeout-specific error message still fires correctly once retries are exhausted — the retry only defers it. A genuine HTTP error response (`HTTPStatusError`, e.g. a real 401/403/500) is never retried by this change — only network/transport-layer failures where no HTTP response was received at all.
- Verified live: mocked `httpx.AsyncClient.post` to raise `httpx.ConnectError` (the exact class behind "All connection attempts failed") twice before succeeding on the 3rd attempt — `model_gateway.chat(..., model="anthropic:...")` correctly retried with the expected 1s/2s backoff and returned the successful response, confirming the fix actually recovers from this exact failure mode rather than just changing the error message. Full Podio + MyCase regression (sessions, model selection) unaffected.

**Not done:** if a transient failure exhausts all 3 retries (a longer outage, not just a blip), the MyCase agent still loses all `steps` gathered so far in that turn — `app/api/mycase_agent.py`'s `except Exception` handler returns `"steps": []` regardless of how much progress `run_mycase_agent` made before the exception. Preserving partial progress on a hard failure would be a reasonable follow-up but wasn't done here since the retry fix addresses the reported symptom directly (transient blips, which are the common case) and touching the exception-handling shape was judged out of scope for this fix.

### Session 27 (2026-07-17) — Anthropic prompt caching (token optimization, `_anthropic_chat` only)
**Ask:** reduce token cost for multi-step agent runs (motivated directly by the Session 26 investigation — a 10-step MyCase query resends the full system prompt + all 47 tool schemas on every single step, since none of that static prefix was cached).

**Implementation (`app/services/model_gateway.py`, `_anthropic_chat`):**
- System prompt converted from a bare string to Anthropic's content-block array form so it can carry a cache marker: `body["system"] = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]`.
- The `tools` array gets `cache_control: {"type": "ephemeral"}` added to its **last** entry only — a cache breakpoint covers everything up to and including the marked block, so one marker at the end caches the whole array; the other tool entries are left unmarked.
- **Two separate breakpoints, not one, deliberately:** the tools array is stable across an entire conversation (helps cache hits turn-to-turn), while the system prompt contains `_current_date_header()` which changes minute-to-minute across separate `run_podio_agent`/`run_mycase_agent` invocations — but is identical across every step *within* one multi-step turn, which is exactly where the expensive repetition happens. Anthropic processes `tools → system → messages` internally, so this ordering means system's breakpoint naturally caches tools too when both are present.
- Response `usage.cache_creation_input_tokens` / `usage.cache_read_input_tokens` / `usage.input_tokens` are now logged on every `chat_ok` event — added specifically so cache hits can be verified from logs rather than assumed to be working silently.
- Scope: Anthropic only. Gemini/Groq/Mistral/OpenAI have their own separate (or no) caching mechanisms and were explicitly left untouched — this only benefits conversations using a `anthropic:*` model.

**Verified:** mocked `httpx.AsyncClient.post` to capture the outgoing request body — confirmed the system block carries `cache_control` and the tools array's LAST entry carries it while earlier entries don't (exact intended shape); confirmed a mocked response's `cache_creation_input_tokens`/`cache_read_input_tokens` correctly flow through to the `chat_ok` log line. **Could not verify an actual cache HIT against the real Anthropic API** — `anthropic_api_key` is currently unset (`NULL`) in this environment's `integration_settings`, discovered while trying to test this live. This is unrelated to prompt caching itself but means **the MyCase Agent cannot currently run against Claude at all** (its selected model is `anthropic:claude-sonnet-5` per Session 26) until a valid key is added in Settings → Anthropic (Claude). Once a real key is present, verify actual cache hits by running a 2+ step MyCase query and checking `cache_read_input_tokens > 0` in the logs on the 2nd+ call.

**Not done:** incremental caching of the growing message/tool-result history within a turn (would need a cache_control marker moved to the last message on each request) — judged lower-value than the tools+system fix since tool schemas are the larger, fully-static chunk; left as a future enhancement if the simpler fix proves insufficient. Also not verified: whether the pinned `anthropic-version: 2023-06-01` header needs bumping for caching to be honored — could not check against a live call for the reason above; if cache_read_input_tokens stays 0 once a real key is added, check this first.

### Session 28 (2026-07-17) — field[custom_field] 400 bug fix + spreadsheet-style output with CSV export
**Reported bug:** a MyCase report query (immigration/asylum case report grouped by agent) hit `MyCase API 400: field[custom_field] Unsupported field` twice — the model (Claude) tried `field_custom_field=id,name,value` then `field_custom_field=id,name`, both rejected.

**Root cause:** neither the `get_cases`/`get_case` tool descriptions nor the system prompt ever told the model what values `field[custom_field]` actually accepts, so it guessed. Per MyCase's real docs, the nested `custom_field` sub-object on each `custom_field_values` entry supports expanding ONLY to `id,field_type` — there is no `name` or `value` sub-field. Worse, the model didn't need to expand anything at all: `custom_field_values[].value` is **already included by default** as a sibling field, not something behind the expansion. The correct pattern for "find cases where custom field X = Y" is: call `get_custom_fields()` once to resolve field name → numeric id, then match that id against each case's `custom_field_values[].custom_field.id` — no `field[custom_field]` needed at all for this use case.

**Fix:**
- `app/mcp_servers/mycase.py` — `get_cases`/`get_case` tool descriptions rewritten to state the exact valid `field_client`/`field_custom_field` sub-fields (the complete list, not examples) and to explicitly say `value` is always present without expansion.
- `app/services/mycase_agent.py` — new CUSTOM FIELDS section in the system prompt covering: the default-included `value`, the `id,field_type`-only expansion, the get_custom_fields()-then-match-by-id pattern for resolving a custom field's name, and that filtering by a custom field's value is done client-side in the agent's own reasoning (there's no `filter[...]` query param for it).

**Second ask, same conversation — "standard output like an excel sheet including all the columns" + a CSV download option.** The existing fallback table (`_render_items_table` in `mycase_agent.py`, used server-side only when the model claims to show data but doesn't) was always a bare 3-column `id | Title | Details` table — never designed to be the primary way of viewing a full record set, and there was no download capability at all.

**Fix (frontend, `mycase-agent/page.tsx` + new `frontend/lib/recordTable.ts`):** rather than depend on the LLM to format a good wide table in its prose reply (unreliable — see the entire Session 18/20/etc. history of models not showing data properly), the tool-call **step card** itself now deterministically renders any `items[]` result as a real HTML table — columns are the union of every key seen across all items (so a mixed result set still gets every column, not just the first item's shape), with each cell's value flattened via `flattenValue()` (nested objects prefer a human label — name, or first+last name, or the bare id — over raw JSON; arrays join their flattened entries). A "Download CSV" button next to the table exports the exact same data (UTF-8 BOM prefixed so Excel doesn't mangle non-ASCII names) via a client-side `Blob` — no backend round-trip, no server-side export/session state needed. This is shape-agnostic by design since MyCase alone has 20+ distinct record shapes across its 46 read endpoints; hardcoding per-resource column lists was rejected as unmaintainable.
- Verified: syntax/import OK (backend); `tsc --noEmit` shows zero new errors (same 6 pre-existing unrelated ones); ran the flatten/CSV logic standalone against a realistic 2-case MyCase-shaped payload (including a comma-containing case name, a nested `staff` array, and `custom_field_values`) — confirmed correct CSV quoting, correct column derivation across mixed-shape items, and confirmed the custom field VALUE (not just its id) flattens into the CSV cell as intended, directly reflecting the bug-fix above. Full Podio + MyCase session regression unaffected.

### Session 29 (2026-07-17) — `aggregate_cases`: deterministic filter/exclude/group-by-count reporting (tool #48)
**Reported:** the same asylum-report request, now correctly avoiding the Session 28 field[custom_field] 400, instead dumped **1000 raw unfiltered cases** as a bare `id | Label` table into the chat reply — nowhere near "case count per assigned agent for active asylum cases."

**Root cause, two layers:**
1. The Session 18/20 "claimed to show records but didn't" completeness guard fired (correctly, per its own logic — the model's reply didn't contain the record ids) but it doesn't distinguish a **listing** request ("show me all X" — where dumping the raw list is the right answer) from a **reporting/aggregation** request ("count X grouped by Y, excluding Z") — for the latter, forcing a raw unaggregated dump is actively wrong, not just unpolished.
2. Deeper problem: even without the guard forcing a bad dump, **this class of request can't be reliably computed by an LLM reading raw case JSON in the first place.** Every tool result is truncated at `_MAX_TOOL_OUTPUT_CHARS` (6000 chars) before it reaches the model — across hundreds/thousands of cases with nested client/staff/custom-field data, the model never actually sees most records' custom field values at that scale. Fixing the guard alone would still leave the underlying aggregation unreliable.

**Fix: `aggregate_cases`, a new deterministic tool (`app/services/mycase_rest.py`) that does the fetching, filtering, exclusion, and grouping/counting in Python — not by the LLM reading through raw results.**
- Walks EVERY page of `get_cases()` internally (server-side, via `next_page_token`, no cap the LLM would ever notice) — raw case JSON is never exposed to the model's context at any point.
- `practice_area` (builtin field) and `custom_field_filters` (`{custom field NAME: substring value}`) filter case-insensitively as substrings (so `"Asylum"` correctly matches both "Asylum - Affirmative" and "Asylum - Defense").
- `exclude_case_stages` matches case-insensitively but EXACTLY (by design — the agent is instructed to resolve a user's loose stage description to the real `get_case_stages()` strings first, since MyCase's actual stage names are inconsistent/abbreviated and fuzzy-matching them server-side would silently misclassify).
- Custom field NAMES (not ids) are accepted directly — resolved once via a `get_custom_fields()` name→id lookup (`_custom_field_name_to_id`), so the agent (and the user) can refer to "CASE TYPE" / "PROCESSING AGENT" by name.
- `group_by` accepts either a builtin case field or a custom field name; counts are computed in Python (`dict` tally), not estimated.
- Returns an already-flattened `items[]` — one row per surviving case, each row carrying the report-level `total_cases`/`total_groups`/`report_date` PLUS that row's group's `case_count` — this is the exact flattened-report shape the user asked for (`total_cases, total_processing_agents, processing_agent_name, report_date, case_count, case_id, ...`), so the agent only needs to present the result, never reshape or recompute it.
- Registered as MCP tool `aggregate_cases` (`app/mcp_servers/mycase.py`, tool #48) with a description that explicitly forbids the model from hand-counting via `get_cases` for anything beyond a handful of records.

**System prompt (`mycase_agent.py`):** new "REPORTS: FILTERING, EXCLUDING, AND COUNTING CASES" section — routes any count/group-by/breakdown-shaped request to `aggregate_cases`, with an explicit 3-step resolve-then-call workflow (resolve stage names via `get_case_stages()`, resolve custom field names via `get_custom_fields()`, THEN call `aggregate_cases` once with the resolved exact values). Added `aggregate_cases` to the Ollama keyword-priority map (`report`, `count`, `group by`, `breakdown`, `how many`, `per agent`).

**Bug found and fixed while wiring this up:** the completeness-guard's "was this already shown" check (`_render_multi_resource_tables`) only ever looked for an `it.get("id")` key in the reply text — `aggregate_cases` rows use `case_id` (to avoid colliding with the report's own several id-like fields), so without a fix every `aggregate_cases` result would ALWAYS be judged "not shown" and force-appended even when the model presented it correctly. Added `_row_id()` (checks `id` then falls back to `case_id`) and used it everywhere the old bare `it.get("id")` was.

**Also upgraded `_render_items_table` (the text fallback) from a bare 2-column `id | Label` table to a real multi-column table** — new `_derive_columns()` (union of keys across all rows, mirrors the Session 28 frontend `deriveColumns()`) and `_flatten_cell()` (mirrors the frontend's `flattenValue()`: nested objects prefer a human label over raw JSON). This means even the worst-case text-only fallback path now renders something report-shaped instead of a useless id+label dump, consistent with the Session 28 frontend table.

**Verified:** `aggregate_cases`'s filter/exclude/group/count logic tested standalone against synthetic multi-page case data (5 cases across 2 simulated pages) mocking `get_cases`/`get_custom_fields` — confirmed correct page-walking, correct practice-area + custom-field substring filtering, correct exact-match stage exclusion, correct custom-field-name-based grouping and per-group counts, and correct flattened row shape. Confirmed 48 tools register end-to-end through the real MCP round-trip. Reproduced the exact reported bug shape (a reply that doesn't show the report) and confirmed the fixed completeness guard now correctly detects `case_id`-keyed rows and renders a proper wide table instead of the old id+Label dump. Regression-tested the standard `id`-keyed path (all other 47 tools) still works unchanged, and that an already-correct reply still isn't duplicated. Full Podio + MyCase session regression against the live server unaffected.

### Session 30 (2026-07-17) — `aggregate_cases` custom-field resolution silently missed fields past position 25 + transient network retry gap in `mycase_rest.py`
**Reported:** the user did everything right this time — confirmed exact stage names via `get_case_stages()`, confirmed the exact custom field name "CASE TYPE" via `get_custom_fields()` AND via a real case-detail screenshot (Session verified in the prior turn) — yet `aggregate_cases(custom_field_filters={"CASE TYPE": "Asylum"})` kept failing with `'CASE TYPE' is not a builtin case field ... or a known custom field name`, across `~10` retries with different casings/phrasings, all rejected. One attempt also hit `"All connection attempts failed"`.

**Root cause 1 (the main bug):** `_custom_field_name_to_id()` called `self.get_custom_fields()` with **no arguments and no pagination loop** — meaning it only ever saw the first page (MyCase's default `page_size=25`) of this firm's 46 custom fields. Counting the real field list (visible in the Session 28 transcript): "PROCESSING AGENT" sits at position ~27 and "CASE TYPE" at position ~34 — **both past the unpaginated 25-item cutoff**, so they were silently absent from the resolved name→id map regardless of how correctly the user (or the model) spelled them. The tool's own error message name-checked against an incomplete map and reported a real, valid field as unknown. Every retry with different phrasing was doomed the same way — the phrasing was never the problem.
- **Fix:** `_custom_field_name_to_id()` now walks every page (`page_size=200` + `next_page_token` loop, same pattern already used by `aggregate_cases`'s own case-fetching walk) before returning the map. Verified live with a mocked 2-page/46-field split (25 + 21, "PROCESSING AGENT" and "CASE TYPE" deliberately placed on page 2) — both now resolve correctly.

**Root cause 2 (the connection error):** `mycase_rest.py`'s `_request()` already had a retry loop, but only for **HTTP 429** — checked via `resp.status_code` *after* a response came back. A `httpx.ConnectError` ("All connection attempts failed" — the exact message reported) is raised *instead of* returning a response, so it skipped the retry loop entirely and propagated straight to the user. This is the identical bug class fixed in `model_gateway.py` in Session 26, just never ported over to `mycase_rest.py`'s own HTTP layer (which makes its own separate set of calls, unrelated to the LLM provider calls Session 26 covered).
- **Fix:** wrapped the request call in `try`/`except httpx.TransportError`, retrying (same `[1,2,4]`s backoff) before giving up. Verified live: mocked 2 consecutive `ConnectError`s followed by a success — confirmed it retries and recovers on the 3rd attempt exactly like the Session 26 fix for the LLM-call path.

**"Add CSV download" — already exists, wasn't reachable.** The Session 28 table+CSV feature (step-card `RecordsTable` + Download CSV button) already triggers automatically for any tool result with a non-empty `items[]` array, which is exactly `aggregate_cases`'s return shape — no new UI work was needed. It simply never appeared because `aggregate_cases` had not once succeeded yet (every call up to this point errored before returning any `items`). No code change beyond the two fixes above was required for this part.

**Verified end-to-end:** re-ran the exact reported scenario (46 custom fields split across 2 pages, "CASE TYPE"/"PROCESSING AGENT" both on page 2, 3 synthetic cases exercising practice-area filter + custom-field substring filter + stage exclusion + unassigned-agent grouping) through the full `aggregate_cases()` call — correctly resolved both custom fields, correctly excluded the closed case and the "IMMIGRATION- SUBMITTED (MAIL/UPLOAD PACKAGE)" case, correctly bucketed the no-agent case as `"(unassigned)"`. Full Podio + MyCase session regression against the live server unaffected.
