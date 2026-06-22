from __future__ import annotations

from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

logger = structlog.get_logger(__name__)


def register_prompt_tools(mcp: FastMCP) -> None:
    """Register prompt-pack MCP tools on the provided FastMCP server."""

    @mcp.tool(
        name="prompt.list",
        description="List all available prompt templates. Pass role to filter by team.",
    )
    async def prompt_list(role: str = "") -> dict[str, Any]:
        from app.services.prompt_service import list_templates

        logger.info("prompt.list_called", role=role)
        templates = list_templates(role or None)
        return {"success": True, "count": len(templates), "templates": templates}

    @mcp.tool(
        name="prompt.run",
        description=(
            "Execute a prompt template by key with the provided variable values. "
            "Use prompt.list first to discover available keys and their required variables."
        ),
    )
    async def prompt_run(key: str, variables: dict[str, str], role: str = "") -> dict[str, Any]:
        from app.services.prompt_service import run_prompt

        logger.info("prompt.run_called", key=key, role=role)
        try:
            return await run_prompt(key, variables, role or None)
        except Exception as exc:
            logger.error("prompt.run_failed", key=key, error=str(exc))
            return {"success": False, "key": key, "error": str(exc)}

    # ── BD convenience shortcuts ──────────────────────────────────────────────

    @mcp.tool(
        name="prompt.bd.crm_summary",
        description="Generate an AI summary of a CRM contact record (dict or JSON string).",
    )
    async def bd_crm_summary(contact_data: str) -> dict[str, Any]:
        from app.services.prompt_service import run_prompt

        logger.info("prompt.bd.crm_summary_called")
        return await run_prompt(
            "bd.crm_summary",
            {"contact_data": contact_data},
            role="bd",
        )

    @mcp.tool(
        name="prompt.bd.followup_draft",
        description=(
            "Draft a personalised follow-up message. "
            "channel: 'email' | 'sms' | 'whatsapp'."
        ),
    )
    async def bd_followup_draft(
        contact_name: str,
        company: str,
        context: str,
        goal: str,
        channel: str = "email",
    ) -> dict[str, Any]:
        from app.services.prompt_service import run_prompt

        logger.info("prompt.bd.followup_draft_called", contact=contact_name)
        return await run_prompt(
            "bd.followup_draft",
            {
                "channel": channel,
                "contact_name": contact_name,
                "company": company,
                "context": context,
                "goal": goal,
            },
            role="bd",
        )

    @mcp.tool(
        name="prompt.bd.lead_qualification",
        description="Score and qualify a lead using BANT criteria.",
    )
    async def bd_lead_qualification(
        name: str, company: str, role: str, info: str
    ) -> dict[str, Any]:
        from app.services.prompt_service import run_prompt

        logger.info("prompt.bd.lead_qualification_called", name=name)
        return await run_prompt(
            "bd.lead_qualification",
            {"name": name, "company": company, "role": role, "info": info},
            role="bd",
        )

    @mcp.tool(
        name="prompt.bd.meeting_notes",
        description="Structure and clean raw meeting notes into an organised summary with action items.",
    )
    async def bd_meeting_notes(raw_notes: str) -> dict[str, Any]:
        from app.services.prompt_service import run_prompt

        logger.info("prompt.bd.meeting_notes_called")
        return await run_prompt(
            "bd.meeting_notes",
            {"raw_notes": raw_notes},
            role="bd",
        )

    # ── Dev convenience shortcuts ─────────────────────────────────────────────

    @mcp.tool(
        name="prompt.dev.spec",
        description="Generate a technical spec from product requirements.",
    )
    async def dev_spec(
        feature_name: str, requirements: str, stack: str = "Python/FastAPI/PostgreSQL"
    ) -> dict[str, Any]:
        from app.services.prompt_service import run_prompt

        logger.info("prompt.dev.spec_called", feature=feature_name)
        return await run_prompt(
            "dev.spec_from_requirements",
            {"feature_name": feature_name, "stack": stack, "requirements": requirements},
            role="dev",
        )

    @mcp.tool(
        name="prompt.dev.bug_triage",
        description="Triage a bug report: root cause, severity, and fix strategy.",
    )
    async def dev_bug_triage(
        title: str,
        description: str,
        steps: str = "",
        expected: str = "",
        actual: str = "",
        environment: str = "production",
    ) -> dict[str, Any]:
        from app.services.prompt_service import run_prompt

        logger.info("prompt.dev.bug_triage_called", title=title)
        return await run_prompt(
            "dev.bug_triage",
            {
                "title": title,
                "description": description,
                "steps": steps or "Not provided",
                "expected": expected or "Not provided",
                "actual": actual or description,
                "environment": environment,
            },
            role="dev",
        )

    @mcp.tool(
        name="prompt.dev.ticket",
        description="Convert an informal bug description into a structured GitHub/JIRA ticket.",
    )
    async def dev_ticket(bug_description: str) -> dict[str, Any]:
        from app.services.prompt_service import run_prompt

        logger.info("prompt.dev.ticket_called")
        return await run_prompt(
            "dev.ticket_from_bug",
            {"bug_description": bug_description},
            role="dev",
        )

    @mcp.tool(
        name="prompt.dev.pr_review",
        description="Generate a PR review summary and checklist.",
    )
    async def dev_pr_review(
        title: str, description: str, diff_summary: str = ""
    ) -> dict[str, Any]:
        from app.services.prompt_service import run_prompt

        logger.info("prompt.dev.pr_review_called", title=title)
        return await run_prompt(
            "dev.pr_review_summary",
            {
                "title": title,
                "description": description,
                "diff_summary": diff_summary or "Not provided",
            },
            role="dev",
        )

    # ── Shared shortcuts ──────────────────────────────────────────────────────

    @mcp.tool(
        name="prompt.summarize",
        description="Summarise any text document into key bullet points.",
    )
    async def shared_summarize(text: str) -> dict[str, Any]:
        from app.services.prompt_service import run_prompt

        logger.info("prompt.summarize_called")
        return await run_prompt("shared.summarize", {"text": text})
