from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

logger = structlog.get_logger(__name__)


def register_dev_tools(mcp: FastMCP) -> None:
    """Register developer-facing MCP tools on the provided FastMCP server."""

    @mcp.tool(
        name="repo.list",
        description="List all GitHub repositories for the authenticated user.",
    )
    async def repo_list() -> dict[str, Any]:
        from app.adapters.github import github_adapter

        logger.info("repo.list_called")
        try:
            repos = await github_adapter.list_repos()
            return {"success": True, "repos": repos, "total": len(repos)}
        except Exception as exc:
            logger.error("repo.list_failed", error=str(exc))
            return {"success": False, "repos": [], "error": str(exc)}

    @mcp.tool(
        name="repo.search",
        description="Search GitHub repositories by keyword. Scoped to the configured org if set.",
    )
    async def repo_search(query: str) -> dict[str, Any]:
        from app.adapters.github import github_adapter

        logger.info("repo.search_called", query=query)
        try:
            result = await github_adapter.search_repos(query)
            return {
                "query": query,
                "total_count": result.get("total_count", 0),
                "results": [
                    {
                        "full_name": r["full_name"],
                        "description": r.get("description"),
                        "html_url": r["html_url"],
                        "stars": r.get("stargazers_count", 0),
                        "language": r.get("language"),
                    }
                    for r in result.get("items", [])
                ],
            }
        except Exception as exc:
            logger.error("repo.search_failed", error=str(exc))
            return {"success": False, "query": query, "error": str(exc)}

    @mcp.tool(
        name="ticket.create",
        description="Create a GitHub issue in the specified repository (format: owner/repo or just repo if org is set).",
    )
    async def ticket_create(
        repo: str,
        title: str,
        description: str,
        priority: str = "medium",
    ) -> dict[str, Any]:
        from app.adapters.github import github_adapter

        logger.info("ticket.create_called", repo=repo, title=title, priority=priority)
        body = f"**Priority:** {priority}\n\n{description}"
        try:
            return await github_adapter.create_issue(repo=repo, title=title, body=body)
        except Exception as exc:
            logger.error("ticket.create_failed", error=str(exc))
            return {"success": False, "title": title, "error": str(exc)}

    @mcp.tool(
        name="ticket.pr_list",
        description="List open pull requests for a GitHub repository (format: owner/repo or just repo if org is set).",
    )
    async def ticket_pr_list(repo: str) -> dict[str, Any]:
        from app.adapters.github import github_adapter

        logger.info("ticket.pr_list_called", repo=repo)
        try:
            return await github_adapter.get_pr_list(repo)
        except Exception as exc:
            logger.error("ticket.pr_list_failed", error=str(exc))
            return {"success": False, "repo": repo, "error": str(exc)}

    @mcp.tool(
        name="spec.generate",
        description=(
            "Generate a technical specification document from a feature description "
            "using the local LLM."
        ),
    )
    async def spec_generate(feature_description: str) -> dict[str, Any]:
        from app.services.model_gateway import model_gateway

        logger.info("spec.generate_called", feature_len=len(feature_description))

        system_prompt = (
            "You are a software architect. Given a feature description, write a concise "
            "technical spec with these sections: Overview, API Design, Data Model, "
            "Edge Cases, Acceptance Criteria. Use markdown. Be brief and direct."
        )
        try:
            spec_text = await model_gateway.generate(
                prompt=f"Feature: {feature_description}",
                system=system_prompt,
                model=model_gateway.route_model("code"),
                num_predict=600,
            )
            return {
                "success": True,
                "feature_description": feature_description[:200],
                "specification": spec_text,
                "model_used": model_gateway.route_model("code"),
            }
        except Exception as exc:
            logger.error("spec.generate_failed", error=str(exc))
            return {
                "success": False,
                "feature_description": feature_description[:200],
                "specification": None,
                "error": str(exc),
            }

    @mcp.tool(
        name="bug.triage",
        description=(
            "Analyse an error message and context, then suggest a root-cause triage path "
            "using the local LLM."
        ),
    )
    async def bug_triage(error_message: str, context: str = "") -> dict[str, Any]:
        from app.services.model_gateway import model_gateway

        logger.info("bug.triage_called", error_len=len(error_message))

        system_prompt = (
            "You are a software engineer doing root-cause analysis. "
            "Given an error, respond with: Root Cause, Fix, Regression Risks. "
            "Use markdown. Be concise."
        )
        context_section = f"\nContext: {context}" if context.strip() else ""
        try:
            analysis = await model_gateway.generate(
                prompt=f"Error: {error_message}{context_section}",
                system=system_prompt,
                model=model_gateway.route_model("analysis"),
                num_predict=400,
            )
            return {
                "success": True,
                "error_message_preview": error_message[:200],
                "triage_analysis": analysis,
                "model_used": model_gateway.route_model("analysis"),
            }
        except Exception as exc:
            logger.error("bug.triage_failed", error=str(exc))
            return {
                "success": False,
                "error_message_preview": error_message[:200],
                "triage_analysis": None,
                "error": str(exc),
            }

    @mcp.tool(
        name="slack.message.send",
        description="Send a message to a Slack channel or DM.",
    )
    async def slack_message_send(channel: str, text: str) -> dict[str, Any]:
        from app.adapters.slack import slack_adapter

        logger.info("slack.message.send_called", channel=channel)
        try:
            return await slack_adapter.send_message(channel, text)
        except Exception as exc:
            logger.error("slack.message.send_failed", error=str(exc))
            return {"success": False, "channel": channel, "error": str(exc)}

    @mcp.tool(
        name="slack.channel.history",
        description="Retrieve recent messages from a Slack channel.",
    )
    async def slack_channel_history(channel: str, limit: int = 50) -> dict[str, Any]:
        from app.adapters.slack import slack_adapter

        logger.info("slack.channel.history_called", channel=channel, limit=limit)
        try:
            return await slack_adapter.get_channel_history(channel, limit)
        except Exception as exc:
            logger.error("slack.channel.history_failed", error=str(exc))
            return {"success": False, "channel": channel, "error": str(exc)}
