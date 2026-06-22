from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Prompt template data model
# ---------------------------------------------------------------------------

@dataclass
class PromptTemplate:
    key: str
    title: str
    description: str
    category: str          # "bd" | "dev" | "shared"
    roles: list[str]       # roles allowed to use this template
    system_prompt: str
    user_template: str     # supports {variable} placeholders
    variables: list[str] = field(default_factory=list)  # required variable names
    model_task: str = "chat"  # passed to model_gateway.route_model()


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, PromptTemplate] = {}


def _register(t: PromptTemplate) -> None:
    _TEMPLATES[t.key] = t


# ── BD templates ─────────────────────────────────────────────────────────────

_register(PromptTemplate(
    key="bd.crm_summary",
    title="CRM Contact Summary",
    description="Generate a concise AI summary of a CRM contact record including key details and talking points.",
    category="bd",
    roles=["bd", "admin"],
    system_prompt=(
        "You are a business development assistant. Your job is to turn raw CRM contact "
        "data into a concise, actionable profile that a salesperson can read in 30 seconds. "
        "Include: who they are, their role/company, recent interactions, key talking points, "
        "and a recommended next action. Be factual — only include what is in the data."
    ),
    user_template="Summarise the following CRM contact record:\n\n{contact_data}",
    variables=["contact_data"],
    model_task="summary",
))

_register(PromptTemplate(
    key="bd.followup_draft",
    title="Follow-up Message Draft",
    description="Draft a professional, personalised follow-up email or message for a prospect.",
    category="bd",
    roles=["bd", "admin"],
    system_prompt=(
        "You are a BD specialist writing on behalf of 360 Synergy Tech. "
        "Write professional, warm, and concise follow-up messages that feel personal rather than templated. "
        "Match the tone to the channel (email is slightly more formal; SMS/WhatsApp is conversational). "
        "Keep emails under 150 words. Keep SMS/WhatsApp under 60 words. "
        "Do NOT use generic filler phrases like 'hope this finds you well'."
    ),
    user_template=(
        "Write a {channel} follow-up for the following prospect.\n\n"
        "Contact: {contact_name}\n"
        "Company: {company}\n"
        "Context / last interaction: {context}\n"
        "Goal of this message: {goal}"
    ),
    variables=["channel", "contact_name", "company", "context", "goal"],
    model_task="chat",
))

_register(PromptTemplate(
    key="bd.lead_qualification",
    title="Lead Qualification",
    description="Assess and score a lead based on provided information using BANT criteria.",
    category="bd",
    roles=["bd", "admin"],
    system_prompt=(
        "You are a sales qualification expert. Evaluate leads using BANT (Budget, Authority, "
        "Need, Timeline). Score each dimension 0–10 and give an overall score with a "
        "recommended action: 'pursue', 'nurture', or 'disqualify'. Be direct and concrete."
    ),
    user_template=(
        "Qualify the following lead:\n\n"
        "Name: {name}\n"
        "Company: {company}\n"
        "Role: {role}\n"
        "Information provided: {info}"
    ),
    variables=["name", "company", "role", "info"],
    model_task="analysis",
))

_register(PromptTemplate(
    key="bd.meeting_notes",
    title="Meeting Notes Cleanup",
    description="Structure and clean up raw meeting notes into an organised summary with action items.",
    category="bd",
    roles=["bd", "mgmt", "admin"],
    system_prompt=(
        "You are an executive assistant. Take raw meeting notes and produce a clean, structured summary. "
        "Format: Attendees, Key Discussion Points (bullet list), Decisions Made, Action Items (with owner + deadline if mentioned). "
        "Do not add information that was not in the original notes."
    ),
    user_template="Clean and structure the following meeting notes:\n\n{raw_notes}",
    variables=["raw_notes"],
    model_task="summary",
))

# ── Dev templates ─────────────────────────────────────────────────────────────

_register(PromptTemplate(
    key="dev.spec_from_requirements",
    title="Technical Spec from Requirements",
    description="Generate a detailed technical specification from product requirements or user stories.",
    category="dev",
    roles=["dev", "admin"],
    system_prompt=(
        "You are a senior software architect. Given product requirements, generate a structured "
        "technical specification document. Include: Overview, Goals & Non-Goals, "
        "Architecture/Design, API contracts (if applicable), Data model changes, "
        "Implementation steps, Testing strategy, Risks & mitigations. "
        "Be specific and implementation-ready. Use markdown formatting."
    ),
    user_template=(
        "Generate a technical spec for the following requirements:\n\n"
        "Feature: {feature_name}\n"
        "Stack: {stack}\n"
        "Requirements:\n{requirements}"
    ),
    variables=["feature_name", "stack", "requirements"],
    model_task="code",
))

_register(PromptTemplate(
    key="dev.bug_triage",
    title="Bug Triage",
    description="Analyse a bug report and suggest root cause, severity, and fix strategy.",
    category="dev",
    roles=["dev", "admin"],
    system_prompt=(
        "You are a senior engineer performing bug triage. Given a bug report, provide: "
        "1) Root cause hypothesis (most likely cause based on the symptoms), "
        "2) Severity (critical/high/medium/low with reasoning), "
        "3) Affected components, "
        "4) Suggested investigation steps, "
        "5) Proposed fix strategy. Be concise and practical."
    ),
    user_template=(
        "Triage the following bug report:\n\n"
        "Title: {title}\n"
        "Description: {description}\n"
        "Steps to reproduce: {steps}\n"
        "Expected: {expected}\n"
        "Actual: {actual}\n"
        "Environment: {environment}"
    ),
    variables=["title", "description", "steps", "expected", "actual", "environment"],
    model_task="analysis",
))

