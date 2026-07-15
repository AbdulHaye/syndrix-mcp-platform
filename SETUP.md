# Syndrix — Setup Guide (New Machine)

Step-by-step instructions to get the Syndrix MCP platform running on a fresh system.
The stack is **fully local**: FastAPI backend + Next.js frontend, backed by
PostgreSQL, Redis, and Ollama. PostgreSQL can run either as a **native install** or in a
**Docker container** — pick one (see step 3, Options A/B). Redis and Ollama are expected as
native installs here.

> Two servers to run at minimum: the **backend** (uvicorn, port 8000) and the
> **frontend** (Next.js, port 3000). PostgreSQL, Redis, and Ollama must be running first.
> Celery workers are optional (only for background jobs).

---

## 1. Prerequisites

Install these before anything else. Versions listed are known-good.

| Software | Version | Notes |
|----------|---------|-------|
| **Python** | 3.11.x | Backend runtime. `python --version` must report 3.11. |
| **Node.js** | 18+ (20 LTS recommended) | Frontend. Includes `npm`. |
| **PostgreSQL** | 16 or 18 | Native install (NOT Docker). Remember the superuser password. |
| **Redis** | 5+ | Cache + Celery broker. |
| **Ollama** | latest | Local LLM. Optional if you only use cloud LLMs (Groq/Gemini/etc.), but the app expects it configured. |
| **Git** | any | To clone the repo. |

### Platform-specific install

