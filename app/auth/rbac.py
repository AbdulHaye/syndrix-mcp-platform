from __future__ import annotations

from app.auth.bearer import TeamIdentity, TeamRole

# Maps tool name prefixes to allowed roles.
# Keys are prefix strings; values are lists of roles that may use tools
# whose names start with that prefix.
TOOL_PERMISSIONS: dict[str, list[TeamRole]] = {
    "crm.": [TeamRole.BD, TeamRole.ADMIN],
    "repo.": [TeamRole.DEV, TeamRole.ADMIN],
    "ticket.": [TeamRole.DEV, TeamRole.ADMIN],
    "spec.": [TeamRole.DEV, TeamRole.ADMIN],
    "bug.": [TeamRole.DEV, TeamRole.ADMIN],
    "slack.": [TeamRole.DEV, TeamRole.BD, TeamRole.ADMIN],
    "report.": [TeamRole.MGMT, TeamRole.ADMIN],
    "team.": [TeamRole.MGMT, TeamRole.ADMIN],
    "client.": [TeamRole.MGMT, TeamRole.ADMIN],
    "health.": [TeamRole.BD, TeamRole.DEV, TeamRole.MGMT, TeamRole.ADMIN],
    "rag.": [TeamRole.BD, TeamRole.DEV, TeamRole.MGMT, TeamRole.ADMIN],
}


def can_use_tool(tool_name: str, identity: TeamIdentity) -> bool:
    """
    Return True if the given identity may invoke the named tool.

    Matching uses prefix lookup against TOOL_PERMISSIONS keys.  The most
    specific (longest) matching prefix wins.  If no prefix matches the tool
    is considered unrestricted (all roles allowed).
    """
    # ADMIN bypasses all checks
    if identity.role == TeamRole.ADMIN:
        return True

    best_prefix: str | None = None
    for prefix in TOOL_PERMISSIONS:
        if tool_name.startswith(prefix):
            if best_prefix is None or len(prefix) > len(best_prefix):
                best_prefix = prefix

    if best_prefix is None:
        # No restriction defined — allow all authenticated users
        return True

    allowed_roles = TOOL_PERMISSIONS[best_prefix]
    return identity.role in allowed_roles
