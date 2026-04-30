from __future__ import annotations

from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

logger = structlog.get_logger(__name__)


def register_crm_tools(mcp: FastMCP) -> None:
    """Register CRM-related MCP tools on the provided FastMCP server."""

    @mcp.tool(
        name="crm.contact.get",
        description="Retrieve a CRM contact record by its unique ID from Podio.",
    )
    async def crm_contact_get(contact_id: str) -> dict[str, Any]:
        from app.adapters.podio import podio_adapter

        logger.info("crm.contact.get_called", contact_id=contact_id)
        try:
            return await podio_adapter.get_contact(contact_id)
        except Exception as exc:
            logger.error("crm.contact.get_failed", error=str(exc))
            return {"success": False, "contact_id": contact_id, "error": str(exc)}

    @mcp.tool(
        name="crm.note.create",
        description="Create a note on a CRM client record in Podio.",
    )
    async def crm_note_create(client_id: str, note: str) -> dict[str, Any]:
        from app.adapters.podio import podio_adapter

        logger.info("crm.note.create_called", client_id=client_id, note_len=len(note))
        try:
            return await podio_adapter.create_note(client_id, note)
        except Exception as exc:
            logger.error("crm.note.create_failed", error=str(exc))
            return {"success": False, "client_id": client_id, "error": str(exc)}

    @mcp.tool(
        name="crm.lead.search",
        description="Search CRM leads in Podio using a keyword query.",
    )
    async def crm_lead_search(query: str, limit: int = 10) -> dict[str, Any]:
        from app.adapters.podio import podio_adapter

        logger.info("crm.lead.search_called", query=query, limit=limit)
        try:
            results = await podio_adapter.search_leads({"query": query, "limit": limit})
            return {"query": query, "limit": limit, "total": len(results), "results": results}
        except Exception as exc:
            logger.error("crm.lead.search_failed", error=str(exc))
            return {"success": False, "query": query, "error": str(exc)}

    @mcp.tool(
        name="crm.message.send",
        description=(
            "Send an SMS or email message to a GHL contact. "
            "channel must be 'sms' or 'email'."
        ),
    )
    async def crm_message_send(
        contact_id: str, message: str, channel: str = "sms"
    ) -> dict[str, Any]:
        from app.adapters.ghl import ghl_adapter

        logger.info("crm.message.send_called", contact_id=contact_id, channel=channel)
        try:
            return await ghl_adapter.send_message(contact_id, message, channel)
        except Exception as exc:
            logger.error("crm.message.send_failed", error=str(exc))
            return {"success": False, "contact_id": contact_id, "error": str(exc)}

    @mcp.tool(
        name="crm.pipeline.stages",
        description="List all pipeline stages in GoHighLevel.",
    )
    async def crm_pipeline_stages() -> dict[str, Any]:
        from app.adapters.ghl import ghl_adapter

        logger.info("crm.pipeline.stages_called")
        try:
            stages = await ghl_adapter.get_pipeline_stages()
            return {"success": True, "stages": stages}
        except Exception as exc:
            logger.error("crm.pipeline.stages_failed", error=str(exc))
            return {"success": False, "error": str(exc)}

    @mcp.tool(
        name="crm.email.send",
        description="Send an email to a recipient via SMTP.",
    )
    async def crm_email_send(to: str, subject: str, body: str) -> dict[str, Any]:
        from app.adapters.email import email_adapter

        logger.info("crm.email.send_called", to=to, subject=subject)
        try:
            return await email_adapter.send_email(to, subject, body)
        except Exception as exc:
            logger.error("crm.email.send_failed", error=str(exc))
            return {"success": False, "to": to, "error": str(exc)}
