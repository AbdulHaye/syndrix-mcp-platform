# Syndrix — Project Handbook

> **Rule:** Every Claude session MUST read this file first. Do not rely on terminal history or prior conversation context. After completing work, append a summary to the [Session Log](#session-log) section at the bottom.

---

## What This Project Is

One internal MCP server acting as a **shared AI capability hub** for three teams:

| Team | Role key | Typical tools |
|------|----------|---------------|
| BD (Business Development) | `bd` | CRM contacts, leads, notes, email |
| Software Dev | `dev` | Repo search, ticket creation, spec gen, bug triage |
| Management | `mgmt` | Daily reports, client health scores |

**Key design rule:** The MCP server is a capability backend, NOT an agent. Business rules stay above the tool layer. Adapters go in `app/adapters/`, tools go in `app/mcp_tools/`.

---

## Stack

| Layer | Technology |
|-------|-----------|
| API framework | FastAPI + FastMCP (MCP Python SDK) |
| Language | Python 3.11 |
| Database | PostgreSQL + pgvector (via asyncpg + SQLAlchemy async) |
| Cache / broker | Redis |
| Local LLM | Ollama (`llama3.2` default, `nomic-embed-text` embeddings) |
| Background jobs | Celery (broker = Redis) |
| Runtime | Local (uvicorn + native Postgres/Redis/Ollama installs) |
| Migrations | Alembic |
| Logging | structlog (JSON in prod, console in dev) |

---

## File Layout

```
syndrix/
├── CLAUDE.md                        ← YOU ARE HERE — read first every session
├── .env                             ← credentials / env vars (never commit secrets)
├── .env.example
├── requirements.txt
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── script.py.mako
├── app/
│   ├── config.py                    ← Settings (pydantic-settings), get_settings()
│   ├── main.py                      ← FastAPI app + FastMCP mount + lifespan
│   ├── auth/
│   │   ├── bearer.py                ← Bearer token auth, TeamIdentity, TeamRole
│   │   └── rbac.py                  ← TOOL_PERMISSIONS map + can_use_tool()
│   ├── api/
│   │   ├── health.py                ← GET /health
│   │   ├── admin.py                 ← Admin endpoints
│   │   └── ingest.py                ← POST /ingest (RAG document ingestion)
│   ├── adapters/
│   │   ├── base.py                  ← BaseAdapter ABC
│   │   ├── podio.py                 ← Podio CRM (OAuth client-credentials) — singleton: podio_adapter
│   │   ├── ghl.py                   ← GoHighLevel CRM v1 (API key) — singleton: ghl_adapter
│   │   ├── slack.py                 ← Slack Web API (bot token) — singleton: slack_adapter
│   │   ├── github.py                ← GitHub REST (PAT) — singleton: github_adapter
│   │   └── email.py                 ← Email/SMTP (aiosmtplib) — singleton: email_adapter
│   ├── mcp_tools/
│   │   ├── health_tools.py          ← health.ping, health.status
│   │   ├── crm_tools.py             ← crm.contact.get, crm.note.create, crm.lead.search, crm.message.send, crm.pipeline.stages, crm.email.send
│   │   ├── dev_tools.py             ← repo.search, ticket.create, ticket.pr_list, spec.generate, bug.triage, slack.message.send, slack.channel.history
│   │   ├── mgmt_tools.py            ← report.team.daily, client.health.score (mocks — Phase 3)
│   │   └── rag_tools.py             ← rag.search, rag.ingest
│   ├── registries/
│   │   ├── tool_registry.py
│   │   ├── resource_registry.py
│   │   └── prompt_registry.py
│   ├── services/
│   │   ├── audit.py                 ← AuditService (logs to Redis + DB)
│   │   ├── memory.py                ← MemoryService (short-term Redis + pgvector long-term)
│   │   ├── model_gateway.py         ← Ollama gateway: generate(), embed(), summarize()
│   │   ├── permissions.py           ← Permission helpers
│   │   └── rag.py                   ← RAGService: ingest() chunks+embeds docs; search() semantic query
│   ├── storage/
│   │   ├── db.py                    ← Async SQLAlchemy engine + session
│   │   ├── models.py                ← AuditLog, KnowledgeDocument, TeamToken ORM models
│   │   └── vector.py                ← VectorStore: store() + search() with pgvector
│   └── workers/
│       └── jobs.py                  ← Celery tasks: sync_crm_contacts, process_document, rebuild_vector_index
└── tests/
    ├── test_auth.py
    └── test_health.py
```

---

## Auth Model

- Bearer tokens in `DEV_TOKENS` env var, format: `team_name:token,team_name:token,...`
- `app/auth/bearer.py` → `verify_token(token)` returns `TeamIdentity(team_name, role, token)`
- Roles: `bd`, `dev`, `mgmt`, `admin` (enum: `TeamRole`)
- `app/auth/rbac.py` → `TOOL_PERMISSIONS` maps tool name prefixes to allowed roles:
  - `crm.*` → BD, ADMIN
  - `repo.*`, `ticket.*`, `spec.*`, `bug.*` → DEV, ADMIN
  - `report.*`, `client.*` → MGMT, ADMIN
  - `health.*` → all roles

---

## How Tools Are Registered

```python
# app/main.py
mcp_server = FastMCP("syndrix")
register_crm_tools(mcp_server)   # app/mcp_tools/crm_tools.py
register_dev_tools(mcp_server)   # app/mcp_tools/dev_tools.py
register_mgmt_tools(mcp_server)  # app/mcp_tools/mgmt_tools.py
# MCP server mounted at /mcp
```

Each `register_*_tools(mcp)` function uses `@mcp.tool(name=..., description=...)` decorators.

---

## Services

### ModelGateway (`app/services/model_gateway.py`)
Singleton `model_gateway`. Methods:
- `generate(prompt, model, system)` → `str`
- `embed(text, model)` → `list[float]`
- `extract_structured(prompt, schema, model)` → `dict`
- `summarize(text, max_words)` → `str`
- `route_model(task_type)` → model name string

Task→model routing: `chat/summary/extract/analysis` → `llama3.2`, `code` → `codellama`, `embed` → `nomic-embed-text`

### MemoryService (`app/services/memory.py`)
Singleton `memory_service`. Methods:
- `store_conversation(team, summary, metadata)` — embeds + stores in pgvector + Redis list
- `retrieve_similar(query, limit)` — semantic search via pgvector
- `get_recent(team, limit)` — last N items from Redis
- Redis injected at startup via `set_redis(client)`

### VectorStore (`app/storage/vector.py`)
Singleton `vector_store`. Methods:
- `store(title, source, content, embedding)` → `KnowledgeDocument`
- `search(query_embedding, limit)` → `list[dict]` (falls back to recency if pgvector unavailable)

---

## Database Models (`app/storage/models.py`)

| Table | Purpose |
|-------|---------|
| `audit_logs` | Tool invocation audit trail |
| `knowledge_documents` | RAG document chunks + pgvector embeddings |
| `team_tokens` | Token storage (supplement to env-based tokens) |

---

## Celery Jobs (`app/workers/jobs.py`)

| Task name | What it will do |
|-----------|-----------------|
| `jobs.sync_crm_contacts` | Sync Podio contacts → PostgreSQL + vector index |
| `jobs.process_document` | Embed a document → store in pgvector |
| `jobs.rebuild_vector_index` | Rebuild IVFFlat index on `knowledge_documents` |

`process_document` is fully wired. `sync_crm_contacts` needs Podio adapter wired (Phase 2).

---

## Environment Variables (`.env`)

```
APP_ENV=development
SECRET_KEY=...
DATABASE_URL=postgresql+asyncpg://mcp_user:mcp_pass@localhost:5432/syndrix
REDIS_URL=redis://localhost:6379/0
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=llama3.2
OLLAMA_EMBED_MODEL=nomic-embed-text
DEV_TOKENS=bd_team:bd-secret-token-123,dev_team:dev-secret-token-456,mgmt_team:mgmt-secret-token-789

# Phase 2 credentials (fill in before wiring each adapter)
PODIO_CLIENT_ID=
PODIO_CLIENT_SECRET=
GHL_API_KEY=
GHL_LOCATION_ID=
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=
GITHUB_TOKEN=
GITHUB_ORG=
SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
```

---

## Phase Roadmap

### Phase 1 — COMPLETE (2026-04-23)
Full skeleton scaffolded. All infrastructure wired (DB, Redis, Ollama, Celery). All adapters exist as stubs returning `{"status": "not_implemented", ...}`. All MCP tools registered but return mock/placeholder data.

### Phase 2 — COMPLETE (2026-04-29)
**Goal:** Replace every `not_implemented` stub with real HTTP calls. Add RAG document ingestion pipeline.

Tasks:
- [x] **Podio adapter** — real OAuth client-credentials flow + `get_contact`, `create_note`, `search_leads`
- [x] **GoHighLevel adapter** — real API key auth (v1) + `get_contact`, `create_note`, `search_leads`, `send_message`, `get_pipeline_stages`
- [x] **Slack adapter** — real bot token auth + `send_message`, `get_channel_history`, `get_contact` (user lookup)
- [x] **GitHub adapter** — real PAT auth + `get_contact` (user), `create_issue`, `get_pr_list`, `search_repos`
- [x] **Email adapter** — aiosmtplib SMTP + `send_email`, `draft_email`
- [x] **Wire crm_tools.py** — real Podio + GHL calls; new tools: `crm.message.send`, `crm.pipeline.stages`, `crm.email.send`
- [x] **Wire dev_tools.py** — real GitHub calls; new tools: `ticket.pr_list`, `slack.message.send`, `slack.channel.history`
- [x] **RAG service** — `app/services/rag.py` — word-based chunking + embed + pgvector store
- [x] **RAG ingestion endpoint** — `POST /ingest` (auth required, all roles)
- [x] **RAG MCP tools** — `rag.search`, `rag.ingest` (all roles)
- [x] **Wire sync_crm_contacts Celery task** — fetches from Podio, embeds + stores in pgvector
- [x] **RBAC** — added `slack.*` (BD+DEV) and `rag.*` (all roles) to `TOOL_PERMISSIONS`
- [x] **Config** — added `PODIO_APP_ID` setting + `.env` entry
- [x] **requirements.txt** — added `aiosmtplib>=3.0.0`

---

## UI — Next.js Frontend (`frontend/`)

**Stack:** Next.js 14 (App Router), Bootstrap 5, TypeScript, bootstrap-icons

**Run:** `cd frontend && npm install && npm run dev` → http://localhost:3000

**Key files:**
```
frontend/
├── app/
│   ├── layout.tsx              ← Bootstrap CSS + global styles imported here
│   ├── page.tsx                ← Redirects to /login or /dashboard (cookie check)
│   ├── login/page.tsx          ← Token-based login (quick-select dev presets)
│   └── dashboard/
│       ├── layout.tsx          ← Sidebar + AuthGuard wrapper
│       ├── page.tsx            ← Overview with stats + tool grid
│       ├── health/page.tsx     ← Live health check (calls /health/detailed)
│       ├── crm/page.tsx        ← BD: contact lookup, lead search, note, message, email
│       ├── dev/page.tsx        ← Dev: repo, tickets, PRs, spec gen, bug triage, Slack
│       ├── mgmt/page.tsx       ← Mgmt: daily report, client health score
│       ├── rag/page.tsx        ← RAG: semantic search + document ingest
│       └── admin/page.tsx      ← Admin: tool registry, audit log, metrics
├── components/
│   ├── Sidebar.tsx             ← Fixed dark sidebar, role-filtered nav
│   ├── Topbar.tsx              ← Page header with live backend status dot
│   ├── ToolCard.tsx            ← Clickable card for each tool
│   ├── EmptyState.tsx          ← Empty state component
│   └── AuthGuard.tsx           ← Redirects to /login if no token in localStorage
├── lib/
│   ├── api.ts                  ← All API calls to FastAPI backend
│   └── auth.ts                 ← localStorage + cookie token management
└── types/index.ts              ← Shared TypeScript types
```

**Auth flow:** Token stored in `localStorage` (key: `mcp_auth`) AND cookie `mcp_token`. `page.tsx` reads cookie server-side to redirect. `AuthGuard.tsx` checks localStorage client-side.

**Tool invocation:** UI calls `POST /tools/invoke` on the FastAPI backend with `{tool, args}`. Backend looks up the tool in FastMCP registry and calls it directly. RBAC is enforced server-side.

**Backend endpoint added for UI:** `app/api/tools.py` → `POST /tools/invoke` (auth required, RBAC enforced).

### UI Phase 1 — COMPLETE (2026-04-29)
Full scaffold done. All pages, components, and API client in place. Health page live. Tool pages wired to `/tools/invoke`. Ready for Phase 2 of UI (real data display, charts, notifications).

### UI Phase 2 — COMPLETE (2026-04-29)
- Rich result rendering — ContactResult, LeadTable, RepoResults, PRTable, TextResult, SlackResult, RAGResults, NoteResult, PipelineResult, ResultRenderer dispatcher
- Toast notifications — ToastProvider/useToast(), auto-dismiss, color-coded by type
- Dark/light mode toggle — Bootstrap 5.3 `data-bs-theme`, persisted to localStorage
- Loading skeletons — SkeletonCard, SkeletonTable, SkeletonText with pulse animation
- Drag-and-drop file upload for RAG ingest — FileUpload component, .txt/.md/.csv/.json

---

### Phase 3 — NOT STARTED
Team use cases: BD prompt pack, Software Dev prompt pack, Management tools, per-team permission enforcement.

### Phase 4 — NOT STARTED
Resilience: retry/dead-letter queues, caching, audit dashboard, per-team metrics.

---

## Key Patterns to Follow

1. **New adapter methods** use `httpx.AsyncClient` with `async with` context manager. Timeout 10–30s.
2. **New MCP tools** use `@mcp.tool(name="prefix.action", description="...")` inside a `register_*_tools(mcp)` function. Register in `main.py`.
3. **All tool name prefixes** must match an entry in `app/auth/rbac.py TOOL_PERMISSIONS` or they default to unrestricted.
4. **No business logic in adapters** — adapters are thin HTTP wrappers only.
5. **Errors** — adapters raise exceptions; tools catch them and return `{"success": False, "error": ...}`.
6. **Logging** — use `structlog.get_logger(__name__)` with `logger.info/warning/error(event_name, **kv)`.

---

## Running Locally

Postgres, Redis, and Ollama must be running natively before starting the backend.

```bash
# One-time DB setup (psql as superuser)
# CREATE USER mcp_user WITH PASSWORD 'mcp_pass';
# CREATE DATABASE syndrix OWNER mcp_user;
# \c syndrix  CREATE EXTENSION IF NOT EXISTS vector;

# One-time Ollama model pull
ollama pull llama3.2
ollama pull nomic-embed-text

# Install Python deps
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Start server (auto-reloads on file save)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Start Celery worker (separate terminal, optional)
celery -A app.workers.jobs worker --loglevel=info

# Start frontend (separate terminal)
cd frontend && npm install && npm run dev
```

---

## Session Log

### Session 1 — 2026-04-23
**What was done:** Phase 1 scaffolding. Created all 46 files: full project skeleton including FastAPI app, FastMCP server, auth/RBAC, all 5 adapter stubs, 4 MCP tool modules, audit/memory/model_gateway services, pgvector storage, Celery workers, Alembic migrations, docker-compose, Dockerfile, tests.

### Session 2 — 2026-04-29
**What was done:**
1. Created CLAUDE.md (this file) with full Phase 1 documentation.
2. Implemented Phase 2 in full:
   - All 5 adapters wired with real HTTP calls (Podio OAuth, GHL v1 API key, Slack bot token, GitHub PAT, Email aiosmtplib)
   - Each adapter has a module-level singleton and lazy `_ensure_connected()` pattern
   - `crm_tools.py` wired to Podio + GHL + Email; added `crm.message.send`, `crm.pipeline.stages`, `crm.email.send`
   - `dev_tools.py` wired to GitHub; added `ticket.pr_list`, `slack.message.send`, `slack.channel.history`
   - `app/services/rag.py` — new RAGService with word-based chunking + embed + pgvector storage
   - `app/mcp_tools/rag_tools.py` — `rag.search` and `rag.ingest` MCP tools
   - `app/api/ingest.py` — `POST /ingest` REST endpoint (all authenticated roles)
   - `app/workers/jobs.py` — `sync_crm_contacts` task now calls real Podio adapter
   - RBAC updated with `slack.*` and `rag.*` permissions
   - `PODIO_APP_ID` added to config + `.env`
   - `aiosmtplib` added to requirements.txt
3. **Credentials needed before any adapter will work:** Fill in the empty vars in `.env` — `PODIO_CLIENT_ID`, `PODIO_CLIENT_SECRET`, `PODIO_APP_ID`, `GHL_API_KEY`, `GHL_LOCATION_ID`, `SLACK_BOT_TOKEN`, `GITHUB_TOKEN`, `GITHUB_ORG`, `SMTP_*` fields.

### Session 3 — 2026-04-29
**What was done:** Built complete Next.js 14 + Bootstrap 5 UI in `frontend/` directory (Phase 1 of UI).
- Login page with team quick-select presets + manual token entry
- Dashboard layout: dark fixed sidebar (role-filtered nav), topbar with live backend status
- Pages: Overview, Health, CRM, Dev Tools, Management, Knowledge Base, Admin
- All tool pages have interactive input panels that call `POST /tools/invoke` on the FastAPI backend
- Health page does live `GET /health/detailed` and renders dependency status
- RAG page uses `POST /ingest` REST endpoint directly
- Admin page shows tool registry, audit log, metrics tabs
- Added `app/api/tools.py` — `POST /tools/invoke` endpoint that looks up FastMCP tool by name + calls it (RBAC enforced)
- `lib/api.ts` — typed API client for all backend calls
- `lib/auth.ts` — localStorage + cookie auth helpers
- **To start UI:** `cd frontend && npm install && npm run dev`

### Session 4 — 2026-04-29
**What was done:** Completed UI Phase 2 — rich rendering, toasts, dark mode, skeletons, drag-and-drop file upload.
- `frontend/lib/toast.tsx` — ToastProvider + useToast() hook with auto-dismiss reducer (max 5, 4.5s TTL)
- `frontend/lib/theme.ts` — useTheme() writes `data-bs-theme` to `<html>` for Bootstrap 5.3 native dark mode
- `frontend/components/ToastContainer.tsx` — fixed top-right stack, color-coded by type
- `frontend/components/Skeleton.tsx` — Skeleton, SkeletonCard, SkeletonTable, SkeletonText components
- `frontend/components/FileUpload.tsx` — drag-and-drop + click upload, .txt/.md/.csv/.json only
- `frontend/components/results/` — 9 rich result components: ContactResult, LeadTable, RepoResults, PRTable, TextResult, SlackResult, RAGResults, NoteResult, PipelineResult
- `frontend/components/results/ResultRenderer.tsx` — switch dispatcher mapping tool names to rich components
- `frontend/app/dashboard/crm/page.tsx` — rewritten: useToast + ResultRenderer + SkeletonCard/SkeletonTable
- `frontend/app/dashboard/dev/page.tsx` — rewritten: useToast + ResultRenderer + SkeletonCard/SkeletonTable
- `frontend/app/dashboard/rag/page.tsx` — rewritten: FileUpload auto-fills content, useToast, RAGResults renderer
- `frontend/app/dashboard/layout.tsx` — added ToastProvider + ToastContainer wrapping
- `frontend/components/Topbar.tsx` — dark mode toggle button (moon/sun icon) using useTheme()
- `frontend/app/globals.css` — skeleton animation, dark mode card fix, hover-shadow utility

**UI Phase 2 status:** COMPLETE. All tool pages render rich results. No more raw JSON fallback except for unknown/future tools.

### Session 5 — 2026-04-29
**What was done:** Bug fixes and Docker removal.
1. **Fixed `/tools/invoke` 404** — `app/main.py` `http_app(path="/")` raised uncaught `TypeError` on some `mcp` versions; added inner `try/except TypeError` to fall back to `http_app()` with no args. Server must be restarted (`uvicorn app.main:app --reload`) to pick up the tools router added in Session 3.
2. **Fixed tool lookup robustness** — `app/api/tools.py` now scans by `.name` attribute when the tool name is not a direct dict key, and uses `getattr(obj, "fn", obj)` to handle varying FastMCP internal structures.
3. **Fixed GitHub repo search** — `app/adapters/github.py` changed `org:{name}` to `user:{name}` so personal GitHub accounts (`GITHUB_ORG=AbdulHaye`) return results.
4. **Removed all Docker** — deleted `Dockerfile`, `.dockerignore`, `infra/docker/docker-compose.yml`, and the `infra/` directory. Project is now run fully locally.
5. **Updated docs** — `README.md` rewritten for local setup (Postgres, Redis, Ollama native installs); `CLAUDE.md` stack table and file layout updated; `.env` Docker comments removed; `.gitignore` Docker section removed.

### Session 6 — 2026-05-05
**What was done:** Backend bug fixes, RAG fixes, Alembic migration fixes, full frontend redesign, landing page, dark mode removal, and navigation improvements.

**Backend fixes:**
1. **Spec generator 404** — `app/services/model_gateway.py` changed `_TASK_MODEL_MAP["code"]` from `"codellama"` → `"llama3.2"`. Also raised timeouts (generate 30s→180s, embed 10s→60s), added `num_predict` token cap, simplified `spec.generate` and `bug.triage` prompts in `dev_tools.py`.
2. **Alembic `InterpolationMissingOptionError`** — `%(DATABASE_URL)s` in `alembic.ini` was treated as ConfigParser interpolation. Fixed by hardcoding the URL in `alembic.ini`, adding `load_dotenv()` in `alembic/env.py`, and switching to `create_async_engine(url, poolclass=NullPool)` directly.
3. **pgvector not installed on Windows** — Added `PGVECTOR_ENABLED` env var. `app/storage/models.py` checks the var + Python import to select `Vector(768)` vs `sa.Text()`. `app/storage/db.py` wraps `CREATE EXTENSION` in try/except. Autogenerated migration fixed to use `sa.Text()`.
4. **`InFailedSQLTransactionError` on RAG search** — pgvector query fails → aborted transaction → fallback query also fails. Fixed with `await session.rollback()` before fallback in `app/storage/vector.py`.
5. **All RAG ingest chunks failing** — `list[float]` can't serialize to `Text` column. Fixed with `json.dumps(embedding)` when `_VECTOR_AVAILABLE=False` in `app/storage/vector.py`.
6. **Wrong vector dimensions** — `nomic-embed-text` outputs 768 dims, not 1536. Fixed `Vector(1536)` → `Vector(768)` in `app/storage/models.py`.
7. **Reduced chunk size** — `app/services/rag.py` default chunk size 400→150 words, overlap 50→20 words for faster ingestion.

**Frontend redesign (UI Phase 3):**
1. **New design system** — `frontend/app/globals.css` completely rewritten. Indigo-based tokens (`--primary: #6366f1`), dark sidebar (`--sb-bg: #0f172a`), light page (`--page-bg: #f1f5f9`). Removed all dark mode CSS.
2. **Dark mode removed** — Deleted `frontend/lib/theme.ts` usage, removed toggle button from `Topbar.tsx`, pinned `data-bs-theme="light"` in `frontend/app/layout.tsx` with inline script clearing `mcp_theme` from localStorage.
3. **New sidebar** — `frontend/components/Sidebar.tsx` rewritten: inline SVG logo, 3-section accordion structure (Overview, Team Tools, Shared), white chevron, brand link changed to `/`.
4. **SidebarProvider** — New `frontend/lib/sidebar.tsx` context for mobile open/close state. `Topbar.tsx` uses hamburger. Dashboard layout wraps in `SidebarProvider` + adds `MobileNav` bottom bar.
5. **New favicon** — `frontend/public/favicon.svg` (hexagonal neural-network icon, indigo).
6. **Landing page** — `frontend/app/page.tsx` completely rewritten as a professional dark landing page: Navbar, Hero + terminal preview, Stats strip, Capabilities (3 cards), Features grid, How It Works, Architecture diagram, Final CTA, Footer. `export const dynamic = "force-dynamic"` prevents caching; reads `mcp_token` cookie to show "Go to Dashboard" vs "Sign In" CTAs.
7. **Login card** — White background, dark Syndrix text, clean light-mode inputs.
8. **Navigation** — Clicking Syndrix logo in sidebar goes to `/` (landing page). Landing page CTAs conditionally route to `/dashboard` or `/login` based on auth state. Fixed stale SSR cache with `force-dynamic`.
9. **Footer simplified** — Landing page footer reduced to single `© 2026 All rights reserved.` line in white text.
