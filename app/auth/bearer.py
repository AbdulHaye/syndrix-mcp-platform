from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings


class TeamRole(str, Enum):
    BD = "bd"
    DEV = "dev"
    MGMT = "mgmt"
    ADMIN = "admin"


@dataclass
class TeamIdentity:
    team_name: str
    role: TeamRole
    token: str


# Mapping from team-name fragment to role
_TEAM_ROLE_MAP: dict[str, TeamRole] = {
    "bd_team": TeamRole.BD,
    "dev_team": TeamRole.DEV,
    "mgmt_team": TeamRole.MGMT,
    "admin_team": TeamRole.ADMIN,
    "bd": TeamRole.BD,
    "dev": TeamRole.DEV,
    "mgmt": TeamRole.MGMT,
    "admin": TeamRole.ADMIN,
}


def _infer_role(team_name: str) -> TeamRole:
    """Infer a TeamRole from a team_name string."""
    lower = team_name.lower()
    for key, role in _TEAM_ROLE_MAP.items():
        if key in lower:
            return role
    # Default unknown teams to DEV role
    return TeamRole.DEV


def verify_token(token: str) -> TeamIdentity | None:
    """Verify a bearer token. Tries JWT first, then falls back to DEV_TOKENS."""
    from app.auth.jwt_utils import decode_access_token

    payload = decode_access_token(token)
    if payload is not None:
        team_name = payload.get("team_name", "unknown")
        role_str = payload.get("role", "dev")
        try:
            role = TeamRole(role_str)
        except ValueError:
            role = TeamRole.DEV
        return TeamIdentity(team_name=team_name, role=role, token=token)

    # Fallback: static DEV_TOKENS (dev/testing only)
    settings = get_settings()
    token_map = settings.get_token_map()
    team_name = token_map.get(token)
    if team_name is None:
        return None
    role = _infer_role(team_name)
    return TeamIdentity(team_name=team_name, role=role, token=token)


_bearer_scheme = HTTPBearer(auto_error=False)


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> TeamIdentity:
    """FastAPI dependency that validates Bearer token and returns TeamIdentity."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    identity = verify_token(credentials.credentials)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return identity


def require_role(role: TeamRole) -> Callable:
    """Factory that returns a FastAPI dependency checking for a minimum role."""

    async def _check_role(
        identity: TeamIdentity = Depends(require_auth),
    ) -> TeamIdentity:
        # ADMIN can access everything
        if identity.role == TeamRole.ADMIN:
            return identity
        if identity.role != role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role.value}' required, got '{identity.role.value}'",
            )
        return identity

    return _check_role
