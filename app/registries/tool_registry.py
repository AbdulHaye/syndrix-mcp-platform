from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.auth.bearer import TeamRole


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    required_roles: list[TeamRole] = Field(default_factory=list)
    source_system: str = "internal"
    timeout_seconds: int = 30
    audit_flag: bool = True


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool_def: ToolDefinition) -> None:
        self._tools[tool_def.name] = tool_def

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_for_role(self, role: TeamRole) -> list[ToolDefinition]:
        results: list[ToolDefinition] = []
        for tool in self._tools.values():
            if not tool.required_roles:
                results.append(tool)
            elif role == TeamRole.ADMIN or role in tool.required_roles:
                results.append(tool)
        return results

    def all(self) -> list[ToolDefinition]:
        return list(self._tools.values())


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
tool_registry = ToolRegistry()

# ---------------------------------------------------------------------------
# Pre-registered tool definitions
# ---------------------------------------------------------------------------
_PLACEHOLDER_STRING_PROP = {"type": "string"}

tool_registry.register(ToolDefinition(
    name="crm.contact.get",
    description="Retrieve a CRM contact record by ID.",
    input_schema={"type": "object", "properties": {"contact_id": _PLACEHOLDER_STRING_PROP}, "required": ["contact_id"]},
    output_schema={"type": "object"},
    required_roles=[TeamRole.BD, TeamRole.ADMIN],
    source_system="podio",
    timeout_seconds=15,
    audit_flag=True,
))

tool_registry.register(ToolDefinition(
    name="crm.note.create",
    description="Create a note against a CRM client record.",
    input_schema={
        "type": "object",
        "properties": {
            "client_id": _PLACEHOLDER_STRING_PROP,
            "note": _PLACEHOLDER_STRING_PROP,
        },
        "required": ["client_id", "note"],
    },
    output_schema={"type": "object"},
    required_roles=[TeamRole.BD, TeamRole.ADMIN],
    source_system="podio",
    timeout_seconds=15,
    audit_flag=True,
))

tool_registry.register(ToolDefinition(
    name="crm.lead.search",
    description="Search CRM leads by keyword or filter query.",
    input_schema={
        "type": "object",
        "properties": {
            "query": _PLACEHOLDER_STRING_PROP,
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["query"],
    },
    output_schema={"type": "array", "items": {"type": "object"}},
    required_roles=[TeamRole.BD, TeamRole.ADMIN],
    source_system="podio",
    timeout_seconds=20,
    audit_flag=True,
))

tool_registry.register(ToolDefinition(
    name="repo.search",
    description="Full-text search across code repositories.",
    input_schema={"type": "object", "properties": {"query": _PLACEHOLDER_STRING_PROP}, "required": ["query"]},
    output_schema={"type": "array"},
    required_roles=[TeamRole.DEV, TeamRole.ADMIN],
    source_system="github",
    timeout_seconds=20,
    audit_flag=False,
))

tool_registry.register(ToolDefinition(
    name="ticket.create",
    description="Create a new project management ticket.",
    input_schema={
        "type": "object",
        "properties": {
            "title": _PLACEHOLDER_STRING_PROP,
            "description": _PLACEHOLDER_STRING_PROP,
            "priority": {"type": "string", "default": "medium"},
        },
        "required": ["title", "description"],
    },
    output_schema={"type": "object"},
    required_roles=[TeamRole.DEV, TeamRole.ADMIN],
    source_system="jira",
    timeout_seconds=15,
    audit_flag=True,
))

tool_registry.register(ToolDefinition(
    name="spec.generate",
    description="Generate a technical specification document from a feature description.",
    input_schema={
        "type": "object",
        "properties": {"feature_description": _PLACEHOLDER_STRING_PROP},
        "required": ["feature_description"],
    },
    output_schema={"type": "object"},
    required_roles=[TeamRole.DEV, TeamRole.ADMIN],
    source_system="llm",
    timeout_seconds=60,
    audit_flag=True,
))

tool_registry.register(ToolDefinition(
    name="bug.triage",
    description="Analyse an error message and suggest a root-cause triage path.",
    input_schema={
        "type": "object",
        "properties": {
            "error_message": _PLACEHOLDER_STRING_PROP,
            "context": _PLACEHOLDER_STRING_PROP,
        },
        "required": ["error_message"],
    },
    output_schema={"type": "object"},
    required_roles=[TeamRole.DEV, TeamRole.ADMIN],
    source_system="llm",
    timeout_seconds=60,
    audit_flag=True,
))

tool_registry.register(ToolDefinition(
    name="report.team.daily",
    description="Generate or retrieve the daily team activity report.",
    input_schema={"type": "object", "properties": {}},
    output_schema={"type": "object"},
    required_roles=[TeamRole.MGMT, TeamRole.ADMIN],
    source_system="internal",
    timeout_seconds=30,
    audit_flag=True,
))

tool_registry.register(ToolDefinition(
    name="health.ping",
    description="Ping the MCP platform to verify liveness.",
    input_schema={"type": "object", "properties": {}},
    output_schema={"type": "object"},
    required_roles=[TeamRole.BD, TeamRole.DEV, TeamRole.MGMT, TeamRole.ADMIN],
    source_system="internal",
    timeout_seconds=5,
    audit_flag=False,
))
