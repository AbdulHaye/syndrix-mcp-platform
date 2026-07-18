from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.db import Base

import os

try:
    from pgvector.sqlalchemy import Vector  # type: ignore[import]
    _VECTOR_AVAILABLE = os.environ.get("PGVECTOR_ENABLED", "true").lower() != "false"
except ImportError:  # pragma: no cover
    _VECTOR_AVAILABLE = False
    Vector = None  # type: ignore[assignment, misc]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    team_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(256), nullable=False)
    input_summary: Mapped[str] = mapped_column(Text, default="")
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} tool={self.tool_name} team={self.team_name}>"


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    if _VECTOR_AVAILABLE and Vector is not None:
        embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    else:
        # Fallback when pgvector is not installed — store as text
        embedding: Mapped[str | None] = mapped_column(Text, nullable=True)  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"<KnowledgeDocument id={self.id} title={self.title!r}>"


class IntegrationSetting(Base):
    __tablename__ = "integration_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    def __repr__(self) -> str:
        return f"<IntegrationSetting key={self.key!r}>"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(512), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    team_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="dev")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role}>"


class PodioChatSession(Base):
    """A Podio Agent chat conversation, persisted so history survives across
    browsers/devices instead of living only in the frontend's localStorage.

    ``owner_key`` is ``user:<user_id>`` for real logins (JWT carries the user id)
    or ``team:<team_name>`` for the DEV_TOKENS fallback (no per-user identity).
    ``messages`` stores the same shape the frontend already used in localStorage
    (role/content/steps/error per turn) — no separate messages table.
    """

    __tablename__ = "podio_chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    team_name: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="New chat")
    messages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<PodioChatSession id={self.id} owner={self.owner_key!r} title={self.title!r}>"


class MyCaseChatSession(Base):
    """A MyCase Agent chat conversation — mirrors PodioChatSession exactly (a
    separate table, not a shared one, so the two agents' history stays independent
    and neither migration risks the other's data)."""

    __tablename__ = "mycase_chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    team_name: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="New chat")
    messages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<MyCaseChatSession id={self.id} owner={self.owner_key!r} title={self.title!r}>"


class TeamToken(Base):
    __tablename__ = "team_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self) -> str:
        return f"<TeamToken id={self.id} team={self.team_name} role={self.role}>"