**Windows**
- Python: https://www.python.org/downloads/ — check "Add python.exe to PATH".
- Node: https://nodejs.org/ (LTS).
- PostgreSQL: https://www.postgresql.org/download/windows/ (EDB installer). Installs as a Windows service `postgresql-x64-XX` that auto-starts.
- Redis: use **Memurai** (https://www.memurai.com/) or Redis on WSL2. Native Windows Redis is unofficial.
- Ollama: https://ollama.com/download

**macOS**
```bash
brew install python@3.11 node postgresql@16 redis
brew services start postgresql@16
brew services start redis
# Ollama: download the app from ollama.com or `brew install ollama`
```

**Ubuntu/Debian**
```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv nodejs npm postgresql redis-server
sudo systemctl enable --now postgresql redis-server
curl -fsSL https://ollama.com/install.sh | sh
```

---

## 2. Clone the repository

```bash
git clone <REPO_URL> syndrix-mcp-platform
cd syndrix-mcp-platform
```

The project root contains `app/` (backend), `frontend/` (Next.js), `alembic/` (migrations),
`requirements.txt`, and `.env.example`.

---

## 3. PostgreSQL — create the database and user

Pick **one** of the two options below. Both end up with a Postgres listening on
`localhost:5432` with user `mcp_user` / password `mcp_pass` / database `syndrix`, which is
what the default `DATABASE_URL` expects.

> ⚠️ **Only run ONE Postgres on port 5432.** If a machine has both a native Postgres service
> and a Docker container mapping 5432, they fight over the port and the app fails to connect
> (`asyncpg CannotConnectNowError`). Choose native **or** Docker — not both.

---

### Option A — Native PostgreSQL

Open `psql` as the Postgres superuser and run:

```sql
CREATE USER mcp_user WITH PASSWORD 'mcp_pass';
CREATE DATABASE syndrix OWNER mcp_user;
GRANT ALL PRIVILEGES ON DATABASE syndrix TO mcp_user;
```

**How to open `psql`:**
- Windows: `& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres` (adjust version), enter superuser password.
- macOS/Linux: `sudo -u postgres psql`

> The default `DATABASE_URL` in `.env` expects exactly this user/password/db:
> `mcp_user` / `mcp_pass` / `syndrix` on `localhost:5432`. If you change any of them,
> update `DATABASE_URL` accordingly.

**pgvector (optional):** Semantic RAG search uses the `pgvector` extension. It is **off by
default** (`PGVECTOR_ENABLED=false`) and the app falls back to keyword/recency ranking, so
you can skip it. To enable: install the pgvector extension for your Postgres, then
`CREATE EXTENSION vector;` inside the `syndrix` DB, and set `PGVECTOR_ENABLED=true` in `.env`.

---

### Option B — Docker PostgreSQL (Windows)

Run Postgres in a container instead of installing it natively. The image
`pgvector/pgvector:pg16` is Postgres 16 **with pgvector built in**, so semantic RAG search
can be enabled later. Follow the steps in order — each command is a single line you can copy
and paste directly.

#### Step B1 — Install Docker Desktop

1. Download **Docker Desktop for Windows**: https://www.docker.com/products/docker-desktop/
2. Run the installer, accept the WSL2 option, and **reboot if prompted**.
3. Launch **Docker Desktop** and wait until the bottom-left status shows **"Engine running"**.

Confirm Docker works — open **PowerShell** and run:

```powershell
docker --version
```

You should see a version string (e.g. `Docker version 27.x.x`). If you get "command not
found," Docker Desktop is not installed or not started.

#### Step B2 — Free up port 5432 (only if native Postgres is installed)

If this machine has **no** native PostgreSQL, skip to Step B3.

If it does, the native service must be stopped or it will conflict with the container.
**Open PowerShell as Administrator** (Start → type "PowerShell" → right-click → *Run as
administrator*), then run these two commands one at a time. Replace `18` with your installed
version (check with `Get-Service postgresql*`):

```powershell
Stop-Service postgresql-x64-18
```

```powershell
Set-Service postgresql-x64-18 -StartupType Manual
```

The first stops it now; the second prevents it from auto-starting on the next reboot.

#### Step B3 — Start the Postgres container

In a normal (non-admin) **PowerShell**, run this **single-line** command. It downloads the
image on first run and creates the `mcp_user` / `mcp_pass` / `syndrix` database automatically:

```powershell
docker run -d --name syndrix-postgres --restart unless-stopped -e POSTGRES_USER=mcp_user -e POSTGRES_PASSWORD=mcp_pass -e POSTGRES_DB=syndrix -p 5432:5432 -v syndrix_pgdata:/var/lib/postgresql/data pgvector/pgvector:pg16
```

> Because the container auto-creates the user and database, you do **NOT** run any
> `CREATE USER` / `CREATE DATABASE` SQL — it already matches the default `DATABASE_URL`.
> The named volume `syndrix_pgdata` keeps your data safe across restarts.

#### Step B4 — Verify the container is running

Run each command on its own:

```powershell
docker ps
```
`syndrix-postgres` should be listed with status **Up**.

Then check the database is reachable:

```powershell
docker exec -it syndrix-postgres psql -U mcp_user -d syndrix -c "SELECT version();"
```

A PostgreSQL version line means it's working. **Postgres is ready — continue to step 4 (Redis).**

#### Step B5 — (Optional) Enable pgvector

Only if you want semantic RAG search. Run:

```powershell
docker exec -it syndrix-postgres psql -U mcp_user -d syndrix -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Then set `PGVECTOR_ENABLED=true` in your backend `.env` (step 7).

#### Managing the container (reference)

Run any of these single lines as needed:

```powershell
docker stop syndrix-postgres
```
```powershell
docker start syndrix-postgres
```
```powershell
docker logs syndrix-postgres
```
```powershell
docker rm -f syndrix-postgres
```
```powershell
docker volume rm syndrix_pgdata
```
- `stop` / `start` — pause and resume the container (data kept).
- `logs` — view Postgres output for debugging.
- `rm -f` — delete the container; **data in the `syndrix_pgdata` volume is kept**.
- `volume rm` — **permanently deletes all data** (only after the container is removed).

Because `DATABASE_URL` still points at `localhost:5432`, the backend and Alembic need **no
changes** — the rest of this guide (migrations, running the server) is identical.

---

## 4. Redis — verify it's running

```bash
redis-cli ping     # should return: PONG
```
Windows/Memurai: `memurai-cli ping`. Default URL `redis://localhost:6379/0` — no auth.

---

## 5. Ollama — pull the models

```bash
ollama serve            # starts the server on :11434 (runs as a service on most installs)
ollama pull llama3.2            # default chat model
ollama pull nomic-embed-text    # embeddings model
```

> Note: `llama3.2` (3B) is fine for the app to boot and for embeddings, but it is **too weak
> for the Podio Agent's multi-step tool calling**. For the Podio Agent, configure a strong
> cloud model in the UI later (Groq `llama-3.3-70b-versatile`, Mistral Large, Gemini, or
> Claude). Cloud API keys are entered in the app's Settings page, not in `.env`.

---

## 6. Backend — Python environment & dependencies

From the project root:

```bash
# Create and activate a virtual environment
python -m venv env

# Activate:
#   Windows PowerShell:  .\env\Scripts\Activate.ps1
#   Windows cmd:         env\Scripts\activate.bat
#   Git Bash / macOS / Linux:  source env/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

> `requirements.txt` pins `bcrypt==4.0.1` on purpose — passlib 1.7.4 breaks with newer
> bcrypt. Do not upgrade bcrypt.

---

## 7. Backend — environment file (`.env`)

Copy the example and fill it in:

```bash
cp .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
```

The `.env.example` ships mostly blank. Use these **working defaults** for a local dev setup
(matches the DB created in step 3):

```dotenv
# ── App ──
APP_ENV=development
SECRET_KEY=dev-secret-change-me-in-production
API_HOST=0.0.0.0
API_PORT=8000

# ── Database ──
DATABASE_URL=postgresql+asyncpg://mcp_user:mcp_pass@localhost:5432/syndrix

# ── Redis ──
REDIS_URL=redis://localhost:6379/0

# ── Ollama ──
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=llama3.2
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_TIMEOUT=300          # optional — agent uses long tool chains
OLLAMA_NUM_CTX=8192         # optional — must be >2048 for system prompt + tool schemas

# ── pgvector ──
PGVECTOR_ENABLED=false      # set true only if the vector extension is installed

# ── JWT Auth ──
JWT_SECRET_KEY=dev-jwt-secret-change-me-in-production
JWT_EXPIRE_MINUTES=480

# ── Bootstrap admin (auto-created on first startup if none exists) ──
ADMIN_EMAIL=admin@syndrix.local
ADMIN_PASSWORD=changeme

# ── DEV_TOKENS — static fallback tokens for testing ──
DEV_TOKENS=bd_team:bd-secret-token-123,dev_team:dev-secret-token-456,mgmt_team:mgmt-secret-token-789

# ── Optional integration credentials (fill in only when wiring each) ──
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

**Required (app won't boot without them):** `APP_ENV`, `SECRET_KEY`, `API_HOST`, `API_PORT`,
`DATABASE_URL`, `REDIS_URL`, `OLLAMA_BASE_URL`, `OLLAMA_DEFAULT_MODEL`, `OLLAMA_EMBED_MODEL`.

**For production:** change `SECRET_KEY`, `JWT_SECRET_KEY`, and `ADMIN_PASSWORD`; leave
`DEV_TOKENS` empty.

> Podio Client ID/Secret and all LLM API keys can also be entered later via the app's
> **Settings** page (stored in the DB `integration_settings` table) — that is the intended
> path for the Podio Agent. `.env` credentials are optional fallbacks.

---

## 8. Backend — run database migrations

With the venv active and `.env` in place:

```bash
alembic upgrade head
```

This creates all tables: `users`, `audit_logs`, `knowledge_documents`, `team_tokens`,
`integration_settings`, etc. (Alembic reads `DATABASE_URL` from the environment.)

---

## 9. Backend — start the server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

On first startup the app **auto-creates the admin user** from `ADMIN_EMAIL` / `ADMIN_PASSWORD`.

**Verify:** open http://localhost:8000/docs (FastAPI Swagger UI). Test login:

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@syndrix.local","password":"changeme"}'
```
A JWT in the response means the backend + DB are wired correctly.

---

## 10. Frontend — install & configure

In a new terminal:

```bash
cd frontend
npm install
```

Create `frontend/.env` (copy from `frontend/.env.example`):

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_DEV_TOKEN_BD=bd-secret-token-123
NEXT_PUBLIC_DEV_TOKEN_DEV=dev-secret-token-456
NEXT_PUBLIC_DEV_TOKEN_MGMT=mgmt-secret-token-789
```

> The `NEXT_PUBLIC_DEV_TOKEN_*` values must match the **token portion** of `DEV_TOKENS` in
> the backend `.env`. `NEXT_PUBLIC_API_URL` must point at the backend.

---

## 11. Frontend — run

```bash
npm run dev      # from the frontend/ directory
```

Open **http://localhost:3000**. Log in with `admin@syndrix.local` / `changeme`.

---

## 12. (Optional) Celery background workers

Only needed for background jobs / scheduled tasks. Requires Redis running.

```bash
# From project root, venv active:
celery -A app.workers.jobs worker --loglevel=info
celery -A app.workers.jobs beat --loglevel=info      # scheduled/periodic jobs
```

> On Windows, Celery's default prefork pool can misbehave — add `--pool=solo` if the worker
> won't start: `celery -A app.workers.jobs worker --loglevel=info --pool=solo`.

---

## 13. Podio Agent — connect (the main feature)

Once the app is up and you've logged in, to use the Podio Agent:

1. **Settings → Podio (MCP):** enter Client ID + Secret.
   Redirect URI: `http://localhost:8000/integrations/podio/callback`
2. **Settings:** add an LLM provider API key (Groq / Gemini / Mistral / OpenAI / Anthropic).
3. **Podio Agent → Connect Podio** → browser login (enables hosted MCP: read/search).
4. **Podio Agent → Connect Files** → browser login (enables REST layer: write/files/flows).
   Redirect URI: `http://localhost:8000/integrations/podio-files/callback`
5. **Pick a workspace** (org → workspace cascade).
6. **Pick a strong model** (Groq `llama-3.3-70b-versatile`, Mistral Large, Gemini, or Claude —
   NOT local `llama3.2`).

See `CLAUDE.md` → "⭐ Podio Agent" for full architecture and troubleshooting.

---

## Startup checklist (every time)

1. PostgreSQL service running (native, port 5432).
2. Redis running (`redis-cli ping` → PONG).
3. Ollama running (`ollama serve` / service).
4. Backend: venv active → `uvicorn app.main:app --reload --port 8000`.
5. Frontend: `cd frontend && npm run dev`.
6. Browse http://localhost:3000.

---

## Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `/auth/login` 500, `asyncpg CannotConnectNowError` | Two Postgres servers fighting over port 5432. Keep only the **native** service running; stop any Docker `syndrix-postgres`. Kill hung `postgres` processes, delete `postmaster.pid`, restart the service. |
| Backend won't start, missing-field validation error | A required `.env` key is blank (see step 7). |
| `alembic upgrade head` connection refused | Postgres not running, or `DATABASE_URL` user/password/db doesn't match what you created in step 3. |
| bcrypt / passlib error on login | Ensure `bcrypt==4.0.1` (pinned in requirements). Reinstall: `pip install bcrypt==4.0.1`. |
| Frontend 401 on every request | JWT expired → log in again. Or `NEXT_PUBLIC_API_URL` / dev tokens don't match backend. |
| Podio Agent "Authentication as None is not allowed" (403) | You used `client_credentials` — you must do the **browser login** (Connect Podio + Connect Files). |
| Podio Agent gives garbled / broken tool calls | The selected model is too weak. Switch to Groq 70B / Mistral Large / Gemini / Claude. |
| Ollama 400 "does not support tools" | You selected `llama3` — it can't do function calling. Use `llama3.2` or a cloud model. |
| Redis connection refused | Redis/Memurai not started; check `REDIS_URL`. |

---

## Port reference

| Service | Port |
|---------|------|
| Backend (FastAPI/uvicorn) | 8000 |
| Frontend (Next.js) | 3000 |
| PostgreSQL | 5432 |
| Redis | 6379 |
| Ollama | 11434 |
