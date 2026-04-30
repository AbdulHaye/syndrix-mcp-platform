from __future__ import annotations

from pydantic import BaseModel, Field

from app.auth.bearer import TeamRole


class PromptDefinition(BaseModel):
    name: str
    description: str
    template: str
    variables: list[str] = Field(default_factory=list)
    required_roles: list[TeamRole] = Field(default_factory=list)


class PromptRegistry:
    def __init__(self) -> None:
        self._prompts: dict[str, PromptDefinition] = {}

    def register(self, prompt_def: PromptDefinition) -> None:
        self._prompts[prompt_def.name] = prompt_def

    def get(self, name: str) -> PromptDefinition | None:
        return self._prompts.get(name)

    def list_for_role(self, role: TeamRole) -> list[PromptDefinition]:
        results: list[PromptDefinition] = []
        for prompt in self._prompts.values():
            if not prompt.required_roles:
                results.append(prompt)
            elif role == TeamRole.ADMIN or role in prompt.required_roles:
                results.append(prompt)
        return results

    def all(self) -> list[PromptDefinition]:
        return list(self._prompts.values())


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
prompt_registry = PromptRegistry()

# ---------------------------------------------------------------------------
# Pre-registered prompts
# ---------------------------------------------------------------------------
prompt_registry.register(PromptDefinition(
    name="discovery_call_summary",
    description="Summarise a discovery call transcript into structured notes.",
    template=(
        "You are an expert BD analyst. Summarise the following discovery call transcript "
        "into: key pain points, proposed solutions, next steps, and decision-maker details.\n\n"
        "Transcript:\n{transcript}\n\n"
        "Client Name: {client_name}\n"
        "Call Date: {call_date}"
    ),
    variables=["transcript", "client_name", "call_date"],
    required_roles=[TeamRole.BD, TeamRole.ADMIN],
))

prompt_registry.register(PromptDefinition(
    name="us_tone_followup",
    description="Draft a US-market follow-up email in a professional, warm tone.",
    template=(
        "Draft a follow-up email to {recipient_name} at {company} after a {meeting_type}. "
        "Tone: professional, warm, US business English. "
        "Key points to mention: {key_points}. "
        "Include a clear call-to-action scheduling a next meeting."
    ),
    variables=["recipient_name", "company", "meeting_type", "key_points"],
    required_roles=[TeamRole.BD, TeamRole.ADMIN],
))

prompt_registry.register(PromptDefinition(
    name="feature_breakdown",
    description="Break a high-level feature request into engineering tasks.",
    template=(
        "You are a senior software architect. Break the following feature request into "
        "discrete engineering tasks suitable for a sprint backlog.\n\n"
        "Feature: {feature_description}\n"
        "Tech Stack: {tech_stack}\n\n"
        "For each task provide: title, description, estimated complexity (S/M/L), "
        "and any dependencies."
    ),
    variables=["feature_description", "tech_stack"],
    required_roles=[TeamRole.DEV, TeamRole.ADMIN],
))

prompt_registry.register(PromptDefinition(
    name="bug_analysis",
    description="Analyse a bug report and suggest root cause and fix approach.",
    template=(
        "You are an expert software engineer performing root-cause analysis.\n\n"
        "Error Message:\n{error_message}\n\n"
        "Stack Trace:\n{stack_trace}\n\n"
        "Context:\n{context}\n\n"
        "Provide: probable root cause, affected components, suggested fix steps, "
        "and any regression risks."
    ),
    variables=["error_message", "stack_trace", "context"],
    required_roles=[TeamRole.DEV, TeamRole.ADMIN],
))

prompt_registry.register(PromptDefinition(
    name="sprint_summary",
    description="Generate an executive sprint summary for management reporting.",
    template=(
        "Generate an executive sprint summary for sprint {sprint_number}.\n\n"
        "Completed items: {completed_items}\n"
        "Incomplete items: {incomplete_items}\n"
        "Blockers: {blockers}\n"
        "Team velocity: {velocity}\n\n"
        "Format as: highlights, metrics, risks, and next sprint priorities. "
        "Keep it concise — suitable for a 2-minute executive read."
    ),
    variables=["sprint_number", "completed_items", "incomplete_items", "blockers", "velocity"],
    required_roles=[TeamRole.MGMT, TeamRole.ADMIN],
))
