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
│   ├── mycase_agent.py          ← POST /agent/mycase + sessions CRUD + status + reports/{id} download
│   ├── integrations.py          ← /integrations/podio/* (OAuth PKCE for hosted MCP)
│   ├── podio_files.py           ← /integrations/podio-files/* (REST OAuth + file upload)
│   ├── integrations_mycase.py   ← /integrations/mycase/* (OAuth connect/callback/disconnect)
│   └── llm.py                   ← GET /llm/models?agent=podio|mycase, POST /llm/model
├── adapters/                    ← Thin HTTP wrappers (podio, ghl, slack, github, email)
├── mcp_tools/                   ← MCP tool registrations (crm, dev, mgmt, rag, prompt, memory)
├── mcp_servers/
│   ├── podio_files.py           ← Standalone FastMCP server: 70+ Podio REST tools
│   └── mycase.py                ← Standalone FastMCP server: 59 MyCase tools
├── services/
│   ├── report_builder.py        ← Agent-agnostic: report spec → multi-sheet .xlsx (openpyxl)
│   ├── report_store.py          ← Generated reports in Redis, 1h TTL, for the download endpoint
│   ├── podio_mcp.py             ← OAuth PKCE + MCP client for mcp.podio.com (hosted MCP)
│   ├── podio_rest.py            ← Custom Podio REST client (write ops, files, flows, etc.)
│   ├── podio_files_client.py    ← In-process adapter → podio_files FastMCP server
│   ├── podio_agent.py           ← Podio agent loop: LLM + Podio tools → reply
│   ├── request_origin.py        ← Derives Podio OAuth redirect URLs from the request
│   ├── mycase_rest.py           ← MyCase REST client (46 GET methods + 13 deterministic tools)
│   ├── mycase_utbms.py          ← Static UTBMS/LEDES billing-code reference table
│   ├── mycase_client.py         ← In-process adapter → mycase FastMCP server
│   ├── mycase_agent.py          ← MyCase agent loop: LLM + MyCase read-only tools → reply
│   ├── model_gateway.py         ← Multi-provider LLM: chat(), generate(), embed()
│   ├── settings_service.py      ← DB-backed key-value settings (integration_settings table)
│   ├── rag.py                   ← RAGService: chunk + embed + pgvector store + hybrid search
│   ├── audit.py                 ← AuditService
│   ├── memory.py                ← MemoryService (Redis short-term + pgvector long-term)
│   └── tracing.py               ← Langfuse client + score_current_trace() (see Observability below)
├── storage/
│   ├── db.py                    ← Async SQLAlchemy engine + session
│   ├── models.py                ← ORM: User, IntegrationSetting, PodioChatSession, MyCaseChatSession, ...
│   └── vector.py                ← VectorStore: store() + search()
└── workers/                     ← Celery tasks (jobs.py) + Beat schedule (periodic.py)

scripts/
└── seed_langfuse_regression_dataset.py  ← Re-runnable: freezes real prompts that once exposed
                                             agent bugs into Langfuse "datasets" for regression checks

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
└── lib/api.ts, recordTable.ts, auth.ts, crypto.ts (Web Crypto + pure-JS fallback, see below)
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
CORS_ORIGINS=                                 # comma-separated extra allowed frontend origins —
                                               # localhost:3000 is always allowed regardless. MUST
                                               # be set to the deployed frontend's real origin (e.g.
                                               # http://<host>:3100) on any hosted deployment, or
                                               # every API call 400s on preflight ("Failed to fetch"
                                               # in the browser) — see Hosted Deployment Notes below.
PODIO_CLIENT_ID=, PODIO_CLIENT_SECRET=        # shared by hosted-MCP OAuth and REST OAuth
GHL_API_KEY=, SLACK_BOT_TOKEN=, GITHUB_TOKEN=, GITHUB_ORG=, SMTP_HOST/PORT/USER/PASSWORD/FROM=
LANGFUSE_PUBLIC_KEY=, LANGFUSE_SECRET_KEY=, LANGFUSE_BASE_URL=  # see Observability section
LANGFUSE_TRACING_ENVIRONMENT=development, LANGFUSE_TRACING_ENABLED=true
MYCASE_AGENT_LLM_JUDGE_ENABLED=false          # opt-in; off by default (adds an LLM call per turn)
MYCASE_AGENT_LLM_JUDGE_MODEL=groq:llama-3.1-8b-instant
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

⚠️ **Redis on this dev box is 5.0.14 and the app cannot actually talk to it.** `redis-py` 8.x negotiates RESP3 with a `HELLO` command, which Redis only understands from **6.0** — so `ping()` raises `unknown command 'HELLO'`, `main.py`'s lifespan catches it, sets `app.state.redis = None`, and **every Redis-backed service silently degrades** (memory, cache, audit, report storage). Nothing logs an error at the point of use, so this is invisible until something that needs Redis quietly stops persisting. Redis appears healthy from the outside (the port is listening, `redis-cli` works) and `/health` does not report it. Fix properly by upgrading the Redis server to 6+; or pass `protocol=2` to `from_url` if 5.x must be supported.

---

## Hosted Deployment Notes

The app was built and tested purely on `localhost` for a long time; deploying to a real server (built via `frontend.Dockerfile` + a backend Dockerfile, both living only on the deploy server — not in this repo) surfaced several localhost-only assumptions baked into the code. All now fixed, but worth knowing if this is ever redeployed somewhere new:

- **CORS is not open by default.** `CORS_ORIGINS` (backend `.env`) must include the deployed frontend's real origin (e.g. `http://<host>:3100`) — `localhost:3000` is always allowed but nothing else is, unless set. Symptom when missing: login (and everything else) fails with a generic browser "Failed to fetch," and the real reason (a 400 on the CORS preflight `OPTIONS` request) only shows up in DevTools' Network tab, never in the caught JS error.
- **`crypto.subtle` and `crypto.randomUUID()` need a "secure context"** — HTTPS, or the browser's special-cased `http://localhost`. A plain-HTTP deployment on a raw IP (no domain, no TLS) is neither, so both are `undefined` there. This broke two things independently: `frontend/lib/crypto.ts` (client-side API-key encryption for Settings) and `makeSessionId()` in both `podio-agent/page.tsx`/`mycase-agent/page.tsx` (chat history session ids). Both now fall back to `crypto.getRandomValues()` — which has **no** secure-context restriction — instead: `crypto.ts` via `@noble/ciphers`/`@noble/hashes` (verified byte-identical output to `crypto.subtle` for the same key/nonce/plaintext), `makeSessionId()` via a hand-built UUID v4. Without the fix, chat history silently never saved: the id fell back to a non-UUID string, the backend's `_parse_session_id` 400s on it, and the save's `.catch()` swallowed the error completely — the chat kept working in memory the whole time, masking the bug. If this ever needs revisiting: the real long-term fix is HTTPS (even a self-signed cert on the raw IP works — the "secure context" check is scheme-based, not CA-trust-based — Let's Encrypt needs a domain, a self-signed cert doesn't).
- **Podio OAuth redirect URLs used to be hardcoded to `localhost`, with no Settings UI field and no whitelisted setting key to override them** — silently broken on any non-local deployment. Fixed by deriving them from the request itself (see `request_origin.py`, documented below in the Podio Agent section) instead of requiring manual configuration.
- **A slow/large agent response can hit a timeout that has nothing to do with this app.** A MyCase Agent "list all X" query against real firm data can legitimately take tens of seconds and return a multi-MB response; over a mobile network this can get killed by the carrier's own transparent proxy with a 504 before the backend finishes (confirmed live: the identical request succeeded on retry, `Remote Address` in DevTools showed a completely different IP than the server itself — a strong sign of an in-path mobile proxy, not a server bug). Worth ruling out with a same-request retry on WiFi/desktop before assuming it's a backend issue.

---

## ⭐ Podio Agent

**What it is:** a chat page (`/dashboard/podio-agent`) where natural language drives Podio actions — reads, writes, files, flows, tasks, calendar, everything. Two separate Podio connections run in parallel, because `client_credentials` OAuth (authenticates only the API key, not a user) 403s on every real call:

1. **Hosted MCP** (`mcp.podio.com`) — reads/search only, OAuth PKCE via "Connect Podio".
2. **Custom REST layer** (`podio_rest.py` + `mcp_servers/podio_files.py`) — writes, files, flows, webhooks, tasks, workspaces, conversations, calendar. Separate OAuth via "Connect Files". REST tools override same-named hosted tools when connected.

**Setup:** Settings → Podio: one OAuth Client ID/Secret pair (generated at `podio.com/settings/api`) powers both connections — `podio_rest.py`'s `_client_id()`/`_client_secret()` fall back to the MCP credentials whenever the (optional, no-longer-shown-in-UI) `podio_rest_client_id/secret` keys are unset. Connect Podio → Connect Files → pick workspace → pick a **strong** model (Groq 70B / Mistral Large / Gemini / Claude — `llama3.2` 3B is too weak for multi-step tool calling, `llama3` doesn't support tools at all).

**OAuth redirect URLs are auto-detected, not configured.** `app/services/request_origin.py` derives both the backend callback (`redirect_uri`, from the request's Host/X-Forwarded-Host) and the post-connect landing page (`frontend_redirect`, from Origin/Referer) per-request, so Connect works unmodified on localhost, a hosted IP, or a future domain — nothing to fill in in Settings for this (the old `podio_mcp_redirect_uri`/`podio_rest_redirect_uri`/`podio_mcp_frontend_redirect` setting keys still exist as an optional override for an exotic reverse-proxy setup, just no longer required or shown in the UI). What's still manual, because it's enforced by Podio itself, not us: **Podio ties one API key to one domain** — the key's Redirect URL (registered at `podio.com/settings/api`) must match whatever domain you're actually running on. A key registered for `localhost` will not authorize a hosted deployment or vice versa; running both simultaneously needs two separate keys, one per domain (confirmed live: a single key's Redirect URL, once pointed at a domain, works for BOTH the MCP and REST callback paths on that same domain — validation appears to be domain-level, not exact-path).

**Agent loop (`podio_agent.py`):** merges tools from both connections (dedup by name); **write-gating** hides write tools unless the message shows write intent (`_wants_write`); Ollama models get a keyword-priority-trimmed tool list (cap 18); loops up to 12 steps calling `model_gateway.chat()`. Heavy hardening layered in over many sessions: recovers tool calls a weak model emits as garbled/glued text or a self-describing `{"name":...,"parameters":...}` JSON envelope instead of a real function call; strips hallucinated content that rides along with a real tool call; guards against fabricated/wrong-but-plausible object IDs (`_guard_object_ids`, substitutes the real id when exactly one candidate was seen this turn); an honesty guard rejects a "success"/"here's the table" claim when the matching tool never actually succeeded, forcing a real retry or an honest failure instead.

**Confirm-before-write:** any of the 19 write tools (`create_item`, `update_item`, `delete_item`, `create_flow`, etc. — `_WRITE_TOOL_NAMES`) is now intercepted right before execution, using its FULLY resolved arguments (after space_id autofill/id-correction/field-cleaning already ran), and returned as `pending_action` in the response instead of being run — the chat UI renders a Confirm/Cancel card (`PendingActionCard` in `podio-agent/page.tsx`). Confirming round-trips that exact `{tool, args}` back as `confirmed_action` on the next call; only that ONE pre-approved action is allowed through that turn (a `confirmed_write_used` flag prevents it from silently green-lighting a second, different write in the same turn). Cancel needs no backend call. Writes used to execute immediately the moment write-intent was detected — this is a deliberate safety change, not a bug fix.

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

**Settings keys:** `podio_mcp_client_id/secret` (the one pair shown in the UI), `podio_rest_client_id/secret` (optional, falls back to mcp_*, no longer shown in the UI), `podio_mcp_redirect_uri`/`podio_rest_redirect_uri`/`podio_mcp_frontend_redirect` (optional overrides — normally auto-detected, see above), OAuth tokens (auto-managed), `podio_mcp_space_id`, `agent_model`.

---

## ⭐ MyCase Agent

**What it is:** a second, independent chat page (`/dashboard/mycase-agent`, BD+Admin only) for MyCase legal practice management software. **Read-only** — 59 tools: 46 real GET/download endpoints + 13 deterministic tools (`aggregate_cases`, `aggregate_invoices`, `aggregate_leads`, `aggregate_payments`, `aggregate_clients`, `find_duplicate_clients`, `get_case_payments`, `search_cases`, `find_cases_with_documents`, `get_case_folder_tree`, `get_invoices_by_date`, `get_case_invoices`, `lookup_utbms_code`). No create/update/delete yet.

**MyCase requests are retry-BUDGETED (`_request`).** Raised from the hosted logs as repeated `/clients attempt 1/2/3` lines. The retry loop had no overall ceiling: 4 attempts × 30s plus 1+2+4s backoff ≈ 127s for ONE call, measured **135.2s** against `/clients` (which 504s reliably on this account). Anything with a read timeout in front — nginx defaults `proxy_read_timeout` to 60s — gives up long before that, so the caller gets a 504 and never sees a real answer. Now: `_TOTAL_BUDGET_SECONDS = 45` across all attempts, per-attempt timeout shrinks to whatever budget remains, and a **504 gets one retry, not three** (it means MyCase's own gateway already burned a full timeout, so each retry costs another for an error that is rarely transient). 429 keeps its full retry run — it's cheap and genuinely transient. Measured after: **46.9s**, healthy endpoints unaffected (0.9s). A read timeout now also reports as *"MyCase's API did not respond within Ns"* rather than *"Could not connect"* — the connection was fine, and that distinction is what tells you whether to look at the network or at the endpoint.

**`custom_field_filters` has THREE value conventions, and the "has a value" one was missing.** `""` = field is blank, `"*"` = field has ANY value, anything else = substring match. Only the blank convention existed, so the vocabulary was one-sided: asked to "show Criminal cases WITH assigned attorneys", the model reached for the only attorney idiom it had been taught — the blank check — and returned the exact opposite set. Ground truth: 237 Criminal cases, **188 with an attorney**, 49 without. Polarity ("with" vs "without") now maps to opposite values and is spelled out in both the tool description and the prompt.

**A field-filtered result carries its own denominator.** The same failure also produced *"All 49 Criminal cases in your system have no assigned attorney"* — a claim about the whole population made from the filtered part of it, and the model could not have known better because it only ever saw the filtered count. `aggregate_cases` now returns `total_ignoring_field_filters` + `field_filter_note` ("49 of 237…") whenever a field filter narrows the set, and the prompt forbids "all"/"every"/"none" without quoting it.

**Fabricated group names are rejected (`_fabricated_group_names`).** A `group_by="assigned_attorney"` call failed with a MyCase 500, so the surviving result was grouped by practice area — and the reply relabelled it "Breakdown by Assigned Attorney: LANA JOSEPH 187, JESSICA PRIVITERA 1". The counts were real, the dimension and one name were invented. Structured breakdown lines are now checked against the group names that actually came back; a mismatch forces a retry naming the real groups and reminding the model that a FAILED group_by means the result it holds is grouped by something else.

**Unknown tool arguments are REJECTED, not dropped.** The single most dangerous bug class found so far: FastMCP silently discards arguments a tool doesn't declare, so the call still runs — just without the constraint the model believed it had applied — and the model then narrates the result as though the filter took effect. Confirmed live twice: `aggregate_cases(spec={…})` (build_report's parameter) applied NO filter at all, returned all 7,247 cases, and the reply described them as "open cases with unpaid balances"; `aggregate_invoices(sort_order="desc")` was ignored outright. `_unknown_tool_args` now blocks the call before it runs and returns an error naming the bad arguments and listing the real ones, so the model self-corrects. Only applies when the tool declares a schema.

**A grouped result automatically becomes a per-group workbook (`_auto_report_spec`).** Grouping is a request for a BREAKDOWN, but the chat can only ever render ONE flat table per resource — so "show invoices by assigned attorney" produced a correct text summary sitting above 1,888 undifferentiated rows ("still 1 full excel sheet"). When an `aggregate_*` call passes an explicit `group_by`, returns ≥2 groups and ≥200 rows, `build_report` now runs automatically in the loop and the flat rows are slimmed out (6,381 invoices → **3.4 KB**, 9 sheets). Decided deterministically, not by prompt, because the model only volunteered `build_report` when the question contained the word "excel". Two traps this hit, both now covered by tests: the resolved group value must be written onto the ROW under a real column (an invoice has no attorney field, so `split_by="assigned_attorney"` silently collapsed 8 groups into one "(none)" sheet), and `split_by` falls back to `group_name` — which every `aggregate_*` row sets — when the rows don't carry the named column. `_retarget_table_pointers` then deterministically rewrites "see the table below" when no table is rendered, because the prompt rule alone did not hold.

**`describe_entity_fields` — live field discovery.** "Cases with a missing SOL date" failed purely because nothing told the agent that `sol_date` is a real native case field. Patching that one field was a point fix; this generalizes it. Returns per-entity notable fields (including COMPUTED ones like `assigned_attorney`/`client_name`/`balance_due` that these tools calculate but MyCase never returns) merged with **live** `get_custom_fields()` output. Deliberately not a static dictionary: this account has 24 case custom fields, they differ per firm, and a hardcoded list goes stale invisibly. Verified live — the agent now calls it first, then filters `custom_field_filters={"sol_date": ""}` correctly (23 cases).

**Group-size filtering (`min_group_size`/`max_group_size`) — a SQL HAVING clause.** "Clients who have more than one case" filters on how big each GROUP is, not on any field of a single record, and there was no way to express that: the agent grouped everything and tried to eyeball the large groups, returning all 2,380 cases and answering wrong. Available on all four `aggregate_*` tools. `group_by="client_name", min_group_size=2` is the whole answer. Two things this taught, both now in the system prompt: (1) **it is a CASES question, not a clients question** — a client record carries no case count, so `aggregate_clients` is the wrong tool (and 504s here); ask "what am I counting?" and call the aggregate for *that* resource. (2) Rows are narrowed to surviving groups so `items`/`groups`/`total_*` all describe the same set, and `groups_before_size_filter` is returned so a narrow result is never mistaken for a small dataset.

**Dangling invoice→case references are real, and must never be presented as clients.** Confirmed live: the case walk is complete (7,247 of 7,247, untruncated) but **3,222 distinct case ids referenced by invoices return a genuine 404** — the invoice outlived the case. Those invoices group under `(case deleted in MyCase)` / `(no case linked)`. A real reply once opened with *"Top groups: (no case) ($77,091), (unknown case) ($46,110)"*, reading as though two clients were named that. `aggregate_invoices` now returns `unresolved_client_invoices` + `unresolved_note`, and the prompt forbids listing placeholder buckets as names — report them separately as unmatched, but never drop them (the amounts are real).

**⭐ Excel reports (`build_report`) — the LLM directs presentation, not computation.** Until this existed, the chat could only ever render **one flat table per resource type** (`primaryResultGroups` in `mycase-agent/page.tsx`), so a request like "active cases per attorney, each attorney's cases on its own sheet, plus a summary" had *no code path* that could satisfy it — every new output shape needed another hardcoded tool. Now the LLM emits a small declarative **spec** (`summary` = group_by + metrics; `detail_sheets` = split_by/columns/sort) and `app/services/report_builder.py` renders a real multi-sheet `.xlsx`. This is the **safe, declarative form** of the [code-execution agent pattern](https://www.anthropic.com/engineering/code-execution-with-mcp): no arbitrary code runs, so it adds no new attack surface — which matters because rows contain client-written case notes the agent already treats as hostile input. It does **not** contradict Key Pattern #8: the LLM picks the *layout*; every count/sum is still exact deterministic Python over the full row set.
- **`build_report` is the one tool NOT registered on the MyCase MCP server**, deliberately — it operates on rows the *current turn* already fetched, which only the agent loop holds. Its schema is declared in `mycase_agent.py` (`_BUILD_REPORT_TOOL_SPEC`) and the call is intercepted in the dispatch loop, alongside the existing `aggregate_cases` limit gate. Routing it through `mycase_client` would re-run a 60–80s page walk for data already in memory.
- **It refuses a `limit`ed source.** `aggregate_cases(limit=N)` caps `items` while `groups` still reflects the full set, so reporting off it would silently under-count. The tool rejects that with an actionable error rather than shipping a partial workbook. A MyCase-side scan-cap (`truncated`) degrades to a disclosed note instead.
- **Payload slimming (`_slim_reported_steps`).** `steps[]` ships the *complete* raw tool result to the browser — tool-output truncation (`_MAX_TOOL_OUTPUT_CHARS`) only ever applied to what the *model* sees — and the frontend then persists it verbatim into the chat-session DB row. One measured "list all open cases" turn was **9.8 MB**, to the browser *and* into Postgres. When a resource's rows went into a workbook, its `items` are now replaced with `{items_omitted, items_omitted_reason}` at the very end of the turn — after every guard has read `items_by_resource` (a separate structure), so reply validation keeps full fidelity while the wire payload collapses.
- **Storage/delivery:** bytes in Redis with a 1h TTL when Redis is available (`report_store.py` — base64'd, because the shared client is `decode_responses=True`), **falling back to a temp DIRECTORY, not process memory**. That distinction is load-bearing: with Redis effectively unavailable on this box (see the Redis 5.x note under Running Locally), an in-memory report vanished the moment `uvicorn --reload` restarted on a file save, so a successfully-built report 404'd on download. Files survive restarts and multiple workers on one host. Served by `GET /agent/mycase/reports/{id}` scoped to the creating team (ids are validated against a charset allowlist first — they land straight in a filename), downloaded via an authed blob fetch (`downloadMyCaseReport`) and rendered as a `ReportCard`. Mirrors the pre-existing Podio export plumbing.
- **Spreadsheet formula injection is handled** (OWASP CWE-1236): a MyCase field starting with `= + - @` would execute on open, so every string cell is pinned to `data_type="s"` (which, unlike a leading apostrophe, doesn't alter the displayed text). The **frontend CSV export had the same latent bug** and was fixed in the same pass (`recordTable.ts`).
- **Caps are disclosed, never silent:** detail sheets are capped (default 100 — grouping real cases by `client_name` produced **4,796** groups) while the Summary sheet still lists every group; sheet names are sanitized/truncated to Excel's 31 chars and de-collided, with the mapping written into the Summary sheet.
- **Not yet built (Phase 2):** real sandboxed Python (pandas/openpyxl) for arbitrary derived columns/calculations the spec vocabulary can't express, intended behind `MYCASE_AGENT_CODE_EXEC_ENABLED=false`. Note before building it: that materially changes the prompt-injection surface, since untrusted case-note text would then be able to influence *generated code* rather than just a validated spec.

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
- **`aggregate_cases`' empty-string custom_field_filters value used to mean the opposite of "blank."** `want not in actual.lower()` is trivially true for `want=""`, so a filter meant to find blank/unassigned fields (e.g. "cases with no Processing Agent") matched every NON-blank row instead — confirmed live: a 1,425-row "no Processing Agent" report where every row had a real agent name. Fixed: an empty filter value now explicitly means "field is blank."
- **Computed (non-custom-field) columns weren't filterable/groupable at all.** `assigned_attorney`/"Lead Attorney" is derived from `staff[].lead_lawyer`, not a real MyCase field or custom field — `group_by="assigned_attorney"` used to error, and the agent's silent fallback to an unfiltered `aggregate_cases()` call plus a fabricated reply count was a second, compounding bug. Now a recognized pseudo-field, filterable/groupable like any real one.
- **No way to filter on a duration between a case's own two date fields.** "Cases closed within 1 month of opening" has no correct expression using only absolute date-range params (`opened_after`/`closed_before` etc. are independent floors/ceilings, not "these two fields on the SAME case were close together") — added `days_to_close_min`/`days_to_close_max` (each surviving row gets a real computed `days_to_close`).
- **Processing Agent duplicate spellings** ("Angelo" vs "Angelo Bazin") are canonicalized via a curated, admin-extensible `_AGENT_ALIAS_MAP` in `mycase_rest.py` (deliberately NOT fuzzy-matched — risks merging two different real people) — `PROCESSING AGENT` is the cleaned/merged value, `PROCESSING AGENT (original)` keeps the untouched raw value.
- **A reply's headline count can silently disagree with its own table.** `_mismatched_found_count` in `mycase_agent.py` compares the reply's "Found N X" claim against the actual rows fetched that turn — mid-turn it forces a retry; on the final closing turn (no retries left) it deterministically rewrites just the number rather than shipping two contradicting counts to the user. General-purpose — catches this class of bug for any resource, not just the ones that exposed it.
- **Scope creep — the agent used to happily answer general-knowledge/off-topic questions** ("who is Elon Musk", "explain agentic AI") using its own training knowledge instead of refusing. Both agents' system prompts now open with an explicit SCOPE rule refusing anything not about the firm's own data, plus a DATA VS. INSTRUCTIONS rule telling the model that text inside tool results (case notes, item comments) is never a command to follow, even if phrased like one.

**Known gaps:** no write operations implemented; a few requested report fields ("Days in Current Stage," "Case Owner") don't exist anywhere in MyCase's API and were left out rather than faked. Staff role/permission data ("who can access case management") does not exist anywhere in MyCase's API either — confirmed live (staff records only expose name/email/title/type/default_hourly_rate/active) — the agent says so rather than guessing. `/clients` has been observed to return HTTP 504 ("Endpoint request timed out") on this account even at `page_size=1`, reproduced twice, and retrying (added for 502/503/504 generally) does not fix it — `aggregate_clients`/`find_duplicate_clients` inherit this risk; the agent is prompted to report the real error rather than claim zero contacts.

---

## Observability (Langfuse)

Both agents are traced end-to-end via [Langfuse Cloud](https://cloud.langfuse.com) (`app/services/tracing.py` + `langfuse` SDK v4, OTel-based). `model_gateway.chat()` is the single choke point every provider/both agents funnel through — wrapping just that one method there gives full LLM-call tracing (model, provider, input/output, errors) for free, no per-provider work needed. Each agent turn is its own trace (`@observe(as_type="agent")` on `run_mycase_agent`/`run_podio_agent`); each tool call is a nested "tool" span. `session_id` (the frontend's existing chat-session UUID, already used for GET/PUT `/agent/*/sessions/{id}`) is threaded through so multi-turn conversations group under one Langfuse session instead of showing as unrelated traces; team/role are tagged via `propagate_attributes` at the router layer.

**Scores:** every deterministic guard in `mycase_agent.py` (`_unverified_resource_claims`, `_mismatched_found_count`, `_reply_falsely_denies_invoices`, etc.) and `podio_agent.py`'s bad-reply guard now also emits a `reply_quality` score (`pass`/`fail`/`corrected`/`pending_confirmation`) — queryable in the Langfuse dashboard instead of grepping structlog output. An optional second-opinion LLM judge (off by default, `MYCASE_AGENT_LLM_JUDGE_ENABLED=true` to enable) reviews each MyCase reply against the real fetched data and logs an `llm_judge` score — a genuinely separate LLM call, so it's opt-in, not default.

**Regression datasets:** `scripts/seed_langfuse_regression_dataset.py` freezes real prompts that once exposed a bug (the Processing Agent/Lead Attorney/duration filter bugs above, the off-topic-scope bugs) into two Langfuse datasets (`mycase-agent-regressions`, `podio-agent-regressions`) — re-runnable via the Langfuse UI's "Run experiment" after any system-prompt or `aggregate_cases` change, to catch a regression before a user does. Extend it (don't just re-run it) whenever a new real bug gets fixed.

**Gotchas (both cost real debugging time to find):**
- **A blank-but-PRESENT `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` in `.env` is NOT the same as absent.** `python-dotenv` sets it to `""`, and the SDK's `is None` check doesn't catch that — it still attempts a real network call and logs a 401 on every span. Set `LANGFUSE_TRACING_ENABLED=false` explicitly to get a true no-op while keys are blank/being set up.
- **Langfuse Cloud is region-sharded** — EU (`https://cloud.langfuse.com`, the default), US (`https://us.cloud.langfuse.com`), Japan (`https://jp.cloud.langfuse.com`). A key from the wrong region 401s with a generic "Invalid credentials" error that looks identical to a genuinely wrong/revoked key — check `LANGFUSE_BASE_URL` matches the project's actual region in the Langfuse dashboard before assuming the key itself is bad.
- **The Langfuse client is a singleton per process, read from `.env` once.** `uvicorn --reload` doesn't watch `.env`, only `.py` files — a key/host change needs a full backend restart, not just any file save.

