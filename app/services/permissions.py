from __future__ import annotations

from fastapi import HTTPException, status

from app.auth.bearer import TeamIdentity
from app.auth.rbac import can_use_tool


class PermissionService:
    """Wraps RBAC logic for tool-level permission checks."""

    async def check_tool_access(self, tool_name: str, identity: TeamIdentity) -> bool:
        """Return True if the identity is allowed to invoke the tool."""
        return can_use_tool(tool_name, identity)

    async def enforce_tool_access(self, tool_name: str, identity: TeamIdentity) -> None:
        """
        Raise HTTPException 403 if the identity is not allowed to use the tool.
        Otherwise return silently.
        """
        allowed = await self.check_tool_access(tool_name, identity)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Team '{identity.team_name}' with role '{identity.role.value}' "
                    f"does not have permission to use tool '{tool_name}'"
                ),
            )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
permission_service = PermissionService()