_register(PromptTemplate(
    key="dev.ticket_from_bug",
    title="Ticket from Bug Report",
    description="Convert an informal bug description into a well-structured JIRA/GitHub ticket.",
    category="dev",
    roles=["dev", "admin"],
    system_prompt=(
        "You are a tech lead writing GitHub or JIRA issues. "
        "Convert informal bug descriptions into professional, complete tickets. "
        "Format: Title (short, descriptive), Type (Bug/Feature/Chore), "
        "Summary (2 sentences max), Steps to Reproduce, Expected vs Actual Behaviour, "
        "Acceptance Criteria, Technical Notes (optional). "
        "Use markdown. Be concise."
    ),
    user_template=(
        "Convert this bug description into a structured ticket:\n\n{bug_description}"
    ),
    variables=["bug_description"],
    model_task="code",
))

_register(PromptTemplate(
    key="dev.pr_review_summary",
    title="PR Review Summary",
    description="Generate a concise summary and review checklist for a pull request.",
    category="dev",
    roles=["dev", "admin"],
    system_prompt=(
        "You are a senior code reviewer. Given a PR title, description and diff summary, "
        "produce a review summary covering: What this PR does, Risk assessment (low/medium/high), "
        "Key areas to review, Potential issues spotted, and a review checklist. "
        "Be specific and constructive."
    ),
    user_template=(
        "Review the following pull request:\n\n"
        "Title: {title}\n"
        "Description: {description}\n"
        "Changed files / summary: {diff_summary}"
    ),
    variables=["title", "description", "diff_summary"],
    model_task="code",
))

# ── Shared templates ──────────────────────────────────────────────────────────

_register(PromptTemplate(
    key="shared.summarize",
    title="Summarise Text",
    description="Produce a concise summary of any text document.",
    category="shared",
    roles=["bd", "dev", "mgmt", "admin"],
    system_prompt=(
        "You are a professional summariser. Condense the provided text into a clear, "
        "accurate summary. Preserve key facts and decisions. "
        "Target length: 3–5 bullet points or 2–3 short paragraphs depending on content."
    ),
    user_template="Summarise the following text:\n\n{text}",
    variables=["text"],
    model_task="summary",
))

_register(PromptTemplate(
    key="shared.rag_answer",
    title="Knowledge Base Q&A",
    description="Answer a question using retrieved knowledge base context.",
    category="shared",
    roles=["bd", "dev", "mgmt", "admin"],
    system_prompt=(
        "You are a knowledgeable assistant for 360 Synergy Tech. "
        "Answer the user's question using ONLY the provided context. "
        "If the answer is not in the context, say so clearly. "
        "Cite the source title when quoting specific facts."
    ),
    user_template=(
        "Question: {question}\n\n"
        "Context from knowledge base:\n{context}"
    ),
    variables=["question", "context"],
    model_task="chat",
))


# ---------------------------------------------------------------------------
# Service API
# ---------------------------------------------------------------------------

def list_templates(role: str | None = None) -> list[dict[str, Any]]:
    """Return all templates visible to a role (or all if role is None)."""
    result = []
    for t in _TEMPLATES.values():
        if role is None or role in t.roles or role == "admin":
            result.append({
                "key": t.key,
                "title": t.title,
                "description": t.description,
                "category": t.category,
                "variables": t.variables,
            })
    return result


def get_template(key: str) -> PromptTemplate | None:
    return _TEMPLATES.get(key)


def _fill_template(template: str, variables: dict[str, str]) -> str:
    """Replace {variable} placeholders with provided values."""
    def replacer(match: re.Match) -> str:  # type: ignore[type-arg]
        var = match.group(1)
        return variables.get(var, match.group(0))
    return re.sub(r"\{(\w+)\}", replacer, template)


async def run_prompt(
    key: str,
    variables: dict[str, str],
    role: str | None = None,
) -> dict[str, Any]:
    """
    Render and execute a prompt template.
    Returns {"success": True, "output": "...", "key": key, ...}
    """
    from app.services.model_gateway import model_gateway

    template = get_template(key)
    if template is None:
        return {"success": False, "error": f"Unknown prompt key: {key}"}

    if role and role not in template.roles and role != "admin":
        return {"success": False, "error": f"Role '{role}' cannot use prompt '{key}'"}

    missing = [v for v in template.variables if v not in variables]
    if missing:
        return {
            "success": False,
            "error": f"Missing required variables: {', '.join(missing)}",
            "required": template.variables,
        }

    user_message = _fill_template(template.user_template, variables)

    logger.info("prompt_run_started", key=key, role=role)
    try:
        model = model_gateway.route_model(template.model_task)
        output = await model_gateway.generate(
            prompt=user_message,
            model=model,
            system=template.system_prompt,
        )
        logger.info("prompt_run_completed", key=key)
        return {
            "success": True,
            "key": key,
            "title": template.title,
            "output": output,
            "model": model,
        }
    except Exception as exc:
        logger.error("prompt_run_failed", key=key, error=str(exc))
        return {"success": False, "key": key, "error": str(exc)}
