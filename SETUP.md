# Syndrix — Setup Guide

FastAPI backend + Next.js frontend, backed by PostgreSQL and Redis. LLM access is via cloud
providers (Groq, Gemini, Mistral, OpenAI, Anthropic) configured in the Settings UI — Ollama
is only needed if you want a local model instead.

---

## 1. Prerequisites

- Python 3.11
- Node.js 18+
- PostgreSQL 15+ (native install)
- Redis (Windows: Memurai or WSL2)
- Ollama (optional — skip if you'll use a cloud LLM provider)

---

## 2. Clone the repository

```bash
git clone <REPO_URL> syndrix-mcp-platform
cd syndrix-mcp-platform
```

---

## 3. PostgreSQL — create the database and user

Open `psql` as the superuser and run:

```sql
CREATE USER mcp_user WITH PASSWORD 'mcp_pass';
CREATE DATABASE syndrix OWNER mcp_user;
GRANT ALL PRIVILEGES ON DATABASE syndrix TO mcp_user;
```

This matches the default `DATABASE_URL` in `.env.example` (`mcp_user` / `mcp_pass` / `syndrix` on `localhost:5432`).

---

## 4. Redis — verify it's running

```bash
redis-cli ping     # should return: PONG
```

---

## 5. Ollama (optional — only if not using a cloud LLM provider)

```bash
ollama serve
ollama pull llama3.2
ollama pull nomic-embed-text
```

If skipped, add an LLM provider API key (Groq / Gemini / Mistral / OpenAI / Anthropic) in the
app's Settings page after step 8.

---

## 6. Backend — install and configure

```bash
python -m venv env
# Activate:
#   Windows PowerShell:  .\env\Scripts\Activate.ps1
#   Git Bash / macOS / Linux:  source env/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

copy .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
```

The default values in `.env` already match the database created in step 3 — no edits needed to get started.

---

## 7. Backend — create tables and start the server

```bash
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

This creates all tables and auto-creates the admin user (`admin@syndrix.local` / `changeme`) on first startup.

API: **http://localhost:8000** · Docs: **http://localhost:8000/docs**

---

## 8. Frontend — install and run

```bash
cd frontend
npm install
copy .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
npm run dev
```

Open **http://localhost:3000** and log in with `admin@syndrix.local` / `changeme`.

---

## 9. (Optional) Celery background workers

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

---

## Port reference

| Service | Port |
|---------|------|
| Backend | 8000 |
| Frontend | 3000 |
| PostgreSQL | 5432 |
| Redis | 6379 |
| Ollama | 11434 |
