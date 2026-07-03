"""Podio Files API — REST OAuth (authorization_code) + file upload/attach.

Backend for the custom Podio file-attachment capability. The bytes path lives here
(multipart upload → Podio file_id); the LLM-facing attach tool lives in
app/mcp_servers/podio_files.py.
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse, Response

from app.auth.bearer import TeamIdentity, TeamRole, require_auth

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/integrations/podio-files", tags=["integrations"])

_ALLOWED_ROLES = {TeamRole.BD, TeamRole.ADMIN}
_FRONTEND_REDIRECT_DEFAULT = "http://localhost:3000/dashboard/podio-agent"
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


def _require_bd_or_admin(identity: TeamIdentity) -> None:
    if identity.role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="BD and Admin roles only.")


@router.get("/connect", summary="Start Podio REST OAuth — returns the authorize URL")
async def connect(identity: TeamIdentity = Depends(require_auth)) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.podio_rest import podio_rest

    try:
        return {"success": True, "authorize_url": await podio_rest.build_authorize_url()}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


@router.get("/callback", summary="OAuth redirect target — exchanges the code for a token")
async def callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
) -> RedirectResponse:
    from app.services.podio_rest import podio_rest
    from app.services.settings_service import get_setting

    frontend = (await get_setting("podio_mcp_frontend_redirect")) or _FRONTEND_REDIRECT_DEFAULT
    if error:
        logger.warning("podio_rest_oauth_error", error=error, detail=error_description)
        return RedirectResponse(f"{frontend}?podio_files=error&reason={error}")
    if not code or not state:
        return RedirectResponse(f"{frontend}?podio_files=error&reason=missing_code")
    try:
        await podio_rest.exchange_code(code, state)
        return RedirectResponse(f"{frontend}?podio_files=connected")
    except Exception as exc:  # noqa: BLE001
        import urllib.parse
        logger.error("podio_rest_exchange_failed", error=str(exc))
        reason = urllib.parse.quote(str(exc)[:200]) or "exchange_failed"
        return RedirectResponse(f"{frontend}?podio_files=error&reason={reason}")


@router.get("/status", summary="Podio REST (files) connection status")
async def status(identity: TeamIdentity = Depends(require_auth)) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.podio_rest import podio_rest

    return {"connected": await podio_rest.is_connected()}


@router.post("/disconnect", summary="Disconnect Podio REST (clear stored tokens)")
async def disconnect(identity: TeamIdentity = Depends(require_auth)) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.podio_rest import podio_rest

    await podio_rest.disconnect()
    return {"success": True}


@router.get("/download/{file_id}", summary="Download (proxy) a Podio file through the backend")
async def download_file(
    file_id: int,
    identity: TeamIdentity = Depends(require_auth),
) -> Response:
    _require_bd_or_admin(identity)
    from app.services.podio_rest import podio_rest

    try:
        filename, mimetype, content = await podio_rest.download_file(file_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("podio_file_download_failed", file_id=file_id, error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    safe_name = filename.replace('"', "'")
    return Response(
        content=content,
        media_type=mimetype,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/export/{app_id}", summary="Export all records from a Podio app as an Excel (.xlsx) file")
async def export_app_xlsx(
    app_id: int,
    identity: TeamIdentity = Depends(require_auth),
) -> Response:
    _require_bd_or_admin(identity)
    from app.services.podio_rest import podio_rest

    try:
        filename, content = await podio_rest.export_app_xlsx(app_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("podio_app_export_failed", app_id=app_id, error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    safe_name = filename.replace('"', "'")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.post("/upload", summary="Upload a file to Podio (and optionally attach it to an item)")
async def upload(
    file: UploadFile = File(...),
    item_id: int | None = Form(None),
    identity: TeamIdentity = Depends(require_auth),
) -> dict[str, Any]:
    _require_bd_or_admin(identity)
    from app.services.podio_rest import podio_rest

    content = await file.read()
    if not content:
        return {"success": False, "error": "Empty file."}
    if len(content) > _MAX_UPLOAD_BYTES:
        return {"success": False, "error": "File too large (max 25 MB)."}

    try:
        uploaded = await podio_rest.upload_file(file.filename or "upload.bin", content)
        file_id = uploaded.get("file_id")
        result: dict[str, Any] = {"success": True, "file_id": file_id, "filename": file.filename}
        if item_id:
            await podio_rest.attach_file(file_id, "item", int(item_id))
            result["attached_to_item"] = int(item_id)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.error("podio_file_upload_failed", error=str(exc))
        return {"success": False, "error": str(exc)}
