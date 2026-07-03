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
│   ├── agent.py                 ← POST /agent/podio (BD+Admin only)
│   ├── integrations.py          ← /integrations/podio/* (OAuth PKCE for hosted MCP)
│   ├── podio_files.py           ← /integrations/podio-files/* (REST OAuth + file upload)
│   └── llm.py                   ← GET /llm/models, POST /llm/model
├── adapters/                    ← Thin HTTP wrappers (podio, ghl, slack, github, email)
│   └── podio.py                 ← LEGACY REST adapter — NOT used by agent (client_credentials 403s)
├── mcp_tools/                   ← MCP tool registrations (crm, dev, mgmt, rag, prompt, memory)
├── mcp_servers/
│   └── podio_files.py           ← Standalone FastMCP server: 50+ Podio REST tools
├── services/
│   ├── podio_mcp.py             ← OAuth PKCE + MCP client for mcp.podio.com (hosted MCP)
│   ├── podio_rest.py            ← Full Podio REST API client (60+ methods, auth_code OAuth)
│   ├── podio_files_client.py    ← In-process adapter: calls podio_files FastMCP server
│   ├── podio_agent.py           ← Agent loop: LLM + Podio tools → reply
│   ├── model_gateway.py         ← Multi-provider LLM: chat(), generate(), embed()
│   ├── settings_service.py      ← DB-backed key-value settings (integration_settings table)
│   ├── rag.py                   ← RAGService: chunk + embed + pgvector store + hybrid search
│   ├── audit.py                 ← AuditService
│   └── memory.py                ← MemoryService (Redis short-term + pgvector long-term)
├── storage/
│   ├── db.py                    ← Async SQLAlchemy engine + session
│   ├── models.py                ← ORM: AuditLog, KnowledgeDocument, TeamToken, User, IntegrationSetting
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
│       ├── settings/page.tsx    ← Credentials UI (always-visible integrations + opt-in LLM providers)
│       ├── crm/page.tsx, dev/page.tsx, mgmt/page.tsx, rag/page.tsx, admin/page.tsx
│       └── prompts/page.tsx     ← Prompt Packs UI
├── components/
│   ├── Sidebar.tsx              ← Dark fixed sidebar; Podio Agent is top-level under Team Tools
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
| `google_api_key`, `groq_api_key`, `mistral_api_key`, `openai_api_key`, `anthropic_api_key` | Settings UI |
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

# Update flow — effects use "values" (not "attributes")
PUT /flow/{flow_id}/
{"name": "...", "effects": [{"type": "comment.create", "values": [{"attribute_id": "comment.value", "value": "text"}]}]}

# Trigger types: item.create, item.update, item.delete
# Effect types and their official attribute_id strings (developers.podio.com/doc/flows):
#   comment.create      → "comment.value"
#   status.create       → "status.value"
#   task.create         → "task.text", "task.due" (int days), "task.responsible"
#   item.update field   → "item.field.{external_id}"
#   conversation.create → "conversation.subject", "conversation.text", "conversation.participant"
# field_ids: numeric field.field_id from get_app — NOT external_id strings
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