## Key Patterns

1. New adapters/REST methods: `httpx.AsyncClient` with `async with`, timeout 30–120s.
2. New MCP tools: `@mcp.tool(name="prefix.action")` inside `register_*_tools(mcp)`, registered in `main.py`.
3. Tool name prefixes must match `TOOL_PERMISSIONS` in `rbac.py` or they're unrestricted.
4. No business logic in adapters — thin HTTP wrappers only.
5. Adapters raise exceptions; tools catch and return `{"success": False, "error": ...}`.
6. Logging: `structlog.get_logger(__name__)` → `logger.info("event_name", key=val)`.
7. Settings UI: integrations always-rendered; LLM providers opt-in, persisted in `localStorage` under `syndrix_added_llms`.
8. Anything that scans/aggregates more than a handful of records: compute it server-side (Python), never leave it to the LLM — this is the single most repeated lesson across the MyCase Agent's whole history above. **Nuance added in Session 48:** this is about the LLM *computing*, not the LLM *deciding presentation*. Letting it emit a validated declarative spec that deterministic Python then executes (see `build_report`) is a different, safe thing — and it's what finally stopped every new output shape from needing its own hardcoded tool.
9. Never write a real credential/settings DB row during testing (even "set then reset") — mock `get_setting`/`upsert_setting` instead. This has broken a real saved MyCase token twice.
10. Both agents skip the LLM entirely (zero cost, sub-second) for a provably-trivial opening greeting on a brand-new conversation (`_opening_greeting_reply` — no history yet, closed greeting set only). Deliberately NOT extended to fuzzy "simple-looking" query routing or model-tier downgrading — a misclassified real question silently answered by a weaker model is a worse failure mode than the cost/latency it would save.
11. `memory.py`'s `MemoryService.store_conversation()` now also logs every guard-caught-and-corrected MyCase reply (see `_remember_correction` in `mycase_agent.py`) — write-only for now (nothing is read back into a future prompt yet); a full retrieval-augmented version is a deliberately separate, unbuilt next step.

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
**37:** Normalized Processing Agent name variants (curated alias map) and, chasing the same request, found and fixed `aggregate_cases`' blank-value filter bug (an empty custom_field_filters value matched every non-blank row instead of blank ones — confirmed live on a 1,425-row "no Processing Agent" report where every row had a real agent).
**38:** Fixed two more real `aggregate_cases` gaps found via live "no Lead Attorney"/"closed within 1 month of opening" requests — computed fields (assigned_attorney) weren't filterable/groupable at all (silent fallback to an unfiltered call + a fabricated reply count), and there was no way to filter a duration between a case's own two date fields (`days_to_close_min/max` added). Added a general `_mismatched_found_count` guard so a reply's headline count can never silently disagree with its own table again, for any resource.
**39:** Implemented Langfuse Cloud observability for both agents (LLM/tool tracing, session grouping, guard results as `reply_quality` scores, regression datasets seeded from this session's fixed bugs). Root-caused two real connectivity dead-ends before it worked: a blank-but-present API key still attempts a network call and 401s (must set `LANGFUSE_TRACING_ENABLED=false` explicitly), and Langfuse Cloud is region-sharded (EU/US/Japan) — a key from the wrong region 401s identically to a genuinely bad key.
**40:** Restricted both agents to firm-data-only answers — they were previously happy to answer general-knowledge/off-topic questions (e.g. explaining "agentic AI") using training knowledge instead of refusing. Added a DATA VS. INSTRUCTIONS rule to both system prompts so text inside tool results (case notes, item comments) is never treated as a command, even if phrased like one.
**41:** Added a confirm-before-write UI for Podio's destructive actions (writes used to execute immediately on detected intent — this is a deliberate safety change), an optional off-by-default LLM-as-judge reply evaluator, a zero-cost opening-greeting routing shortcut for both agents, and a write-only long-term-memory log of guard-corrected replies.
**42:** First real hosted-deployment pass (Docker build to a VPS, no domain) surfaced a run of localhost-only assumptions baked in since local-only development. Fixed a frontend TypeScript build failure blocking the Docker image entirely (`SkeletonTable` missing its `cols` prop; four `unknown && <jsx/>` conditionals in `ContactResult`/`NoteResult` that TS can't narrow). Also audited `requirements.txt` against actual imports and added `cryptography` (used directly, not just via `python-jose`'s extra) and `pytest`/`pytest-asyncio` (tests existed but weren't installable from this file alone).
**43:** Fixed backend CORS hardcoded to `http://localhost:3000` — added a `CORS_ORIGINS` env var (`get_cors_origins()` in `config.py`) instead, since the deployed frontend's origin was silently rejected on every preflight (surfaced to users as a generic "Failed to fetch").
**44:** Fixed two independent breakages caused by the same root cause — `crypto.subtle`/`crypto.randomUUID()` require a secure context (HTTPS or `localhost`), which a plain-HTTP hosted-IP deployment isn't: (1) Settings API-key encryption (`frontend/lib/crypto.ts`) threw outright; fixed with a `@noble/ciphers`/`@noble/hashes` pure-JS fallback, verified byte-identical to the Web Crypto output. (2) Chat history silently never saved on either agent — `makeSessionId()`'s fallback produced a non-UUID id the backend rejected with a 400, swallowed by a silent `.catch()`; fixed by building a real UUID v4 from `crypto.getRandomValues()` instead (no secure-context restriction), and the two previously-silent catches now at least `console.error` so a future failure isn't invisible again.
**45:** Overhauled Podio's OAuth redirect handling — `podio_mcp_redirect_uri`/`podio_rest_redirect_uri`/`podio_mcp_frontend_redirect` were hardcoded to `localhost` with no Settings UI field and no whitelisted setting key, so there was no way to fix a hosted deployment short of a raw DB write. Replaced with `request_origin.py`, deriving all three from the incoming request (Host/X-Forwarded-Host, Origin/Referer) so Connect works unmodified on any host; the old keys remain as optional overrides only. Also simplified Settings → Podio down to one Client ID/Secret pair (REST already fell back to the MCP credentials when unset — the separate REST fields just added confusion) after confirming live that Podio's redirect_uri validation is domain-level, not exact-path.
**46:** Diagnosed a hosted-only MyCase Agent "Failed to fetch" via live DevTools network capture — not a backend bug: `aggregate_cases()` with no `limit` legitimately returns every matching case in full (2.5MB for one real "list all open cases" request), and a mobile carrier's transparent proxy (visible as a `Remote Address` completely different from the server's own IP) 504'd the first attempt before the backend finished; the identical request succeeded on retry. Documented as a Hosted Deployment Note rather than changed, pending a decision on whether broad unbounded queries should ever be capped by default (tension with the project's long-standing "never silently truncate" principle).
**47:** Fixed 16 real user-reported MyCase Agent bugs (grouping, missing native fields, missing joins) — root-caused each via read-only probes against the actual connected account (a 7,200+ case immigration firm) instead of guessing, which surfaced facts nobody knew: `sol_date` and `created_at` are real NATIVE case fields (not custom fields) that `aggregate_cases` never exposed; leads have a real `status` field with the literal firm value "NEED FOLLOW-UP"; `get_invoice_payments()` unfiltered already returns 9,314 real records (~$8.16M) with `attorney`/`client`/`case` directly attached, just with no deterministic aggregation tool; `/clients` reliably 504s on this account even at `page_size=1` (confirmed twice), and retrying (added 502/503/504 to `_request`'s retry set generally) does not fix it. Added `sol_date_after/before`, `created_after/before`, and `client_name` as a `group_by` target to `aggregate_cases`, plus a sorted `groups: [{name,count}]` summary (fixes "which stage has the most cases" — the model no longer scans a flat, truncatable row list to find the max). Added three new deterministic tools mirroring `aggregate_cases`' pattern: `aggregate_leads` (status/date/attorney-via-linked-case filtering+grouping), `aggregate_payments` + `get_case_payments` (firm-wide/per-case payment aggregation — attorney is directly on each payment, no join needed), and `group_by` support on `aggregate_invoices` (client_name/assigned_attorney, resolved via the invoice's linked case). Added `aggregate_clients`/`find_duplicate_clients`, flagged as at-risk given the `/clients` 504. Broadened the fake-tool-call leak regex to also catch a bare `tool_name(args)` call with no JSON object. Updated the system prompt to render a `groups`-based breakdown for any grouped request, and to say plainly that MyCase's API exposes no staff role/permission data at all (confirmed live) rather than guessing. Every new/changed REST-layer method was verified directly against the live account (correct real data every time — 9,314 real payments totaling ~$8.16M, real attorney names with real balances, etc.). A real end-to-end `/agent/mycase` chat replay of "which case stage has the highest number of cases" (with the configured `mistral:mistral-large-2512` model) surfaced ONE MORE real bug the direct tool tests couldn't catch: the model omitted `group_by` entirely, it silently defaulted to `practice_area`, and the reply confidently answered "Immigration: 4,002 cases" — a practice-area breakdown, not a case-stage one. Fixed by making both the tool description and the system prompt explicitly map the user's wording to the exact `group_by` value ("stage"→case_stage, "attorney"→assigned_attorney, "client"→client_name) instead of assuming the model would infer it; added as its own regression-dataset item. Separately, this and a baseline "how many total cases" query both took several minutes end-to-end on this account/model combo (while the zero-cost greeting shortcut still returned instantly, confirming the backend itself is healthy) — worth a closer look if response times are a recurring complaint, since some of it may be full unfiltered `items` arrays (no `limit` passed) getting serialized into the response rather than pure LLM latency.
**48:** Structural answer to the (correct) complaint that Session 47's fixes were all point fixes that wouldn't generalize — the real cause was that the chat could only ever render ONE flat table per resource, so "active cases per attorney, each attorney on its own sheet, plus a summary" had no code path at all and every new output shape needed another hardcoded tool. Added LLM-directed Excel reporting: the model emits a declarative spec (`summary` group_by/metrics + `detail_sheets` split_by/columns/sort) and `report_builder.py` renders a real multi-sheet .xlsx. This is the SAFE, declarative form of the code-execution agent pattern — no arbitrary code runs, deliberately, because case notes are untrusted input; sandboxed Python is scoped as a later Phase 2. `build_report` is the one tool NOT on the MCP server (it reads rows the turn already fetched, which only the agent loop holds) and it refuses a `limit`ed source rather than shipping a silently partial workbook. Confirmed live on the user's own request: `aggregate_cases(status=open, group_by=assigned_attorney)` → 9 sheets / 2,380 rows with correct per-attorney counts, workbook verified by opening it. **The response payload went from 9.8 MB to 2.7 KB (~3,600×)** because rows that went into a workbook are stripped from `steps` — which also stops them being persisted verbatim into the chat-session DB row, and directly addresses the slow-response/504 problems noted in 46/47. Three real bugs surfaced along the way: (1) the first generated report 404'd on download because **Redis is silently dead on this box** (Redis 5.0.14 vs redis-py 8's RESP3 `HELLO` handshake — documented under Running Locally; it degrades memory/cache/audit too, invisibly) and the in-process fallback died on `uvicorn --reload`, so the fallback is now temp files on disk; (2) the frontend CSV export had an unpatched spreadsheet-formula-injection hole (same class the new xlsx writer defends against, fixed in the same pass); (3) `aggregate_cases` labelled the same bucket "(none)" in `groups` but "(unassigned)" on the rows, making a report's counts look like they disagreed with the summary above them.
**49:** Added the missing HAVING primitive (`min_group_size`/`max_group_size` on all five `aggregate_*` tools) after "show me clients who have more than one case" returned 2,380 ungrouped rows. Filtering on a GROUP'S SIZE had no expression in the tool layer at all, so the model could only group everything and try to eyeball the big groups. Reproducing it live showed the failure is also **unstable**: the same question went to `aggregate_clients(group_by="id")` on one run and `aggregate_cases` on another — so both a new primitive AND routing guidance were needed. Grouping clients by a unique id can never yield a group >1, so that call is now rejected *before* the slow /clients walk with an error naming the correct call. Also confirmed a genuine data-integrity fact: the case walk is complete (7,247/7,247, untruncated) but **3,222 case ids referenced by invoices return a real 404** — invoices outliving deleted cases — which had been surfacing as fake client names (a reply once opened "Top groups: (no case) ($77,091), (unknown case) ($46,110)"). Relabelled to `(case deleted in MyCase)`, disclosed via `unresolved_client_invoices`/`unresolved_note`, and the prompt now forbids listing placeholder buckets as names while still requiring their amounts be reported. Then ran a **7-query self-authored live battery** (`scratchpad/battery.py`) — questions the user never asked, one per distinct flow — which is where the rest of this session's bugs came from. It confirmed the HAVING primitive works end-to-end (`max_group_size=1` → 9 of 24 practice areas; `min_group_size=50` → 3 of 8 attorneys, correctly flagging "(unassigned)" as *not* a person), that the Excel path still works (9 sheets / 2,380 rows / 2.9 KB), that a count-only question answers correctly, and that "what permissions does each staff member have" is properly refused. It also exposed four things nothing else would have:
- **A reply that is nothing but a tool NAME shipped to the user** — asked for clients with >1 case, the model replied with the single word `aggregate_clients` and made no tool call. No guard fired: `_has_text_tool_call` needs a `(`/`{`, and `_is_garbage_reply` judges noise ratios, so a lone identifier reads as clean prose. Added `_is_bare_tool_name`.
- **A garbled text tool call escaped every regex**: `aggregate_invoicesმწ{"group_by": ...}` — two stray Georgian characters between the name and the `{` beat both patterns, and 2 non-ASCII chars in 62 stayed under the garbage threshold. `_has_text_tool_call` now also looks for a REAL tool name sitting within 8 chars of a `{`/`(`, whatever junk is wedged between.
- **The `aggregate_cases` limit gate misfired on threshold questions.** "Which attorneys have at least **50** open cases?" made `_requested_case_count` return 50, so the gate rejected the correct call and forced `limit=50` — capping a group-count answer to 50 rows and making the reply say "first 50 shown". Numbers preceded by *at least / more than / over / …* are now read as thresholds, and the gate stands down entirely when `min_group_size`/`max_group_size` is present.
- **`/agent/mycase/status` reported `connected:true, expired:false` while every call 401'd** — a pasted token has no stored expiry, so the badge could never detect a dead one. It now makes one cheap 60s-cached real call and returns `token_valid`/`token_error`; a network blip reports unknown rather than falsely "dead".

- **The same read-only call was issued four times in one turn** — four full ~7,200-case walks (317 s) whose 3,098 rows were then serialised into the response four times over, for a **13.5 MB payload**. Identical `(tool, args)` pairs are now served from a per-turn cache: the model still gets the real result, the browser gets a small `repeat_of_earlier_identical_call` marker instead of another full copy.
- **A `group_by` call whose reply never showed the grouping.** "Show me unpaid invoices grouped by client" answered *"Found 1,889 unpaid invoices — see the table below"* — but that table is one flat invoice list, so the requested grouping appeared nowhere. The prompt now states that using `group_by` obliges the reply to contain the breakdown, and that the KEEP-REPLIES-SHORT rule does not override it (that rule is about not retyping RECORDS, which the table already shows; a group breakdown is not in the table). Verified fixed: the reply now lists each client with invoice count and balance, largest first.

**53:** Fixed an unbounded retry loop in `mycase_rest._request` that DevOps spotted in the hosted logs (repeated `/clients attempt 1/2/3`). One call could run ~127s (measured 135.2s) with no overall ceiling, which blows past a reverse proxy read timeout and returns a 504 to the browser with no response at all. Added a 45s total budget, shrinking per-attempt timeouts, and one retry for 504s instead of three: 135.2s -> 46.9s, healthy calls unchanged. Read timeouts now say so instead of claiming a connection failure. NOTE this is a DIFFERENT failure from the missing-history-tables report — it can kill a whole turn (no response, so nothing saved), whereas the reported symptom was text saved WITHOUT tables. 130 tests pass.

**52:** "Show Criminal cases with assigned attorneys" returned the UNASSIGNED ones, twice, with two different totals (13 and 49). Root causes, none of them about that question: (a) `custom_field_filters` could only express "field is blank" — there was no way to say "field has a value", so the model used the only attorney idiom it knew and inverted the query; `"*"` now means "has any value". (b) A filtered count was narrated as the population ("All 49 Criminal cases…" when 237 exist and 188 have an attorney) because the model never saw a denominator; field-filtered results now carry `total_ignoring_field_filters`. (c) The differing totals were the model silently varying its own filters between runs, which is why the filters applied are now surfaced and quoted. Verified live: the same question run twice now returns **188 of 237** both times, and the negative phrasing correctly returns the complementary 49. Self-testing then caught a further hallucination — a failed `group_by` left a practice-area-grouped result that the reply relabelled as an attorney breakdown, inventing the name "JESSICA PRIVITERA" — now rejected by `_fabricated_group_names`. 121 tests pass.

**51:** Four more reported failures, fixed at the level of the shared cause rather than per-question. (1) **Unknown tool arguments were silently dropped** — the worst bug of the session, because the call still runs unfiltered and the reply sounds right (`aggregate_cases(spec=…)` returned all 7,247 cases described as "open cases with unpaid balances"); now rejected with a corrective error. (2) **The closing/fallback reply path skipped the structural guards**, so a leaked tool call shipped as the final answer whenever the loop ran out of steps — every guard now runs there too, and the fallback text describes the DATA rather than listing internal tool names ("Completed: aggregate_invoices, build_report, get_events."). (3) **Auto-report triggering moved from row count to user intent + group count** — row count was wrong in both directions, skipping a wanted 83-row per-attorney breakdown while generating an unwanted 101-sheet/1,624-group workbook for a grouping that was only an intermediate step. The intent regex also has to allow an adjective ("by **assigned** attorney"). (4) **Metadata rendered as data**: `describe_entity_fields` dumped its whole schema blob into the chat, and steps whose rows had moved into a workbook rendered their leftover metadata (including the entire `groups` array) as a bogus one-row table — both fixed in the reference-tool sets and `stepItems`. Also: the auto-built workbook is now announced to the model (it was building a second one and showing two Download buttons), and `_retarget_table_pointers` ignores reference tools when deciding whether a table is actually on screen. Two further guards came out of re-testing, both replacing prompt rules that only held *sometimes*: **contradictory filters are rejected** (`custom_field_filters={"sol_date": ""}` — field is EMPTY — combined with `sol_date_after/before` — field is in a RANGE — can never match, and the resulting 0 rows was reported as the fact "there are no open cases with an SOL date in the next 30 days" when the real answer was 93); and **`_ensure_group_breakdown` appends the real per-group numbers** when a grouped reply omits them, because the same question listed every attorney on one run and said only "see the breakdown above" — pointing at nothing — on the next. Also fixed while doing this: queuing the auto-report notice mid-tool-loop produced a Mistral 400 (`Unexpected role 'tool' after role 'user'`) — a provider will not accept a user message wedged between an assistant tool_call and its tool response, so notices are flushed after the loop. Documented as still-open: "clients with an overdue invoice but no upcoming appointment" remains unanswerable because `/clients` 504s, and cross-resource set questions now have prompt guidance but no deterministic tool. 112 tests pass.

**50:** Acted on an external review of the agent's failures. Its two best calls were right and are now built: grouped results auto-become per-group workbooks (`_auto_report_spec` — "show invoices by assigned attorney" went from a correct summary above 1,888 flat rows to 9 sheets at 3.4 KB), and `describe_entity_fields` gives live field discovery (verified: the agent now looks up `sol_date` before answering, instead of failing). Its other diagnoses were checked against the running system and did NOT hold, which is worth recording so they aren't re-attempted: filters *are* wired to real API params — MyCase simply has almost none (the only date filter in the whole API is `updated_after`, which is why everything is computed in Python); the tool loop *does* execute calls and feed results back — the leaked-tool-call symptom is the model emitting call-shaped text, not an orchestration bug; and the `/clients` 504 is not an unpaginated bulk pull (it fails at `page_size=1`, and three backoff retries over 135 s don't help). Sandboxed code execution was also declined for aggregation specifically: the `aggregate_*` tools already group/count/sum exactly, and case notes are attacker-controllable text, so model-written code is a materially larger attack surface than a validated spec — it stays scoped to Phase 2 for genuinely novel calculations. 93 tests pass.

83 tests pass. **Still not right, and worth picking up next:** "show me clients who have more than one case" now computes correctly (648 of 4,797 client groups) and the wrong-tool guard fires as intended, but the model then wanders off doing per-client `get_clients` lookups and answers about three clients instead of presenting the 648 groups. The data layer is right; the presentation for that specific question is not. Two contributing factors worth considering: the dominant group is `(none)` with 1,684 clientless cases (a placeholder that is not a client at all, arguably shouldn't be in a "which clients…" answer, but must not be silently dropped either), and a count-only question over leads still ships every row (620 KB for "how many leads need follow-up") because payload slimming only triggers when a workbook was built.
