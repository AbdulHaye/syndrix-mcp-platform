from __future__ import annotations

from pydantic import BaseModel, Field

from app.auth.bearer import TeamRole


class ResourceDefinition(BaseModel):
    uri: str
    name: str
    description: str
    mime_type: str = "application/json"
    required_roles: list[TeamRole] = Field(default_factory=list)


class ResourceRegistry:
    def __init__(self) -> None:
        self._resources: dict[str, ResourceDefinition] = {}

    def register(self, resource_def: ResourceDefinition) -> None:
        self._resources[resource_def.uri] = resource_def

    def get(self, uri: str) -> ResourceDefinition | None:
        return self._resources.get(uri)

    def list_for_role(self, role: TeamRole) -> list[ResourceDefinition]:
        results: list[ResourceDefinition] = []
        for resource in self._resources.values():
            if not resource.required_roles:
                results.append(resource)
            elif role == TeamRole.ADMIN or role in resource.required_roles:
                results.append(resource)
        return results

    def all(self) -> list[ResourceDefinition]:
        return list(self._resources.values())


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
resource_registry = ResourceRegistry()

# ---------------------------------------------------------------------------
# Pre-registered resources
# ---------------------------------------------------------------------------
resource_registry.register(ResourceDefinition(
    uri="crm://pipelines",
    name="CRM Pipelines",
    description="All active sales pipelines from the CRM system.",
    mime_type="application/json",
    required_roles=[TeamRole.BD, TeamRole.ADMIN],
))

resource_registry.register(ResourceDefinition(
    uri="kb://bd-playbooks",
    name="BD Playbooks",
    description="Business development playbooks and call frameworks.",
    mime_type="text/markdown",
    required_roles=[TeamRole.BD, TeamRole.ADMIN],
))

resource_registry.register(ResourceDefinition(
    uri="repo://architecture",
    name="Architecture Docs",
    description="System architecture diagrams and decision records.",
    mime_type="text/markdown",
    required_roles=[TeamRole.DEV, TeamRole.ADMIN],
))

resource_registry.register(ResourceDefinition(
    uri="runbook://incident-guides",
    name="Incident Response Runbooks",
    description="Step-by-step guides for common incident scenarios.",
    mime_type="text/markdown",
    required_roles=[TeamRole.DEV, TeamRole.MGMT, TeamRole.ADMIN],
))
