from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import structlog
import structlog.stdlib
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.fastmcp import FastMCP

from app.config import get_settings

# ---------------------------------------------------------------------------
# structlog configuration
# ---------------------------------------------------------------------------

def _configure_logging() -> None:
    settings = get_settings()
    shared_processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]
    if settings.is_production:
        # JSON output for production log aggregation
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


_configure_logging()
logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# FastMCP server — created ONCE and shared across all tool modules
# ---------------------------------------------------------------------------
mcp_server = FastMCP("syndrix")

# Register all tool modules (imports execute the register_*_tools() calls)
from app.mcp_tools.health_tools import register_health_tools  # noqa: E402
from app.mcp_tools.crm_tools import register_crm_tools  # noqa: E402
from app.mcp_tools.dev_tools import register_dev_tools  # noqa: E402
from app.mcp_tools.mgmt_tools import register_mgmt_tools  # noqa: E402
from app.mcp_tools.rag_tools import register_rag_tools  # noqa: E402
from app.mcp_tools.prompt_tools import register_prompt_tools  # noqa: E402
from app.mcp_tools.memory_tools import register_memory_tools  # noqa: E402

register_health_tools(mcp_server)
register_crm_tools(mcp_server)
register_dev_tools(mcp_server)
register_mgmt_tools(mcp_server)
register_rag_tools(mcp_server)
register_prompt_tools(mcp_server)
register_memory_tools(mcp_server)

# ---------------------------------------------------------------------------
# Lifespan: startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Connect to infrastructure on startup; clean up on shutdown."""
    settings = get_settings()
    log = logger.bind(env=settings.app_env)

    # --- Startup ---
    log.info("platform_starting", version="0.1.0")

    # Database
    try:
        from app.storage.db import init_db
        await init_db()
        log.info("database_connected")
    except Exception as exc:  # noqa: BLE001
        log.error("database_connect_failed", error=str(exc))

    # Redis
    redis_client = None
    try:
        import redis.asyncio as aioredis
        redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        await redis_client.ping()
        log.info("redis_connected", url=settings.redis_url)

        # Wire Redis into services that need it
        from app.services.audit import audit_service
        from app.services.memory import memory_service
        from app.services.cache import cache_service
        audit_service.set_redis(redis_client)
        memory_service.set_redis(redis_client)
        cache_service.set_redis(redis_client)

        # Store on app state for health checks and other access
        app.state.redis = redis_client
    except Exception as exc:  # noqa: BLE001
        log.error("redis_connect_failed", error=str(exc))
        app.state.redis = None

    # Bootstrap admin user (once, if no admin exists in DB)
    try:
        import uuid as _uuid
        from sqlalchemy import select
        from app.storage.db import get_db
        from app.storage.models import User
        from passlib.context import CryptContext

        _pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

        async with get_db() as session:
            existing = await session.execute(
                select(User).where(User.role == "admin").limit(1)
            )
            if existing.scalar_one_or_none() is None:
                admin = User(
                    id=_uuid.uuid4(),
                    email=settings.admin_email,
                    hashed_password=_pwd_ctx.hash(settings.admin_password),
                    full_name="Admin",
                    team_name="admin_team",
                    role="admin",
                    is_active=True,
                )
                session.add(admin)
                log.info("admin_user_created", email=settings.admin_email)
            else:
                log.info("admin_user_exists")
    except Exception as exc:  # noqa: BLE001
        log.error("admin_bootstrap_failed", error=str(exc))

    log.info("platform_ready", host=settings.api_host, port=settings.api_port)

    yield  # Application runs here

    # --- Shutdown ---
    log.info("platform_shutting_down")

    if redis_client is not None:
        try:
            await redis_client.aclose()
            log.info("redis_disconnected")
        except Exception as exc:  # noqa: BLE001
            log.warning("redis_disconnect_error", error=str(exc))

    try:
        from app.storage.db import close_db
        await close_db()
        log.info("database_disconnected")
    except Exception as exc:  # noqa: BLE001
        log.warning("database_disconnect_error", error=str(exc))

    log.info("platform_stopped")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Syndrix",
    description="Internal MCP-based AI tool platform.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — open in dev, restrict in prod
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next: Any) -> Response:
    start = time.perf_counter()
    response: Response = await call_next(request)
    latency_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        latency_ms=round(latency_ms, 2),
        client=request.client.host if request.client else "unknown",
    )
    return response


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
from app.api.health import router as health_router  # noqa: E402
from app.api.admin import router as admin_router  # noqa: E402
from app.api.auth import router as auth_router  # noqa: E402
from app.api.ingest import router as ingest_router  # noqa: E402
from app.api.tools import router as tools_router  # noqa: E402
from app.api.settings import router as settings_router  # noqa: E402
from app.api.agent import router as agent_router  # noqa: E402
from app.api.integrations import router as integrations_router  # noqa: E402
from app.api.podio_files import router as podio_files_router  # noqa: E402
from app.api.llm import router as llm_router  # noqa: E402

app.include_router(auth_router)
app.include_router(health_router)
app.include_router(admin_router)
app.include_router(ingest_router)
app.include_router(tools_router)
app.include_router(settings_router)
app.include_router(agent_router)
app.include_router(integrations_router)
app.include_router(podio_files_router)
app.include_router(llm_router)

# ---------------------------------------------------------------------------
# Mount MCP server
# ---------------------------------------------------------------------------
# The MCP SDK exposes either sse_app() or http_app() depending on version.
# We try http_app() first (newer SDK), fall back to sse_app() (older SDK).
try:
    try:
        mcp_asgi = mcp_server.http_app(path="/")
    except TypeError:
        mcp_asgi = mcp_server.http_app()
    app.mount("/mcp", mcp_asgi)
    logger.info("mcp_mounted", path="/mcp", transport="http")
except AttributeError:
    try:
        mcp_asgi = mcp_server.sse_app()
        app.mount("/mcp", mcp_asgi)
        logger.info("mcp_mounted", path="/mcp", transport="sse")
    except AttributeError:
        logger.warning("mcp_mount_failed", msg="Could not mount MCP server — check SDK version.")


@app.get("/", tags=["root"], include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "service": "Syndrix",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
        "mcp": "/mcp",
    }
