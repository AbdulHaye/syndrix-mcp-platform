from __future__ import annotations

from collections import Counter, defaultdict
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
            "Generate the daily team activity report from the live audit log. "
            "Shows tool invocations, success/failure rates and top tools per team."
        ),
    )
    async def report_team_daily() -> dict[str, Any]:
        from app.services.audit import audit_service

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logger.info("report.team.daily_called", date=today)

        entries = await audit_service.recent(limit=1000)

        # ── aggregate by team + tool prefix ──────────────────────────────────
        team_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "invocations": 0,
            "successes": 0,
            "failures": 0,
            "tools": Counter(),
            "latency_ms": [],
        })

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_entries = [
            e for e in entries
            if e.timestamp.strftime("%Y-%m-%d") == today_str
        ]

        for entry in today_entries:
            stats = team_stats[entry.team_name]
            stats["invocations"] += 1
            if entry.success:
                stats["successes"] += 1
            else:
                stats["failures"] += 1
            stats["tools"][entry.tool_name] += 1
            stats["latency_ms"].append(entry.latency_ms)

        # ── format output ─────────────────────────────────────────────────────
        formatted_teams: dict[str, Any] = {}
        for team, stats in team_stats.items():
            lats = stats["latency_ms"]
            formatted_teams[team] = {
                "invocations": stats["invocations"],
                "successes": stats["successes"],
                "failures": stats["failures"],
                "error_rate": round(
                    stats["failures"] / stats["invocations"] * 100, 1
                ) if stats["invocations"] else 0.0,
                "avg_latency_ms": round(sum(lats) / len(lats), 1) if lats else 0.0,
                "top_tools": dict(stats["tools"].most_common(5)),
            }

        # totals
        total_invocations = sum(t["invocations"] for t in formatted_teams.values())
        total_failures = sum(t["failures"] for t in formatted_teams.values())

        source = "live" if today_entries else "no_data"

        return {
            "report_date": today,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "totals": {
                "invocations": total_invocations,
                "failures": total_failures,
                "active_teams": len(formatted_teams),
            },
            "teams": formatted_teams,
            "highlights": _build_highlights(formatted_teams, total_invocations),
        }

    @mcp.tool(
        name="client.health.score",
        description=(
            "Calculate a client health score based on CRM engagement and recent interactions. "
            "Returns a scored profile with dimension breakdown."
        ),
    )
    async def client_health_score(client_id: str) -> dict[str, Any]:
        from app.adapters.podio import podio_adapter

        logger.info("client.health.score_called", client_id=client_id)

        try:
            contact = await podio_adapter.get_contact(client_id)
        except Exception as exc:
            logger.warning("client_health_no_contact", client_id=client_id, error=str(exc))
            contact = {}

        # Scoring heuristics based on available CRM fields
        score_engagement = _score_engagement(contact)
        score_recency = _score_recency(contact)
        score_data_completeness = _score_completeness(contact)

        dimensions = {
            "engagement":         {"score": score_engagement,       "weight": 0.40},
            "data_recency":       {"score": score_recency,          "weight": 0.35},
            "data_completeness":  {"score": score_data_completeness,"weight": 0.25},
        }
        overall = round(
            sum(d["score"] * d["weight"] for d in dimensions.values()), 1
        )
        grade = "A" if overall >= 80 else "B" if overall >= 60 else "C" if overall >= 40 else "D"

        return {
            "client_id": client_id,
            "health_score": overall,
            "score_out_of": 100,
            "grade": grade,
            "dimensions": dimensions,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "source": "live" if contact else "no_data",
            "contact_snapshot": {
                k: contact.get(k) for k in ("name", "company", "email") if contact.get(k)
            },
        }

    @mcp.tool(
        name="report.tool.usage",
        description="Return tool usage statistics over the last N audit entries.",
    )
    async def report_tool_usage(limit: int = 500) -> dict[str, Any]:
        from app.services.audit import audit_service

        logger.info("report.tool.usage_called", limit=limit)
        entries = await audit_service.recent(limit=limit)
        tool_counter: Counter[str] = Counter()
        team_counter: Counter[str] = Counter()
        error_counter: Counter[str] = Counter()

        for e in entries:
            tool_counter[e.tool_name] += 1
            team_counter[e.team_name] += 1
            if not e.success:
                error_counter[e.tool_name] += 1

        return {
            "success": True,
            "entries_analysed": len(entries),
            "top_tools": dict(tool_counter.most_common(10)),
            "top_teams": dict(team_counter.most_common(10)),
            "top_error_tools": dict(error_counter.most_common(5)),
        }


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _score_engagement(contact: dict[str, Any]) -> float:
    score = 0.0
    if contact.get("last_note"):
        score += 40
    if contact.get("email"):
        score += 20
    if contact.get("phone"):
        score += 20
    if contact.get("company"):
        score += 20
    return min(score, 100.0)


def _score_recency(contact: dict[str, Any]) -> float:
    raw = contact.get("last_edit_on") or contact.get("updated_at") or contact.get("created_on")
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - dt).days
        if age_days <= 7:
            return 100.0
        if age_days <= 30:
            return 70.0
        if age_days <= 90:
            return 40.0
        return 10.0
    except Exception:
        return 0.0


def _score_completeness(contact: dict[str, Any]) -> float:
    fields = ["name", "email", "phone", "company", "title"]
    filled = sum(1 for f in fields if contact.get(f))
    return round(filled / len(fields) * 100, 1)


def _build_highlights(teams: dict[str, Any], total: int) -> list[str]:
    highlights: list[str] = []
    if total == 0:
        highlights.append("No tool invocations recorded today yet.")
        return highlights
    highlights.append(f"{total} tool invocations recorded today across {len(teams)} teams.")
    for team, stats in teams.items():
        if stats["error_rate"] > 20:
            highlights.append(
                f"{team} has a high error rate ({stats['error_rate']}%) — investigate failures."
            )
        if stats["invocations"] > 0:
            top = list(stats["top_tools"].keys())[:1]
            if top:
                highlights.append(f"{team}'s most-used tool: {top[0]}.")
    return highlights
