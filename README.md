# Syndrix

Internal AI capability hub for BD, Dev, and Management teams.

**Backend:** Python 3.11 · FastAPI · PostgreSQL · Redis
**Frontend:** Next.js 14 · Bootstrap 5
**LLM access:** cloud providers (Groq, Gemini, Mistral, OpenAI, Anthropic, Z.ai, OpenRouter) configured in the Settings UI, or a local Ollama model — see [CLAUDE.md](./CLAUDE.md) for full architecture.

---

## Prerequisites

| Service | Notes |
|---------|-------|
| Python 3.11+ | https://www.python.org/downloads/ |
| Node.js 18+ | https://nodejs.org/ |
| PostgreSQL 15+ | Native install — https://www.postgresql.org/download/ |
| Redis | Windows: Memurai or WSL2 — https://redis.io/docs/install/ |
| Ollama | **Optional** — only needed for a local model; skip if you'll add a cloud LLM provider key instead. https://ollama.com/download |

---

## 1 — Clone

```bash
git clone <REPO_URL> syndrix-mcp-platform
cd syndrix-mcp-platform
```

## 2 — PostgreSQL

Create the database and user (run once, as superuser in `psql`):

```sql
CREATE USER mcp_user WITH PASSWORD 'mcp_pass';
CREATE DATABASE syndrix OWNER mcp_user;
GRANT ALL PRIVILEGES ON DATABASE syndrix TO mcp_user;
-- Optional — only if PGVECTOR_ENABLED=true:
-- \c syndrix
-- CREATE EXTENSION IF NOT EXISTS vector;
```

This matches the default `DATABASE_URL` below (`mcp_user` / `mcp_pass` / `syndrix` on `localhost:5432`).

## 3 — Redis

Make sure it's running:

```bash
redis-cli ping     # should return: PONG
```

## 4 — Ollama (optional)

Skip this if you'll use a cloud LLM provider instead (add its API key in the app's Settings page after step 7).

```bash
ollama serve
ollama pull llama3.2
ollama pull nomic-embed-text
```

Ollama runs as a background service after install — default URL `http://localhost:11434`.

## 5 — Backend

```bash
python -m venv env
# Activate — Windows PowerShell: .\env\Scripts\Activate.ps1
#            Git Bash / macOS / Linux: source env/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

copy .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
```

`.env.example` ships with blank values — fill in at least these to match the setup above (full variable reference: [CLAUDE.md § Environment Variables](./CLAUDE.md#environment-variables)):

```
SECRET_KEY=<any-random-string>
JWT_SECRET_KEY=<any-random-string>
DATABASE_URL=postgresql+asyncpg://mcp_user:mcp_pass@localhost:5432/syndrix
REDIS_URL=redis://localhost:6379/0
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=llama3.2
OLLAMA_EMBED_MODEL=nomic-embed-text
DEV_TOKENS=bd_team:bd-secret-token-123,dev_team:dev-secret-token-456,mgmt_team:mgmt-secret-token-789
```

Then create the tables and start the server:

```bash
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

This auto-creates the admin user (`admin@syndrix.local` / `changeme`) on first startup.

API: **http://localhost:8000** · Docs: **http://localhost:8000/docs**

## 6 — Frontend

```bash
cd frontend
npm install
copy .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
```

Fill in `.env`:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_DEV_TOKEN_BD=bd-secret-token-123
NEXT_PUBLIC_DEV_TOKEN_DEV=dev-secret-token-456
NEXT_PUBLIC_DEV_TOKEN_MGMT=mgmt-secret-token-789
```

```bash
npm run dev
```

UI: **http://localhost:3000**

## 7 — Log in

Either:
- **Admin login:** `admin@syndrix.local` / `changeme`, or
- **Quick-select team button** on the login page, using a dev token below (must match `DEV_TOKENS` in the backend `.env`).

| Team | Token |
|------|-------|
| BD | `bd-secret-token-123` |
| Dev | `dev-secret-token-456` |
| Mgmt | `mgmt-secret-token-789` |

⚠️ Change these before going to production.

## 8 — (Optional) Celery background workers

```bash
celery -A app.workers.jobs worker --loglevel=info
celery -A app.workers.jobs beat --loglevel=info
```

Windows: add `--pool=solo` to the worker command if it won't start.

---

## Startup checklist (every time)

1. PostgreSQL running (port 5432).
2. Redis running.
3. Ollama running (skip if using a cloud LLM provider).
4. Backend: `uvicorn app.main:app --reload --port 8000`.
5. Frontend: `cd frontend && npm run dev`.

## Port reference

| Service | Port |
|---------|------|
| Backend | 8000 |
| Frontend | 3000 |
| PostgreSQL | 5432 |
| Redis | 6379 |
| Ollama | 11434 |
