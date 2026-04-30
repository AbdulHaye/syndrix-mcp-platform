from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

logger = structlog.get_logger(__name__)


def register_mgmt_tools(mcp: FastMCP) -> None:
    """Register management-facing MCP tools on the provided FastMCP server."""

    @mcp.tool(
        name="report.team.daily",
        description=(
            "Retrieve or generate the daily team activity report. "
            "Returns a placeholder report structure until data sources are wired."
        ),
    )
    async def report_team_daily() -> dict[str, Any]:
        """Returns a structured placeholder daily report."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logger.info("report.team.daily_called", date=today)
        return {
            "report_date": today,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "teams": {
                "bd_team": {
                    "calls_logged": 0,
                    "notes_created": 0,
                    "leads_contacted": 0,
                    "pipeline_updates": 0,
                },
                "dev_team": {
                    "tickets_created": 0,
                    "tickets_closed": 0,
                    "prs_merged": 0,
                    "specs_generated": 0,
                },
                "mgmt_team": {
                    "reports_reviewed": 0,
                    "approvals_given": 0,
                },
            },
            "highlights": [
                "No data connectors wired yet — all values are placeholder zeros.",
                "Connect CRM, ticketing, and GitHub adapters to populate live data.",
            ],
            "source": "mock",
        }

    @mcp.tool(
        name="client.health.score",
        description=(
            "Calculate or retrieve a client health score based on engagement and activity. "
            "Returns a placeholder score until CRM data is connected."
        ),
    )
    async def client_health_score(client_id: str) -> dict[str, Any]:
        """Returns a placeholder client health score."""
        logger.info("client.health.score_called", client_id=client_id)
        return {
            "client_id": client_id,
            "health_score": 0,
            "score_out_of": 100,
            "grade": "N/A",
            "dimensions": {
                "engagement": {"score": 0, "weight": 0.3},
                "payment_timeliness": {"score": 0, "weight": 0.25},
                "support_tickets": {"score": 0, "weight": 0.2},
                "feature_adoption": {"score": 0, "weight": 0.15},
                "nps_feedback": {"score": 0, "weight": 0.1},
            },
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "source": "mock",
            "note": (
                "Client health scoring requires live CRM and product usage data. "
                "Connect the relevant adapters to enable real scoring."
            ),
        }
