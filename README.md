# Syndrix

Internal AI capability hub for BD, Dev, and Management teams.

**Backend:** Python 3.11 · FastAPI · PostgreSQL · Redis · Ollama  
**Frontend:** Next.js 14 · Bootstrap 5

---

## Prerequisites

Install these before starting:

| Service | Install |
|---------|---------|
| Python 3.11+ | https://www.python.org/downloads/ |
| Node.js 18+ | https://nodejs.org/ |
| PostgreSQL 15+ | https://www.postgresql.org/download/ |
| Redis | https://redis.io/docs/install/ (Windows: use WSL2 or https://github.com/tporadowski/redis/releases) |
| Ollama | https://ollama.com/download |

---

## 1 — Environment setup

```bash
# Copy and fill in credentials
copy .env.example .env
copy frontend\.env.example frontend\.env
```

The `.env` file is pre-configured for local defaults — no changes needed to get started:

```
DATABASE_URL=postgresql+asyncpg://mcp_user:mcp_pass@localhost:5432/syndrix
REDIS_URL=redis://localhost:6379/0
OLLAMA_BASE_URL=http://localhost:11434
```

In `frontend/.env.local`:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_DEV_TOKEN_BD=bd-secret-token-123
NEXT_PUBLIC_DEV_TOKEN_DEV=dev-secret-token-456
NEXT_PUBLIC_DEV_TOKEN_MGMT=mgmt-secret-token-789
```

---

## 2 — PostgreSQL setup

Create the database and user (run once):

```sql
-- In psql as superuser
CREATE USER mcp_user WITH PASSWORD 'mcp_pass';
CREATE DATABASE syndrix OWNER mcp_user;
\c syndrix
CREATE EXTENSION IF NOT EXISTS vector;
```

---

## 3 — Ollama — pull models (one-time)

```bash
ollama pull llama3.2
ollama pull nomic-embed-text
```

Ollama runs as a background service automatically after install. Default URL: `http://localhost:11434`

---

## 4 — Backend

```bash
# Install dependencies
pip install -r requirements.txt

# Run database migrations
alembic upgrade head

# Start the API server (auto-reloads on file save)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API is live at **http://localhost:8000**  
Swagger docs at **http://localhost:8000/docs**

Optional — start the Celery worker in a second terminal:

```bash
celery -A app.workers.jobs worker --loglevel=info
```

---

## 5 — Frontend

```bash
cd frontend
npm install
npm run dev
```

UI is live at **http://localhost:3000**

Log in with one of the quick-select team buttons on the login page.

---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             

## Default dev tokens

| Team | Token |
|------|-------|
| BD | `bd-secret-token-123` |
| Dev | `dev-secret-token-456` |
| Mgmt | `mgmt-secret-token-789` |

Change these in `.env` → `DEV_TOKENS` before going to production.
