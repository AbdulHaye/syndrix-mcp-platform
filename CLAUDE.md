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
#   conversation.create → "conversation.subject", "conversation.text", "conversation.participant"
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
