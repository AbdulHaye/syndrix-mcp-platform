from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.auth.bearer import TeamIdentity, TeamRole, require_auth

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/integrations/mycase", tags=["integrations"])

_ALLOWED_ROLES = {TeamRole.BD, TeamRole.ADMIN}
_FRONTEND_REDIRECT_DEFAULT = "http://localhost:3000/dashboard/mycase-agent"


def _require_bd_or_admin(identity: TeamIdentity) -> None:
    if identity.role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="BD and Admin roles only.")


@router.get("/connect", summary="Start MyCase OAuth (Authorization Code grant) — returns the authorize URL")
async def connect(identity: TeamIdentity = Depends(require_auth)) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.mycase_rest import mycase_rest

    try:
        url = await mycase_rest.build_authorize_url()
        return {"success": True, "authorize_url": url}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


@router.get("/callback", summary="OAuth redirect target — exchanges the code for a token")
async def callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
) -> RedirectResponse:
    # No auth dependency: this is MyCase's browser redirect, validated by the
    # `state` we issued in build_authorize_url().
    from app.services.mycase_rest import mycase_rest
    from app.services.settings_service import get_setting

    frontend = (await get_setting("mycase_frontend_redirect")) or _FRONTEND_REDIRECT_DEFAULT

    if error:
        logger.warning("mycase_oauth_error", error=error, detail=error_description)
        return RedirectResponse(f"{frontend}?mycase=error&reason={error}")
    if not code or not state:
        return RedirectResponse(f"{frontend}?mycase=error&reason=missing_code")

    try:
        await mycase_rest.exchange_code(code, state)
        return RedirectResponse(f"{frontend}?mycase=connected")
    except Exception as exc:  # noqa: BLE001
        logger.error("mycase_exchange_failed", error=str(exc))
        return RedirectResponse(f"{frontend}?mycase=error&reason=exchange_failed")


@router.post("/disconnect", summary="Disconnect MyCase (clear stored tokens)")
async def disconnect(identity: TeamIdentity = Depends(require_auth)) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.mycase_rest import mycase_rest

    await mycase_rest.disconnect()
    return {"success": True}
