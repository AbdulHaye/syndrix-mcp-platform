# Syndrix — Project Handbook

> **Rule:** Every Claude session MUST read this file first. Do not rely on terminal history or prior conversation. After completing work, append one line to [Session History](#session-history) at the bottom.

---

## What This Project Is

An internal MCP server acting as a **shared AI capability hub** for three teams:

| Team | Role key | Typical tools |
|------|----------|---------------|
| BD (Business Development) | `bd` | CRM contacts, leads, notes, email, Podio Agent, MyCase Agent |
| Software Dev | `dev` | Repo search, tickets, spec gen, bug triage |
| Management | `mgmt` | Daily reports, client health scores |

**Design rule:** the MCP server is a capability backend, NOT an agent — business rules stay above the tool layer. Adapters go in `app/adapters/`, tools go in `app/mcp_tools/`.

The two flagship features are the **Podio Agent** (read/write chat agent over Podio) and the **MyCase Agent** (read-only chat agent over MyCase legal practice management software) — both are natural-language chat pages where an LLM decides which tools to call. See their own sections below.

---

## Stack

| Layer | Technology |
|-------|-----------|
| API framework | FastAPI + FastMCP (MCP Python SDK) |
| Language | Python 3.11 |
| Database | PostgreSQL + pgvector (asyncpg + SQLAlchemy async) |
| Cache / broker | Redis |
| Local LLM | Ollama (`llama3.2` default, `nomic-embed-text` embeddings) |
| Cloud LLMs | Google Gemini, Groq, Mistral, OpenAI, Anthropic, Z.ai (GLM), OpenRouter |
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
│   ├── agent.py                 ← POST /agent/podio + /agent/podio/sessions CRUD
│   ├── mycase_agent.py          ← POST /agent/mycase + /agent/mycase/sessions CRUD + /agent/mycase/status
│   ├── integrations.py          ← /integrations/podio/* (OAuth PKCE for hosted MCP)
│   ├── podio_files.py           ← /integrations/podio-files/* (REST OAuth + file upload)
│   ├── integrations_mycase.py   ← /integrations/mycase/* (OAuth connect/callback/disconnect)
│   └── llm.py                   ← GET /llm/models?agent=podio|mycase, POST /llm/model
├── adapters/                    ← Thin HTTP wrappers (podio, ghl, slack, github, email)
├── mcp_tools/                   ← MCP tool registrations (crm, dev, mgmt, rag, prompt, memory)
├── mcp_servers/
│   ├── podio_files.py           ← Standalone FastMCP server: 70+ Podio REST tools
│   └── mycase.py                ← Standalone FastMCP server: 54 MyCase tools
├── services/
│   ├── podio_mcp.py             ← OAuth PKCE + MCP client for mcp.podio.com (hosted MCP)
│   ├── podio_rest.py            ← Custom Podio REST client (write ops, files, flows, etc.)
│   ├── podio_files_client.py    ← In-process adapter → podio_files FastMCP server
│   ├── podio_agent.py           ← Podio agent loop: LLM + Podio tools → reply
│   ├── mycase_rest.py           ← MyCase REST client (46 GET methods + 7 deterministic tools)
│   ├── mycase_utbms.py          ← Static UTBMS/LEDES billing-code reference table
│   ├── mycase_client.py         ← In-process adapter → mycase FastMCP server
│   ├── mycase_agent.py          ← MyCase agent loop: LLM + MyCase read-only tools → reply
│   ├── model_gateway.py         ← Multi-provider LLM: chat(), generate(), embed()
│   ├── settings_service.py      ← DB-backed key-value settings (integration_settings table)
│   ├── rag.py                   ← RAGService: chunk + embed + pgvector store + hybrid search
│   ├── audit.py                 ← AuditService
│   └── memory.py                ← MemoryService (Redis short-term + pgvector long-term)
├── storage/
│   ├── db.py                    ← Async SQLAlchemy engine + session
│   ├── models.py                ← ORM: User, IntegrationSetting, PodioChatSession, MyCaseChatSession, ...
│   └── vector.py                ← VectorStore: store() + search()
└── workers/                     ← Celery tasks (jobs.py) + Beat schedule (periodic.py)

frontend/
├── app/
│   ├── page.tsx, login/page.tsx ← Landing + login
│   └── dashboard/
│       ├── layout.tsx           ← Sidebar + AuthGuard + ToastProvider
│       ├── podio-agent/page.tsx ← Podio Agent chat UI
│       ├── mycase-agent/page.tsx← MyCase Agent chat UI (virtualized tables for large results)
│       ├── settings/page.tsx    ← Credentials UI (integrations + opt-in LLM providers)
│       └── crm/, dev/, mgmt/, rag/, admin/, prompts/  ← other team-tool pages
├── components/Sidebar.tsx, Markdown.tsx, JsonTree.tsx
└── lib/api.ts, recordTable.ts, auth.ts
```

---

## Auth

- **JWT:** `POST /auth/login` → JWT (24h). Default admin: `admin@syndrix.local` / `changeme`.
- **DEV_TOKENS fallback:** `bearer.py` tries JWT first, then `DEV_TOKENS` env var (`team:token,...`).
- **Roles:** `bd`, `dev`, `mgmt`, `admin`. RBAC (`app/auth/rbac.py`) maps tool-name prefix → allowed roles (`crm.*`/`agent.*` → BD+ADMIN, `repo.*`/`ticket.*` → DEV+ADMIN, `report.*` → MGMT+ADMIN, `mycase.*` → BD+ADMIN, `health.*` → all).

---

## Environment Variables

```
APP_ENV=development
SECRET_KEY=..., JWT_SECRET_KEY=...
DATABASE_URL=postgresql+asyncpg://mcp_user:mcp_pass@localhost:5432/syndrix
REDIS_URL=redis://localhost:6379/0
OLLAMA_BASE_URL=http://localhost:11434, OLLAMA_DEFAULT_MODEL=llama3.2, OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_TIMEOUT=300, OLLAMA_NUM_CTX=8192       # must be >2048 for prompt + tool schemas to fit
PGVECTOR_ENABLED=false                        # true only if the pgvector extension is installed
DEV_TOKENS=bd_team:bd-secret-token-123,...
ADMIN_EMAIL=admin@syndrix.local, ADMIN_PASSWORD=changeme
PODIO_CLIENT_ID=, PODIO_CLIENT_SECRET=        # shared by hosted-MCP OAuth and REST OAuth
GHL_API_KEY=, SLACK_BOT_TOKEN=, GITHUB_TOKEN=, GITHUB_ORG=, SMTP_HOST/PORT/USER/PASSWORD/FROM=
```
All other integration/LLM credentials (Podio, MyCase, and 7 LLM provider API keys) are stored DB-side via Settings UI, not env vars — see each agent's Settings table below.

---

## Running Locally

```bash
# psql: CREATE USER mcp_user WITH PASSWORD 'mcp_pass'; CREATE DATABASE syndrix OWNER mcp_user;
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
cd frontend && npm install && npm run dev         # http://localhost:3000
celery -A app.workers.jobs worker --loglevel=info # optional
```
⚠️ **Postgres runs as a native Windows service, not Docker.** A stale `syndrix-postgres` Docker container also binds 5432 — keep it stopped, or the port conflict hangs the native service (see Session 15).

---

## ⭐ Podio Agent

**What it is:** a chat page (`/dashboard/podio-agent`) where natural language drives Podio actions — reads, writes, files, flows, tasks, calendar, everything. Two separate Podio connections run in parallel, because `client_credentials` OAuth (authenticates only the API key, not a user) 403s on every real call:

1. **Hosted MCP** (`mcp.podio.com`) — reads/search only, OAuth PKCE via "Connect Podio".
2. **Custom REST layer** (`podio_rest.py` + `mcp_servers/podio_files.py`) — writes, files, flows, webhooks, tasks, workspaces, conversations, calendar. Separate OAuth via "Connect Files". REST tools override same-named hosted tools when connected.

**Setup:** Settings → Podio (MCP): Client ID/Secret. Connect Podio → Connect Files → pick workspace → pick a **strong** model (Groq 70B / Mistral Large / Gemini / Claude — `llama3.2` 3B is too weak for multi-step tool calling, `llama3` doesn't support tools at all).

**Agent loop (`podio_agent.py`):** merges tools from both connections (dedup by name); **write-gating** hides write tools unless the message shows write intent (`_wants_write`); Ollama models get a keyword-priority-trimmed tool list (cap 18); loops up to 12 steps calling `model_gateway.chat()`. Heavy hardening layered in over many sessions: recovers tool calls a weak model emits as garbled/glued text or a self-describing `{"name":...,"parameters":...}` JSON envelope instead of a real function call; strips hallucinated content that rides along with a real tool call; guards against fabricated/wrong-but-plausible object IDs (`_guard_object_ids`, substitutes the real id when exactly one candidate was seen this turn); an honesty guard rejects a "success"/"here's the table" claim when the matching tool never actually succeeded, forcing a real retry or an honest failure instead.

**`model_gateway.py`** dispatches `"provider:model"` strings to per-provider chat methods (`ollama`, `google`, `groq`, `mistral`, `openai`, `anthropic`, `zai`, `openrouter`) — Groq/Mistral/OpenAI/Z.ai/OpenRouter share one `_openai_compat_chat()` helper. All provider paths retry transient transport errors (not HTTP error responses) 3x with backoff. Anthropic responses use prompt caching on the system prompt + tool schemas to cut token cost across multi-step turns.

**Podio REST gotchas (full endpoint reference: docstrings in `podio_rest.py`):**
- Auth is `Authorization: OAuth2 <token>` (not Bearer); token endpoint wants a JSON body.
- Flow create: `POST /flow/app/{app_id}/` (NOT `/flow/`, which 404s). Effects are an ARRAY of `{attribute_id, value}`; every value must be a STRING. Only `task.create`/`comment.create`/`status.create` effects actually work — there is **no "update a field" effect** (needs Podio's separate GlobiFlow product, not integrated here).
- Flow update: `PUT /flow/{id}/` is a full replace requiring `name` — our `update_flow()` fetches the current flow and merges first. Uses `"attributes"` like create (a `"values"` key 500s).
- A flow has no `active` key at all — it's live the moment it's created; there's no activation endpoint.
- `config.field_ids` accepts a numeric id, external_id, or label — resolved against the live app; never invent one.
- Hosted MCP has no `delete_item` tool and its `update_item` 403s — use the custom REST versions.
- `task/total/` returns time-bucket counts only (no `completed`/`app_id` filter); task ranking (`/task/{id}/rank/`) is HTTP 410 Gone (removed by Podio).
- The activity stream (`GET /stream/...`) — not `get_items` sorted by `last_edit_on` — is the correct source for "what changed / recently updated," since comments/files don't bump `last_edit_on`.

**Settings keys:** `podio_mcp_client_id/secret`, `podio_rest_client_id/secret` (optional, falls back to mcp_*), OAuth tokens (auto-managed), `podio_mcp_space_id`, `agent_model`.

---

## ⭐ MyCase Agent

**What it is:** a second, independent chat page (`/dashboard/mycase-agent`, BD+Admin only) for MyCase legal practice management software. **Read-only** — 54 tools: 46 real GET/download endpoints + 8 deterministic tools (`aggregate_cases`, `aggregate_invoices`, `search_cases`, `find_cases_with_documents`, `get_case_folder_tree`, `get_invoices_by_date`, `get_case_invoices`, `lookup_utbms_code`). No create/update/delete yet.

**Core design principle:** anything that filters, counts, or searches across more than a handful of records is computed **server-side in Python**, never left for the LLM to eyeball raw JSON — an LLM sampling a truncated dataset reliably produces confident, wrong answers once the data is bigger than what fits in one glance. Every deterministic tool exists because a "let the model call get_X and figure it out" version was tried first and got it wrong.

**Real API:** `https://external-integrations.mycase.com/v1`, Bearer auth, cursor pagination (`page_size`/`page_token`, `Item-Count` header = the TRUE total across all pages). OAuth: browser login at `auth.mycase.com/login_sessions/new`, token exchange/refresh both at `auth.mycase.com/tokens` (JSON body, not form-encoded). Settings → MyCase: Client ID/Secret/Redirect URI for the Connect flow, or paste an access+refresh token pair directly (no refresh token = stops working silently after 24h).

**Settings keys:** `mycase_client_id/secret`, `mycase_redirect_uri`, `mycase_access_token`/`refresh_token`/`token_expiry` (OAuth-managed), `mycase_agent_model`.

**Connection status** (`GET /agent/mycase/status`) reports real token expiry/countdown, not just "is a token present" — the UI badge shows time remaining and turns amber ("expired, will self-heal") or red ("expired, reconnect required") instead of always saying "Connected."

**Hard-won lessons (each was a real, reported bug — now fixed):**
- **Scan caps must be dynamic, not a fixed guess.** Every full-table walk (cases, invoices, documents) probes the REAL total first (`page_size=1`, reads `Item-Count`) and sizes the scan to it, capped by a 100,000 safety ceiling — a hardcoded 5,000/20,000 cap used to make real, correctly-spelled cases and most of a firm's invoices silently invisible once the account grew past the guess (confirmed live: 7,216 real cases, 48,161 documents, 6,188+ invoices).
- **MyCase's only server-side date filter is `updated_after`.** Anything else (exact day, `invoice_date`, `due_date`, etc.) is fetched broad and filtered exactly in Python (`get_invoices_by_date`) — never trust the model to eyeball-filter a raw batch.
- **`get_invoices` hides invoices with online payments disabled by default** unless `only_allowed_online_payments=false` is passed — every deterministic invoice tool here defaults to ALL invoices.
- **`total_amount`/`paid_amount` sometimes come back as strings**, not numbers, on real invoices — always coerced via `_as_float` before arithmetic.
- **Anti-hallucination guards:** a read-only agent can still fabricate a confident number with no real tool call behind it, or flatly contradict its own successful result (e.g. claim "no invoices" when the fetched data shows one) — both are caught and force a correction before the reply reaches the user.
- **Chat UI has no tool-call step-card panel.** The model gives a one-line acknowledgment; the real table (+ Download CSV) renders directly from the raw tool result, independent of what the model's text says. Tables over 200 rows are row-virtualized so a 5,000+ row result doesn't slow the page down.

**Known gaps:** no write operations implemented; a few requested report fields ("Days in Current Stage," "Case Owner") don't exist anywhere in MyCase's API and were left out rather than faked.

---

## Key Patterns

1. New adapters/REST methods: `httpx.AsyncClient` with `async with`, timeout 30–120s.
2. New MCP tools: `@mcp.tool(name="prefix.action")` inside `register_*_tools(mcp)`, registered in `main.py`.
3. Tool name prefixes must match `TOOL_PERMISSIONS` in `rbac.py` or they're unrestricted.
4. No business logic in adapters — thin HTTP wrappers only.
5. Adapters raise exceptions; tools catch and return `{"success": False, "error": ...}`.
6. Logging: `structlog.get_logger(__name__)` → `logger.info("event_name", key=val)`.
7. Settings UI: integrations always-rendered; LLM providers opt-in, persisted in `localStorage` under `syndrix_added_llms`.
8. Anything that scans/aggregates more than a handful of records: compute it server-side (Python), never leave it to the LLM — this is the single most repeated lesson across the MyCase Agent's whole history above.
9. Never write a real credential/settings DB row during testing (even "set then reset") — mock `get_setting`/`upsert_setting` instead. This has broken a real saved MyCase token twice.

---

## Session History

*(Condensed — each entry is what changed and why it mattered, not a full incident writeup. Full historical detail for older entries lived here before this file was compacted; git history has the original text if ever needed.)*

**1–4** (Apr 2026): Initial build — skeleton, real adapters (Podio/GHL/Slack/GitHub/SMTP), RAG pipeline, full Next.js UI.
**5:** Removed Docker; fixed tool-invoke 404 and GitHub search. Project is local-only.
**6:** Backend bug fixes; full frontend redesign (indigo theme, new sidebar, landing page).
**7:** Prompt packs, memory tools, CRM AI summaries, hybrid RAG search, Redis caching, Celery Beat.
**8:** Built the first Podio Agent; pivoted from `client_credentials` (always 403s) to Podio's hosted MCP + OAuth.
**9:** Added Google Gemini as 2nd LLM provider + model selector dropdown.
**10:** Added Groq/Mistral/OpenAI/Anthropic; custom Podio file upload; big agent reliability pass; chat UI upgrades.
**11:** Fixed a file-vs-item deletion bug; added file delete/download; JWT expiry raised to 24h.
**12:** Full agent + system-prompt audit; fixed several wrong Podio REST endpoints (task assign/uncomplete/rank/count).
**13:** More REST fixes (`get_tasks`, `update_task`); flow API endpoint/format corrections; ID-transposition guard.
**14:** Calendar date-range fix, linked-account calendars, agent date-awareness, reminder fixes, ID anti-hallucination guard.
**15:** Infra only — fixed a stuck native/Docker Postgres port conflict that was causing login 500s.
**16:** Big Podio reliability sweep — stopped unwanted item cloning, real field-schema display on create, honesty guard for fake "success" claims, activity-stream tool for "recent" queries, per-app task tool, flow wizard UX + the discovery that only task/comment/status flow effects exist.
**17:** Fixed two real flow-create bugs (string coercion, field_id resolution) + a false "active" reading; fixed `update_flow`; fixed a false "replaced successfully" claim after a failed recreate.
**18:** Fixed "listed them in a table" replies with no real table — added a completeness guard that appends the real data when a reply claims to show it but doesn't.
**19:** Moved Podio Agent chat history to the database (was localStorage-only).
**20:** Generalized the Session 18 guard to multi-app + paginated results (was losing earlier apps'/pages' data).
**21:** Fixed tool-call recovery for a model emitting a self-describing `{"name":...,"parameters":...}` JSON envelope as plain text instead of a real function call.
**22:** Added Z.ai (GLM) as 7th LLM provider.
**23:** Built the MyCase Agent — a second, independent read-only chat agent (46 tools, own DB table, own model selector).
**24:** Implemented the real MyCase OAuth "Connect" flow (earlier guessed endpoint URLs were wrong).
**25:** MyCase error handling, 429 retry, UTBMS billing-code lookup tool.
**26:** Fixed "All connection attempts failed" — added transport-error retry to all 4 LLM provider paths.
**27:** Added Anthropic prompt caching to cut token cost on multi-step turns.
**28:** Fixed a MyCase custom-field 400 bug; added spreadsheet-style table + CSV export to the frontend.
**29:** Added `aggregate_cases` — deterministic case filter/group/count tool, replacing unreliable LLM-side counting.
**30:** Fixed `aggregate_cases` missing custom fields past page 25; added network retry to `mycase_rest.py`.
**31:** Fixed "N cases and their invoices" across 5 rounds of real bugs (missing `limit`, wrong tool choice, online-payment default, string amounts, dynamic invoice-scan cap, a truncation-flag false positive).
**32:** Added OpenRouter as 8th LLM provider.
**33:** MyCase "Connected" badge now shows real token expiry / countdown instead of a static badge.
**34:** Fixed "case not found" for a real, correctly-spelled case — case/document scan caps needed the same dynamic-sizing fix as invoices, plus a related truncation-flag bug.
**35:** Added `aggregate_invoices` — deterministic invoice filter/sort/limit tool, fixing "top N unpaid invoices."
**36:** Fixed MyCase chat UI slowdown on large (5,000+ row) tables — row virtualization + memoization.
