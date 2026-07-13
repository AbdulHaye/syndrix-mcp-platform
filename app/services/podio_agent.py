from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import structlog

from app.services.model_gateway import model_gateway
from app.services.podio_mcp import podio_mcp
from app.services.podio_files_client import podio_files

logger = structlog.get_logger(__name__)

# Max tool-execution rounds (allow pagination + per-item file listing).
_MAX_STEPS = 25
# Truncate very large tool outputs so they don't blow the context window.
_MAX_TOOL_OUTPUT_CHARS = 6000
# Cap generated tokens per step — enough for multi-step tool selection reasoning.
_NUM_PREDICT = 1200
# More tokens for the final prose reply (listing fields, summarising results).
_NUM_PREDICT_FINAL = 1500

# ── Ollama-specific tool limiting ──────────────────────────────────────────────
# llama3.2 (3B) hallucinates when given 40+ tool schemas. We keep only the tools
# most likely needed for the current request, always reserving slots for the core
# discovery tools.  Cloud models (Groq, Gemini, etc.) get the full set.

_OLLAMA_MAX_TOOLS = 18

_OLLAMA_TOOL_ALWAYS = {
    "get_items", "get_item", "get_app", "get_apps_in_space",
    "get_spaces_in_organization", "get_organizations", "search_globally",
}

# keyword → priority tool names added on top of the always-set
_OLLAMA_TOOL_PRIORITY: dict[str, set[str]] = {
    "flow":        {"get_app_flows", "get_flow", "get_flow_context",
                    "get_flow_effect_attributes", "get_flow_possible_attributes",
                    "create_flow", "update_flow", "delete_flow"},
    "workflow":    {"get_app_flows", "get_flow", "create_flow", "update_flow", "delete_flow"},
    "automation":  {"get_app_flows", "get_flow", "create_flow"},
    "webhook":     {"list_webhooks", "create_webhook", "delete_webhook",
                    "request_webhook_verification", "validate_webhook_verification"},
    "task":        {"get_tasks", "get_app_tasks", "create_task", "update_task", "complete_task",
                    "delete_task", "reassign_task", "uncomplete_task", "get_reference_tasks"},
    "calendar":    {"get_calendar", "get_space_calendar", "get_app_calendar",
                    "list_linked_accounts", "get_linked_account_calendar"},
    "recent":      {"get_activity_stream", "get_apps_in_space", "get_items"},
    "today":       {"get_activity_stream", "get_apps_in_space", "get_items"},
    "activity":    {"get_activity_stream"},
    "latest":      {"get_activity_stream", "get_items"},
    "reminder":    {"get_reminder", "set_reminder", "delete_reminder"},
    "recurrence":  {"get_recurrence", "set_recurrence", "delete_recurrence"},
    "conversation":{"list_conversations", "get_conversation",
                    "create_conversation", "reply_to_conversation"},
    "workspace":   {"create_workspace", "update_workspace", "archive_workspace",
                    "restore_workspace", "delete_workspace",
                    "invite_workspace_member", "remove_workspace_member",
                    "update_workspace_member_role"},
    "member":      {"get_space_members", "get_org_members",
                    "invite_workspace_member", "remove_workspace_member",
                    "update_workspace_member_role"},
    "file":        {"get_item_files", "download_file", "attach_file_to_item",
                    "attach_file", "delete_file", "set_item_image"},
    "export":      {"export_app_xlsx"},
    "clone":       {"clone_item"},
    "revision":    {"get_item_revisions", "revert_item_revision"},
    "history":     {"get_item_revisions"},
    "reference":   {"get_item_references"},
    "bulk":        {"bulk_delete_items"},
    "delete":      {"delete_item", "bulk_delete_items", "delete_task"},
    "create":      {"create_item", "create_task"},
    "update":      {"update_item", "update_task", "update_item_field"},
    "comment":     {"get_item_comments", "add_comment"},
    "note":        {"add_comment"},
    "search":      {"search_globally"},
}


def _filter_tools_for_ollama(
    tool_specs: list[dict[str, Any]], message: str
) -> list[dict[str, Any]]:
    """Cap the tool list for small local models.

    Always includes the core discovery + CRUD tools.  Adds domain-specific
    tools matched by keywords in the user message.  Hard-caps at
    ``_OLLAMA_MAX_TOOLS`` so llama3.2 can reason over the schema without
    hallucinating.
    """
    msg_lower = (message or "").lower()
    priority: set[str] = set(_OLLAMA_TOOL_ALWAYS)
    for kw, names in _OLLAMA_TOOL_PRIORITY.items():
        if kw in msg_lower:
            priority.update(names)

    prioritised = [t for t in tool_specs if t["function"]["name"] in priority]
    rest = [t for t in tool_specs if t["function"]["name"] not in priority]
    return (prioritised + rest)[:_OLLAMA_MAX_TOOLS]

# The Podio MCP tools the agent exposes (real names on mcp.podio.com).
_CORE_TOOLS = {
    "get_organizations",
    "get_spaces_in_organization",
    "get_apps_in_space",
    "get_app",
    "get_app_summary",
    "get_items",          # list/filter items in an app (the prompt's "filter_items")
    "get_item",
    "search_globally",
    "get_item_comments",
    "get_tasks",
    "get_notifications",
    "get_space_members",
    "get_org_members",
    # write tools (gated by _wants_write)
    "create_item",
    "update_item",
    "add_comment",
    "create_task",
    "update_task",
    "complete_task",
}

# Destructive/write tools — only exposed when the user's message implies a change.
_WRITE_TOOLS = {
    "create_item", "update_item", "add_comment",
    "create_task", "update_task", "complete_task",
}
_WRITE_WORDS = (
    "create", "update", "modify", "change", "delete", "remove", "rename",
    "assign", "complete", "comment", "note", "task",
    "add ", "new ", "set ", "mark ", "move ", "edit ",
    "attach", "upload",
    # extended domains
    "webhook", "hook", "flow", "workflow", "automation",
    "conversation", "reply",
    "invite", "archive", "restore",
    "clone", "duplicate",
    "revert", "revision",
    "label", "export", "bulk",
    "reassign", "uncomplete", "rank",
    "recur", "remind",
)

# Read-only tools from the custom REST MCP server — exposed when read intent is detected
# even when no write intent is present.
_FILES_READ_ONLY = {
    # File reads
    "download_file", "get_item_files",
    # Item history / graph
    "get_item_revisions", "get_item_references", "get_items_by_view",
    # Task reads
    "get_reference_tasks", "get_app_tasks",
    "get_task_labels", "get_task_summary", "get_task_count",
    # Flow reads
    "get_app_flows", "get_flow", "get_flow_context",
    "get_flow_effect_attributes", "get_flow_possible_attributes",
    # Webhook reads
    "list_webhooks",
    # Conversation reads
    "list_conversations", "get_conversation",
    # Calendar (native + externally added / linked-account calendars)
    "get_calendar", "get_space_calendar", "get_app_calendar",
    "list_linked_accounts", "get_linked_account_calendar",
    # Recent activity across the whole workspace (items/comments/files, newest first)
    "get_activity_stream",
    # Reminder / recurrence reads
    "get_reminder", "get_recurrence",
    # Non-destructive operations
    "export_app_xlsx",
    # NOTE: clone_item is intentionally NOT here. Cloning creates a new record and
    # must only be exposed on explicit clone/duplicate intent (see _wants_clone) —
    # otherwise the model reaches for it as a workaround when create_item fails.
}

# Explicit clone/duplicate intent — clone_item is only exposed when the user
# actually asks to clone/copy a record, never as a create_item fallback.
_CLONE_WORDS = ("clone", "duplicate", "make a copy", "copy of", "copy the")


def _wants_clone(message: str) -> bool:
    m = (message or "").lower()
    return any(w in m for w in _CLONE_WORDS)


# Tools whose schemas include 'space_id' but Podio's API REJECTS it at runtime.
_SPACE_ID_BLOCKED = {
    "get_task_summary", "get_task_count", "get_calendar",
}

# Tools that use 'space' (not 'space_id') as their workspace filter parameter.
# get_tasks requires at least one filter; we auto-inject the active workspace.
_SPACE_PARAM_TOOLS = {"get_tasks", "get_task_summary", "get_task_count"}


_REPEAT_RUN_RE = re.compile(r"([^\w\s])\1{14,}")  # e.g. ".............." "----------------"


def _is_garbage_reply(text: str) -> bool:
    """True when the model emits noise instead of a real reply: bracket/pipe soup,
    or a degenerate run of a repeated punctuation char (e.g. a long row of dots or
    dashes the model spews when its generation collapses)."""
    s = (text or "").strip()
    if not s or len(s) < 15:
        return False
    # A long run of the same punctuation char (........  --------  ~~~~~~~~) is garbage.
    if _REPEAT_RUN_RE.search(s):
        return True
    noise = sum(1 for c in s if c in "{}[]|\\/ \n\t")
    if noise / len(s) > 0.65:
        return True
    # Any single non-alphanumeric char dominating the reply (e.g. dots) is garbage.
    punct = sum(1 for c in s if not c.isalnum() and not c.isspace())
    return punct / len(s) > 0.6


_HALLUCINATED_SUCCESS_PHRASES = (
    "successfully updated", "has been updated", "successfully created",
    "has been created", "successfully deleted", "has been deleted",
    "successfully completed", "has been completed", "successfully added",
    "has been added", "successfully set", "title has been",
    "item has been", "record has been", "task has been",
    "i have updated", "i've updated", "i have created", "i've created",
    "i have deleted", "i've deleted", "i have completed", "i've completed",
    # broader patterns models use when hallucinating multi-step results
    "comment added", "task created", "note added", "created and linked",
    "added to the item", "linked to the item", "have been added",
    "have been created", "comment has been", "task has been created",
    "review added", "assigned to you",
    # file attach / image — the model claimed an attach/set that never ran or errored
    "attached successfully", "successfully attached", "has been attached",
    "have been attached", "attached the file", "attached to the item",
    "file attached", "file has been", "image set", "image has been set",
    "set as the item", "successfully uploaded", "upload successful",
    # flow / automation replace + activate claims (delete+recreate scenarios)
    "successfully replaced", "has been replaced", "successfully saved",
    "now active", "is active", "status: active", "flow created",
    "automation created", "flow has been", "automation has been",
    "automation is active", "flow is active",
)

# Write tools that CREATE/attach something new. A "created / replaced / now active"
# claim must be backed by one of these SUCCEEDING — a successful delete_flow (e.g. a
# delete+recreate where the recreate failed, leaving nothing) does NOT justify it.
_CREATE_WRITE_TOOLS = {
    "create_item", "create_task", "create_flow", "create_webhook", "clone_item",
    "update_item", "update_task", "update_flow", "update_item_field",
    "attach_file_to_item", "set_item_image", "add_comment",
}
_CREATE_SUCCESS_PHRASES = (
    "successfully created", "has been created", "successfully replaced",
    "has been replaced", "now active", "is active", "status: active",
    "task created", "task has been created", "flow created", "automation created",
    "automation is active", "flow is active", "successfully saved", "created and",
    "successfully attached", "has been attached", "attached successfully",
)

_WRITE_TOOL_NAMES = {
    "create_item", "update_item", "delete_item", "add_comment",
    "create_task", "update_task", "complete_task", "delete_task",
    "attach_file_to_item", "set_item_image", "delete_file",
    "create_flow", "update_flow", "delete_flow",
    "create_webhook", "delete_webhook",
    "clone_item", "bulk_delete_items", "update_item_field", "revert_item_revision",
}


_TEMPLATE_VAR_RE = re.compile(r"\{[a-z_]{2,}\}")  # e.g. {item_id}, {task_id}, {item_title}
# Matches tool calls written as plain text: get_items":{ / get_items({ / get_items: {
# and the GLUED, separator-less form get_items{"app_id":...} — the '{' immediately
# followed by a quoted JSON key is the strong signal (avoids matching prose braces).
_TEXT_TOOL_CALL_RE = re.compile(r'\b[a-z][a-z0-9_]{2,}["\']?\s*[:(]?\s*\{\s*["\']')


def _has_template_vars(text: str) -> bool:
    """True if the model left unfilled template placeholders in its reply."""
    return bool(_TEMPLATE_VAR_RE.search(text or ""))


def _has_text_tool_call(text: str) -> bool:
    """True when the model serialised a tool call as plain text instead of using
    the structured function-calling format (e.g. 'get_items\":{"app_id":...')."""
    return bool(_TEXT_TOOL_CALL_RE.search(text or ""))


def _has_non_ascii_garbage(text: str) -> bool:
    """True if the reply contains a significant amount of non-ASCII characters
    (e.g. Arabic, CJK) mixed into what should be an English response."""
    s = (text or "").strip()
    if not s:
        return False
    non_ascii = sum(1 for c in s if ord(c) > 127)
    return non_ascii / len(s) > 0.1  # >10% non-ASCII is almost certainly garbage


def _step_errored(step: dict) -> bool:
    """True when a recorded tool step did NOT succeed. Covers every failure shape
    the tool layers produce: hosted-MCP {"isError": True}, our REST wrappers'
    {"success": False} / {"error": ...}, and raised exceptions captured as
    {"isError": True, "content": "..."}."""
    res = step.get("result")
    if not isinstance(res, dict):
        return False
    if res.get("isError") or res.get("error") is not None or res.get("success") is False:
        return True
    return False


def _successful_write_tools(steps: list[dict]) -> list[str]:
    """Names of write tools that actually SUCCEEDED this turn."""
    return [
        s.get("tool")
        for s in steps
        if s.get("tool") in _WRITE_TOOL_NAMES and not _step_errored(s)
    ]


def _is_hallucinated_write(text: str, steps: list[dict]) -> bool:
    """True when the reply claims a write/attach/set/delete SUCCEEDED but no write
    tool completed successfully this turn — i.e. none was called, or every one that
    was called returned an error. A write tool that ERRORED does NOT count as done:
    the model must report the failure, never claim success on a failed call.

    Special case (delete+recreate): a "created / replaced / now active" claim must be
    backed by a CREATE-type write succeeding. A successful delete_flow alone — with the
    recreate erroring, leaving nothing behind — is NOT success and must not pass."""
    t = (text or "").lower()
    claims_create = any(p in t for p in _CREATE_SUCCESS_PHRASES)
    if claims_create:
        created_ok = any(s.get("tool") in _CREATE_WRITE_TOOLS and not _step_errored(s) for s in steps)
        create_errored = any(s.get("tool") in _CREATE_WRITE_TOOLS and _step_errored(s) for s in steps)
        # A create-type write ERRORED this turn and none succeeded, yet the reply claims
        # a create/replace/activate succeeded — the delete+recreate-failed trap. Flag it
        # even if an unrelated delete succeeded this turn.
        if create_errored and not created_ok:
            return True
    claims_success = any(p in t for p in _HALLUCINATED_SUCCESS_PHRASES)
    if not claims_success:
        return False
    return not _successful_write_tools(steps)


def _wants_write(message: str) -> bool:
    m = (message or "").lower()
    return any(w in m for w in _WRITE_WORDS)


# ── get_app field-schema compaction ────────────────────────────────────────────
# Podio's hosted get_app returns a large, deeply-nested app object. For a 16-field
# app the raw payload easily exceeds _MAX_TOOL_OUTPUT_CHARS and gets truncated,
# leaving the model without the external_ids / types / required flags it needs to
# build a valid create_item payload (root cause of "No field found" on guessed
# external_ids and "must be Range" on an omitted required date field). We replace
# the get_app result the model sees with a compact, COMPLETE field schema.

def _find_app_fields(data: Any) -> list[dict[str, Any]]:
    """Locate the Podio app ``fields`` list anywhere in a get_app result, regardless
    of nesting (root, under 'app', inside structuredContent, etc.)."""
    def _is_field_list(x: Any) -> bool:
        return isinstance(x, list) and any(
            isinstance(e, dict) and "external_id" in e and "type" in e for e in x
        )

    if isinstance(data, dict):
        if _is_field_list(data.get("fields")):
            return [e for e in data["fields"] if isinstance(e, dict)]
        for v in data.values():
            found = _find_app_fields(v)
            if found:
                return found
    elif isinstance(data, list):
        if _is_field_list(data):
            return [e for e in data if isinstance(e, dict)]
        for v in data:
            found = _find_app_fields(v)
            if found:
                return found
    return []


def _compact_field(f: dict[str, Any]) -> dict[str, Any]:
    cfg = f.get("config") or {}
    settings = cfg.get("settings") or {}
    out: dict[str, Any] = {
        "external_id": f.get("external_id"),
        "field_id": f.get("field_id"),
        "type": f.get("type"),
        "label": f.get("label") or cfg.get("label"),
        "required": bool(cfg.get("required")),
    }
    opts = settings.get("options")
    if isinstance(opts, list):
        out["options"] = [
            {"id": o.get("id"), "text": o.get("text")}
            for o in opts
            if isinstance(o, dict) and o.get("status") != "deleted"
        ]
    return out


# Per-type input hint shown to the USER in the create/update form.
_FIELD_TYPE_HINT: dict[str, str] = {
    "text": "text",
    "number": "number",
    "money": "amount + currency, e.g. 999.99 USD",
    "date": "date (YYYY-MM-DD), optionally with a time",
    "duration": "duration",
    "phone": "phone number",
    "email": "email address",
    "contact": "person — give a name (I'll match it)",
    "member": "person — give a name (I'll match it)",
    "app": "linked record — give the record's name (I'll match it)",
    "location": "address",
    "embed": "link / URL",
    "image": "file — upload it first, then give me the file",
    "file": "file — upload it first, then give me the file",
    "progress": "percentage 0–100",
    "calculation": "(auto-calculated — you don't fill this)",
}


def _render_app_form(app_name: str, compact_fields: list[dict[str, Any]]) -> str:
    """Build a ready-to-display Markdown form the model can show verbatim: every
    fillable field grouped required/optional, with each dropdown's selectable
    option values spelled out so the user can pick."""
    def _line(f: dict[str, Any]) -> str:
        label = f.get("label") or f.get("external_id") or "(unnamed)"
        ftype = f.get("type") or ""
        opts = f.get("options")
        if isinstance(opts, list) and opts:
            choices = ", ".join(str(o.get("text")) for o in opts if o.get("text"))
            return f"- **{label}** (choose one): {choices}"
        hint = _FIELD_TYPE_HINT.get(ftype, ftype or "value")
        return f"- **{label}** — {hint}"

    # calculation fields are auto-computed; never ask the user to fill them.
    fillable = [f for f in compact_fields if f.get("type") != "calculation"]
    required = [f for f in fillable if f.get("required")]
    optional = [f for f in fillable if not f.get("required")]

    lines: list[str] = [f"Here are the fields for **{app_name or 'this app'}**. "
                        "Give me values for the ones you want to set:", ""]
    if required:
        lines.append("**Required:**")
        lines += [_line(f) for f in required]
        lines.append("")
    if optional:
        lines.append("**Optional:**")
        lines += [_line(f) for f in optional]
    return "\n".join(lines).strip()


def _summarise_get_app_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return a compact get_app result exposing every field's external_id, field_id,
    type, required flag and (for category/status) options — so the schema the model
    needs to build create_item is always present and never truncated. Also includes
    a ready-to-display ``form`` (all fields + dropdown option values) the model shows
    the user before creating. Leaves the result unchanged if no field list is found."""
    if not isinstance(result, dict):
        return result
    src = result.get("data") if result.get("data") is not None else result
    fields = _find_app_fields(src)
    if not fields:
        return result

    app_meta: dict[str, Any] = {}
    def _scan_meta(node: Any) -> None:
        if app_meta or not isinstance(node, dict):
            return
        keys = ("app_id", "name", "item_name", "space_id", "url_label")
        picked = {k: node.get(k) for k in keys if node.get(k) is not None}
        if picked.get("app_id") is not None:
            app_meta.update(picked)
            return
        for v in node.values():
            _scan_meta(v)
    _scan_meta(src)

    compact = [_compact_field(f) for f in fields]
    return {
        "isError": False,
        "app": app_meta,
        "field_count": len(fields),
        "fields": compact,
        "form": _render_app_form(app_meta.get("name") or "", compact),
        "note": (
            "This is the app's field schema. USE IT FOR THE CURRENT TASK — do not assume "
            "an item is being created. If the user is CREATING/EDITING a record: show the "
            "'form' text VERBATIM (all fields + dropdown option values) and wait for their "
            "values (unless every required field was already given); the create_item/update_item "
            "'fields' keys MUST be these exact external_id strings; value formats: text=string; "
            "number=number; money={\"value\":N,\"currency\":\"USD\"}; date={\"start\":\"YYYY-MM-DD "
            "HH:MM:SS\"}; category/status=the option id whose text was picked; relationship/app="
            "item_id integer; contact=profile_id integer. If the user is building a FLOW/automation: "
            "use this list to show which fields they can trigger on or update (use 'field_id' as the "
            "numeric field_ids for a specific-field trigger, and 'external_id' in "
            "item.field.{external_id} effects) — do NOT call create_item."
        ),
    }


# ── "Fetched the fields but didn't show them" guard ─────────────────────────────
# After get_app the model sometimes says "I fetched the fields, tell me which one"
# without actually LISTING any field. We detect that and append the real list so the
# user always sees the options.
_CLAIMS_FIELDS_PHRASES = (
    "fetched the", "fetched all", "list of fields", "the fields", "which field",
    "choose which", "fields from the", "fields in your", "fields in the",
    "waiting for you to tell me which", "let me know which field",
    "so you can choose", "pick which", "select which", "which of the following fields",
)


def _claims_to_present_fields(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in _CLAIMS_FIELDS_PHRASES)


def _count_field_labels_shown(text: str, labels: list[str]) -> int:
    t = (text or "").lower()
    return sum(1 for lbl in labels if lbl and str(lbl).lower() in t)


def _render_field_list(app_name: str, fields: list[dict[str, Any]]) -> str:
    """A neutral, display-ready field list (works for both 'which field to trigger on'
    and 'here are the fields'). Dropdowns show their selectable option values."""
    lines: list[str] = [f"Fields in **{app_name or 'this app'}** you can choose from:"]
    for f in fields:
        if f.get("type") == "calculation":
            continue
        label = f.get("label") or f.get("external_id") or "(unnamed)"
        ftype = f.get("type") or ""
        opts = f.get("options")
        if isinstance(opts, list) and opts:
            choices = ", ".join(str(o.get("text")) for o in opts if o.get("text"))
            lines.append(f"- **{label}** ({ftype}): {choices}")
        else:
            lines.append(f"- **{label}** ({ftype})")
    return "\n".join(lines)


# User-authored operating prompt, adapted to the real Podio MCP tool names
# (filter_items→get_items, list_apps→get_apps_in_space, list_organizations→
# get_organizations, list_workspaces→get_spaces_in_organization, list_members→
# get_space_members/get_org_members, get_item_by_field→get_items+filters,
# reassign_task→update_task, get_activity_stream→get_notifications). Podio's MCP
# server has no delete_item tool.
_SYSTEM_PROMPT = """You are a Podio CRM assistant. Execute tasks accurately using available tools. Never fabricate data.

---

CORE RULES
- Complete every task fully — never stop halfway.
- Never call write tools (create_item, update_item) unless user explicitly says "create", "update", or "modify".
- Never guess or fabricate data — only return what tools return.
- Never ask for information retrievable by a tool (app_id, space_id, org_id).
- If a tool returns partial data, keep calling until complete.
- If a tool fails, retry once with corrected parameters before reporting failure.
- NEVER write function calls like tool_name({args}) in plain text. Only use structured tool calls.
- NEVER output JSON or code blocks in plain text. Summarise tool results in clear prose.
- CRITICAL: NEVER say "successfully updated / created / deleted / completed / added / linked / attached / set / uploaded" unless you have already called the relevant tool for THAT exact action AND its result confirms success. If you have not yet called the tool, call it now — do not reply with prose first.
- CRITICAL — ACTUALLY SHOW WHAT YOU FETCH: When you fetch a list to help the user choose (app fields, dropdown options, items, tasks, members), you MUST print that list in your reply. NEVER say "I fetched the fields / I have the list / choose which one" without actually listing them right there. A promise to show is not showing.
- CRITICAL — CHECK EVERY TOOL RESULT: After each tool call, read its result before saying anything. A result containing "isError", "error", "success": false, "no field", "not allowed", "must be", "404"/"400"/"403"/"500", or any HTTP error means the action FAILED. On failure: fix and retry once if you can, otherwise tell the user plainly it FAILED and why. A failed call is NOT "done" — never report a failed or never-attempted action as successful. Never claim a record/file "already exists" or is "already attached" from memory: verify with a read tool (e.g. get_item_files) first.
- CRITICAL: When a request has multiple steps (e.g. read → comment → create task), you MUST call ALL the tools in sequence before writing any summary. Never stop after the reads and pretend the writes happened.
- PARTIAL EXECUTION: Steps in a multi-step request are INDEPENDENT unless one truly needs another's output. If ONE step is blocked or impossible (e.g. a reminder on an item whose app has no date field), DO NOT abandon the whole request and DO NOT stop to ask a question — carry out every OTHER step you can (e.g. still add the comment, still clone), then finish with a per-step status report: each step marked ✓ done (with the result/ID) or ✗ blocked (with a one-line reason and, if useful, a suggested alternative).
- Only pause to ask the user when a REQUIRED input is genuinely missing AND cannot be looked up by a tool or safely defaulted (e.g. defaulting task assignee to the current user). Do NOT ask "Would you like me to proceed?" for non-destructive steps you were already told to do.
- Do NOT turn a scope-changing workaround (e.g. creating a task to stand in for an impossible item reminder) into a blocking question. Complete the steps you can, mention the alternative in your final report, and only perform such a workaround if the user explicitly asked for it.
- NEVER output template placeholders like {item_id}, {task_id}, {item_title} — these are never valid in a reply. Always substitute real values from tool results.
- NEVER fabricate IDs (task_id, user_id, item_id). Only use IDs that appear in actual tool results.
- CRITICAL: When the user provides a numeric ID (flow_id, item_id, task_id, app_id, etc.), copy it EXACTLY digit-for-digit into the tool call. Never transpose, round, or alter any digit. If unsure, repeat the ID back before calling the tool.
- CRITICAL: NEVER use clone_item (or any duplicate/copy operation) as a workaround for a failed create_item. Cloning is a DIFFERENT action and is only permitted when the user explicitly asked to clone/duplicate/copy a record. If create_item fails, re-read the exact error, fix the specific field it names, retry once, and if it still fails report the exact error plus the remaining required fields from get_app — do NOT clone, and do NOT invent any other substitute.

---

TOOL SELECTION

| Situation                                        | Tool(s)                                                                 |
|--------------------------------------------------|-------------------------------------------------------------------------|
| Get all/multiple records from app                | get_items                                                               |
| Get one record by ID                             | get_item                                                                |
| Find app_id in active workspace                  | get_apps_in_space                                                       |
| Find space_id                                    | get_spaces_in_organization                                              |
| Find org_id                                      | get_organizations                                                       |
| Workspace / org members                          | get_space_members / get_org_members                                     |
| Search across entire account                     | search_globally                                                         |
| Recently created/updated item(s), "what changed / what did I do today", most-recent record across a WHOLE workspace | get_activity_stream(space_id=<space_id>) |
| List files attached to a record                  | get_item_files                                                          |
| Read / download an attached file                 | download_file                                                           |
| Recent activity / notifications                  | get_notifications                                                       |
| List incomplete tasks in the WHOLE workspace     | get_tasks(space_id=<space_id>, completed=false) — space_id required     |
| List completed tasks                             | get_tasks(completed=true) — do NOT pass space_id for completed tasks    |
| All tasks in a specific APP ("tasks in the X app") | get_app_tasks(app_id=<app_id>) — walks the app's items & collects their tasks. NOT get_tasks(space_id) (whole workspace) and NOT get_reference_tasks(ref_type="app") (returns 0 — the app object has no tasks) |
| Create a task                                    | create_task                                                             |
| Update task text / due date / label              | update_task(task_id, text=, due_on=, label_id=)                        |
| Complete a task                                  | complete_task                                                           |
| Delete a task                                    | delete_task                                                             |
| Reassign a task to another user                  | reassign_task                                                           |
| Mark a completed task back as incomplete         | uncomplete_task                                                         |
| Remove a task's link to a record                 | remove_task_reference                                                   |
| Change task priority order                       | rank_task                                                               |
| All tasks linked to a specific record (one item) | get_reference_tasks(ref_type="item", ref_id=<item_id>)                  |
| Task label operations                            | get_task_labels / create_task_label / update_task_label / delete_task_label |
| Task counts / aggregated stats                   | get_task_summary / get_task_count                                       |
| List / inspect automations on an app             | get_app_flows / get_flow                                                |
| Flow variable reference discovery                | get_flow_context / get_flow_effect_attributes / get_flow_possible_attributes |
| Create / update / delete an automation           | create_flow / update_flow / delete_flow                                 |
| List / create / delete webhooks                  | list_webhooks / create_webhook / delete_webhook                         |
| Activate a webhook (two-step verification)       | request_webhook_verification → validate_webhook_verification            |
| Create / update / archive / restore a workspace  | create_workspace / update_workspace / archive_workspace / restore_workspace |
| Delete a workspace permanently                   | delete_workspace                                                        |
| Invite / remove / change role of workspace member| invite_workspace_member / remove_workspace_member / update_workspace_member_role |
| Create a new app inside a workspace              | create_app                                                              |
| Send a direct message / start a conversation     | create_conversation / reply_to_conversation                             |
| List / read conversations                        | list_conversations / get_conversation                                   |
| Calendar events (full / by workspace / by app)   | get_calendar / get_space_calendar / get_app_calendar                    |
| Get / set / remove a reminder on a task or item  | get_reminder / set_reminder / delete_reminder                           |
| Get / set / remove a recurring task schedule     | get_recurrence / set_recurrence / delete_recurrence                     |
| Full change history of a record                  | get_item_revisions                                                      |
| Roll back a record to an earlier revision        | revert_item_revision                                                    |
| Records that reference / link to a record        | get_item_references                                                     |
| Update a single field without touching others    | update_item_field                                                       |
| Export all records from an app to Excel          | export_app_xlsx                                                         |
| Duplicate / clone a record                       | clone_item                                                              |
| Delete multiple records at once                  | bulk_delete_items                                                       |

---

KNOWN APP IDs — ONLY valid in space_id 7532914
If active workspace != 7532914, ignore this table and call get_apps_in_space to discover real IDs.

Seller Leads=29772004, Offers=29772006, Agents=29772008, Zip Codes=29772009,
Lead Followups=29772010, FU Templates=29772014, FU Messages=29772015,
Settings=29776001, Low Price Index=29776000, Contacts=26071436, Employees=26071461,
Products=26071589, Customers=26071633, Orders=26071687, Posts=26085138,
Mailers=26098285, My Offers=26101722, Cashapps=26201703, Teammates=26201713,
Hide and Seek=26216639, Teammates on testing=26218146, Twilio Call Logs=26503625,
TestWork=27886326, Students=27905316, Mark Sheet=27940870, Test 360 ST=28248302,
Testing Podio=28248576, Calendars=28636758, Test Users=28689369,
Property Detail=28695511, Availability Dev=29798190, Rennova Leads=30534986.

Default space_id = 7532914.

---

PAGINATION — Never miss records
- Start: limit=50, offset=0.
- If response returns exactly 50, call again with offset+50.
- Repeat until response returns fewer than 50.
- Combine all pages and report total count.

---

RECENT ACTIVITY / "WHAT CHANGED TODAY" / "THE RECENTLY UPDATED ITEM"
When the user refers to recent activity, "the recently updated/created item", "what I created/updated/commented today", "the latest item", or "the most recent record" WITHOUT naming a single app:
1. Call get_activity_stream(space_id=<active space_id>). It returns events NEWEST FIRST across EVERY app in the workspace, and includes comments and file attachments — which do NOT change an item's last_edit_on and are therefore INVISIBLE to a get_items sort. NEVER answer these questions by picking the top of one arbitrary app's get_items — that is what returns a stale item and attaches files to the wrong record.
2. To find items from TODAY, use the CURRENT DATE header and read the events whose created_on/last_edit_on is today. The stream is already time-ordered; do not assume "no items today" just because one app's get_items looked old.
3. If get_activity_stream is unavailable/errors, FALL BACK: call get_apps_in_space(space_id), then get_items(app_id, sort_by="last_edit_on", sort_desc=true, limit=5) for each app, and compare created_on/last_edit_on across all apps — do not stop at the first app.
4. Before performing a WRITE (attach a file, add a comment, update) on an item you identified as "the recent one", state which item you mean (name + item_id) and, if there is ANY ambiguity, confirm with the user first. Attaching to the wrong record is hard to undo.
⚠️ Adding a comment or attaching a file does NOT bump last_edit_on. If the user says they commented today but an item shows an old last_edit_on, that is expected — trust the activity stream, not last_edit_on.
---
ITEM CREATE / UPDATE WORKFLOW
⚠️ This section is ONLY for create_item / update_item (creating or editing a Podio RECORD).
   For creating an automation/flow, see FLOW (AUTOMATION) WORKFLOW below.
   "Workflow" / "automation" / "flow" → FLOW WORKFLOW, NOT this section.
1. Identify app_id in the active workspace (use known IDs only if space_id=7532914, else call get_apps_in_space).
2. Call get_app(app_id). The result contains a ready-made 'form' string plus a 'fields' schema (external_id, type, required, and dropdown options).
3. ALWAYS show the user the 'form' string FIRST — display it verbatim so they see EVERY field and, for each dropdown (category/status) field, the exact option values they can choose. Then WAIT for the user to provide values.
   - The ONLY time you may skip the form and create immediately is when the user already gave a value for every required=true field in their request. If ANY required field is still unspecified, or the user gave no field values at all, you MUST show the form and wait — do NOT invent values, do NOT default a dropdown to a guessed option, do NOT proceed to create_item.
4. Once the user replies with values, build the fields object using exact external_ids from get_app. Include every field the user provided PLUS every field marked required=true. Omit optional fields the user left blank.
   - Keys MUST be the literal external_id strings from get_app — never guess a key like "sku" that is not in the schema (Podio returns "No field found").
   - money field value → {"value": 999.99, "currency": "USD"}.
   - date field value → {"start": "YYYY-MM-DD HH:MM:SS"} (an object with "start"; add "end" only for ranges). A bare string or an omitted required date causes Podio error 'must be Range'.
   - category/status value → the numeric option id from that field's options list.
   - relationship/app value → the referenced item_id integer; contact value → profile_id integer.
5. Call create_item / update_item.
6. If it FAILS: read the error, correct the exact field/format it names, retry ONCE. If it still fails, report the exact error and the required fields still needed. NEVER fall back to clone_item or fabricate a record.
7. On success, confirm and show the new item_id.

---

FIELD VALUE FORMATS

| Field Type                  | Format                                                      |
|-----------------------------|-------------------------------------------------------------|
| text                        | plain string                                                |
| number                      | integer or float (not quoted)                               |
| date / due_on               | "YYYY-MM-DD HH:MM:SS" — NOT "YYYY-MM-DD" alone, NOT ISO 8601 with T/Z |
| money                       | {"value": 100, "currency": "USD"}                           |
| phone / email               | [{"type": "work|home|other", "value": "..."}]               |
| address                     | single full string — no splitting                           |
| category / status           | exact option ID or exact option text from get_app           |
| relationship / app reference| item_id as integer — resolve name to item_id via get_items  |
| contact / member (person)   | profile_id integer — resolve via get_space_members          |

Never invent external_ids. Only use keys that literally appear in get_app output for that app.
Only send fields the user provided. Never add extras to "fill out" the record.
---
DELETE FILE WORKFLOW
Triggered by: "delete file / attachment / photo / document / upload"
1. Call get_item_files(item_id) — show filename, size, file_id.
2. Ask user to confirm which file to delete.
3. Call delete_file(file_id).
4. Confirm deletion.
!! Never call delete_item for a file deletion request.
---
DELETE RECORD WORKFLOW
Triggered by: "delete this contact / agent / record / item"
1. Call get_item(item_id) — show name and item_id.
2. Warn user: "This permanently deletes the record. Confirm?"
3. Wait for explicit confirmation.
4. Call delete_item(item_id).
!! Never delete without explicit user confirmation.
---
TASK MANAGEMENT WORKFLOW
- list incomplete tasks in the WHOLE WORKSPACE: call get_tasks(space_id=<space_id>, completed=false, limit=50). Always pass space_id for incomplete tasks.
- tasks in a SPECIFIC APP ("all the tasks in the Products app"): resolve the app_id first (get_apps_in_space if not known), then call get_app_tasks(app_id=<app_id>). It walks the app's items and returns every task on them. DO NOT use get_tasks(space_id=...) (that is the whole workspace across all apps) and DO NOT use get_reference_tasks(ref_type="app") (Podio returns 0 — tasks live on the app's ITEMS, not on the app object). If the result has items_truncated=true, tell the user the app had more items than were scanned — do not imply the list is complete.
- list completed tasks: call get_tasks(completed=true, limit=50). Do NOT pass space_id — Podio ignores the space filter for completed tasks and will return nothing.
- create task: call create_task(text, due_on="YYYY-MM-DD HH:MM:SS"). due_on MUST use "YYYY-MM-DD HH:MM:SS" — NOT "YYYY-MM-DD" alone and NOT ISO 8601 "YYYY-MM-DDThh:mm:ssZ". Example: "2024-07-16 12:00:00".
- update task (text/due/label): call update_task(task_id, text=..., due_on=..., label_id=...). Provide at least one field.
- assign label to task: 1) call get_task_labels to find label_id by name. 2) call update_task(task_id, label_id=<id>).
- delete task: call delete_task(task_id). Show task text first; confirm before deleting.
- reassign: call reassign_task(task_id, user_id). Resolve user → get_space_members if needed.
- uncomplete: call uncomplete_task(task_id).
- change priority: call rank_task(task_id, before_task_id OR after_task_id).
- tasks on a record: call get_reference_tasks(ref_type="item", ref_id=<item_id>).
- recurring schedule: get_recurrence / set_recurrence / delete_recurrence with ref_type="task".
  set_recurrence schedule format: {"step":"daily|weekly|monthly", "days_of_week":[1-7] (weekly only), "day_of_month":1-31 (monthly only)}.
- reminder on task/item: Podio reminders are RELATIVE (minutes before the object's due date). Prefer set_reminder(ref_type, ref_id, remind_delta=<minutes before due>, e.g. 1440=1 day, 60=1 hour). You MAY pass remind_at="YYYY-MM-DD HH:MM:SS" instead — it is converted using the object's due date, which must already exist and be after the reminder time. get_reminder / delete_reminder to read/remove.
---
FLOW (AUTOMATION) WORKFLOW
⚠️ "Create a workflow / automation / flow" means create_flow, NOT create_item. This is
   NOT the item-create form. But you DO use get_app here — to SHOW the user which fields
   they can trigger on / update, and to get a field's numeric field_id.

ASK ONE QUESTION AT A TIME, in this order. After each answer, move to the next step —
never dump all questions at once, and never proceed until the current step is answered.
Briefly explain each choice so the user knows what it does (be a guide, not a form).

STEP 1 — TRIGGER. Ask: "When should this automation run?" and list ONLY these:
   1. When a new record is created  (item.create)
   2. When an existing record is updated  (item.update)
   3. When a record is deleted  (item.delete)
   Wait for the answer.
   • If they chose UPDATED (item.update), ask next: "Should it run on ANY field change,
     or only when a SPECIFIC field changes?"
     - If they want a specific field (or ask "what fields can I choose / trigger on"),
       CALL get_app(app_id) and PRESENT the field list from its 'form'/'fields' (field
       name + type; for dropdowns list the option values). Let them pick one or more.
       Remember each chosen field's NUMERIC field_id (from the schema) for field_ids later.
     - If ANY field, no get_app needed for the trigger.

STEP 2 — ACTION. Ask: "What should the automation do?" and list ONLY these three (the only
   effects Podio's flow API supports):
   1. Create a task        (task.create)
   2. Add a comment        (comment.create)
   3. Post a status update  (status.create)
   Wait, then ask the DETAIL question for the chosen action:
   - Create a task   → "What should the task say? Who is it assigned to? Due in how many days?"
   - Add a comment   → "What should the comment text say?"
   - Status update   → "What should the status text say?"
   ⚠️ DO NOT suggest {{item.field_name}} style variables — they are NOT valid in the REST API.

⚠️ WHAT THIS FLOW API CAN AND CANNOT DO — be honest about this UP FRONT, before asking for details:
   CAN (the ONLY supported effects): create a task, add a comment, post a status update.
   CANNOT (NOT supported by Podio's flow API — these need Podio's advanced automation
   "GlobiFlow / Workflow Automation", configured manually in Podio, which this tool cannot access):
     • Updating / setting ANY field value — even to a fixed value. Podio rejects a field effect with
       "Unknown attribute item.field.X". There is NO working way to set a field via this API.
     • Arithmetic or RELATIVE field changes — "decrease Progress by 10", "increase X by N", add/subtract.
     • CONDITIONAL logic — "if Progress ≤ 10 set to 0", "only when …", if/then branches.
     • Copying/deriving a value from another field or the trigger, or any computed value.
     • Sending email/SMS, calling webhooks as an effect, or multi-step branching.
   If the user asks for any CANNOT item (e.g. "set/decrease Progress"), DO NOT pretend to build it, DO NOT
   call create_flow with an item.update / item.field.* effect (it will fail), and DO NOT invent a
   "field_id is missing" excuse. Tell them plainly it is not possible via the flow API, explain it needs
   GlobiFlow set up manually in Podio, and offer a supported alternative (create a task, add a comment,
   or post a status — e.g. a task "Review Progress after Category change"). Wait for them to pick one.

STEP 3 — NAME. Suggest a descriptive name and confirm (e.g. "Product Updated - Create Review Task").
   (You may ask for the name first instead, if the user prefers — but always confirm it.)

STEP 4 — Only AFTER trigger, action, and name are settled, call tools:
1. Call get_app_flows(app_id) — check for duplicates.
2. If item.update + specific field(s): pass field_ids=[<numeric field_id>, ...] to create_flow
   (the numeric field_id from get_app) so only those field changes fire. If you are unsure of the
   numeric id you MAY pass the field's external_id string instead (e.g. "category") — the tool
   resolves it to the real field_id and validates it. NEVER invent a field_id: every field in the
   get_app schema HAS one — read it there. Do NOT claim "field_id is missing"; if get_app returned
   no fields at all, that is a schema-fetch problem to report, not a reason to refuse the automation.
3. Build the effects list. Podio requires attributes as an array of {attribute_id, value} objects.
   Official attribute_id strings (from developers.podio.com/doc/flows):
   - comment.create:      [{"type": "comment.create", "attributes": [{"attribute_id": "comment.value", "value": "<comment text>"}]}]
   - status.create:       [{"type": "status.create", "attributes": [{"attribute_id": "status.value", "value": "<status text>"}]}]
   - task.create:         [{"type": "task.create", "attributes": [{"attribute_id": "task.text", "value": "<description>"}, {"attribute_id": "task.due", "value": "7"}]}]
     (task.due is the number of days as a STRING — "7" for next week, "0" for the trigger day. All effect values must be strings.)
   - conversation.create: attributes use "conversation.subject", "conversation.text", "conversation.participant"
   ⚠️ There is NO supported "update a field" effect — do NOT build an effect of type item.update or an
      attribute_id like "item.field.X"; Podio rejects it ("Unknown attribute"). Use only task/comment/status.
   Do NOT call get_flow_effect_attributes — endpoint unavailable.
   Do NOT invent {{item.field_name}} variables — they are not valid.
4. Present the final config summary to the user and wait for explicit confirmation.
5. Call create_flow(app_id, trigger_type, name, effects, field_ids?).
6. Report the new flow_id and confirm the automation is active.

To UPDATE: update_flow(flow_id, name?, config?, effects?) — pass ONLY the parts you want to
  change; it MERGES with the current flow (a rename will NOT wipe the field trigger). Effects use
  the SAME "attributes" array as create (NOT a "values" key — that 500s). To change which field a
  filtered trigger watches, pass config={"field_ids":[<field_id or external_id>]}. Trigger TYPE
  (item.create/update/delete) cannot be changed — for that, delete_flow then create_flow.
  ⚠️ Prefer update_flow over delete+recreate for content/name/field changes; only delete+recreate
     when the TRIGGER TYPE itself must change, and if the recreate fails, do NOT report success.
To DELETE: delete_flow(flow_id) — confirm with user first.
To INSPECT: get_flow(flow_id) — shows full trigger + effects config.
---
WEBHOOK WORKFLOW
1. Create: create_webhook(ref_type="app|space", ref_id, url, event_type). URL must use port 80 or 443.
2. After creation, activate via two-step verification:
   a. request_webhook_verification(hook_id) — Podio sends a code to the callback URL.
   b. validate_webhook_verification(hook_id, code) — submit the received code to activate.
3. List webhooks: list_webhooks(ref_type, ref_id).
4. Delete: delete_webhook(hook_id).
---
WORKSPACE OPERATION WORKFLOW
- Create: create_workspace(org_id, name, privacy="open|closed", url_label?). Get org_id via get_organizations.
- Update: update_workspace(space_id, name?, privacy?, url_label?).
- Archive BEFORE deleting: archive_workspace(space_id), then delete_workspace(space_id).
- Restore archived: restore_workspace(space_id).
- Members: invite_workspace_member(space_id, email_or_user_id), remove_workspace_member(space_id, user_id), update_workspace_member_role(space_id, user_id, role="admin|regular|light").
---
CONVERSATION WORKFLOW
- Start a conversation: create_conversation(participant_ids=[user_id,...], subject, text).
- Reply: reply_to_conversation(conversation_id, text).
- List all: list_conversations(). Read one: get_conversation(conversation_id).
- Resolve user_id via get_space_members or get_org_members before creating.
---
CALENDAR / REMINDER WORKFLOW
- To answer "upcoming events/tasks", CALL THE CALENDAR TOOLS — do NOT brute-force get_items with a guessed date field.
- Full calendar: get_calendar() — returns upcoming tasks + item due dates. Defaults to today → +90 days.
- Workspace calendar: get_space_calendar(space_id). App calendar: get_app_calendar(app_id).
- Optional date_from / date_to (YYYY-MM-DD) narrow the window; compute them from the CURRENT DATE above (never a 2024 date). Omit them for the default upcoming window.
- EXTERNAL calendars: get_calendar/get_space_calendar/get_app_calendar only return Podio-native items + tasks. Calendars the user ADDED via Podio's "Add Calendar" (Google/Exchange/etc.) are separate. To include them: call list_linked_accounts(capability="calendar") to get each linked_account_id, then get_linked_account_calendar(linked_account_id) for each. If the user mentions calendars that aren't showing up, check these linked accounts.
- Reminders (RELATIVE — minutes before the object's due date): set_reminder(ref_type="task|item", ref_id, remind_delta=<minutes before due>). remind_at="YYYY-MM-DD HH:MM:SS" is accepted and converted via the object's due date (which must exist). get_reminder / delete_reminder to read/remove.
- When the user wants the reminder AT a specific time (e.g. "remind me Monday at 10am"): set the object's due to that exact time and use remind_delta=0 so it fires exactly then. Only use a positive remind_delta when the user explicitly says "X minutes/hours/days before". Never invent an arbitrary lead like 30 minutes.
- A reminder on an ITEM needs that item's app to have a date field. If it does not, say so and (only if the user's request implies a scheduled follow-up) set the reminder on a linked task instead — and state clearly in the summary that it is on the TASK, not the item, and give the exact fire time from the tool result.
---
ITEM HISTORY / REFERENCES WORKFLOW
- Change history: get_item_revisions(item_id) — lists all versions with who changed and when.
- Roll back: revert_item_revision(item_id, revision_id) — returns new state after revert.
- What links to this record: get_item_references(item_id) — shows related items across apps.
  ⚠️ If get_item_references returns app names / counts but no item titles or IDs, immediately
     call get_items(app_id=<referenced_app_id>, limit=50) to resolve the specific records.
     Do NOT ask the user — resolve it automatically and show the full list.
- Update one field only: update_item_field(item_id, field_id, value) — leave all other fields untouched.
- Clone a record: clone_item(item_id) — returns the new cloned item_id.
- Bulk delete: bulk_delete_items(app_id, item_ids=[...]) — confirm with user before calling.
- Export to Excel: export_app_xlsx(app_id) — returns a download_url; do NOT embed the bytes in the reply.
---
OUTPUT FORMAT
- Always show: "Found X records" at the top.
- Present results as a clean list or table with item_id on every row.
- Never output raw JSON.
- If no records found: "No records found in [App Name]."
---
STRICT PROHIBITIONS
- Never use write tools for read operations.
- Never call delete_item for file/attachment deletions.
- Never delete a record without explicit user confirmation.
- Never pass placeholder or dummy field values.
- Never fabricate item IDs, field values, or record counts."""


def _coerce_args(raw: Any) -> dict[str, Any]:
    """Normalise the tool-call arguments into a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _recover_tool_name(raw_name: str, valid_names: set[str]) -> tuple[str, dict | None]:
    """Recover a real tool name + inline args from a mangled tool-name string.

    Small models routinely corrupt the tool-name field by appending args JSON,
    reasoning text, or unicode noise — joined by a plain space, a unicode space,
    or nothing at all. Observed shapes:
        'get_app {"app_id": 123}'      → args jammed after an ASCII space
        'get_app{"app_id": 123}'       → args jammed with no separator
        'get_calendar Räikk{}'         → unicode noise + empty args (real case)
        '**Note...** ​get_tasks'        → reasoning text jammed before the name
        '{"item_id": 3328181366}'      → name IS a JSON object (name unrecoverable)

    Returns ``(name, inline_args)``. ``name`` is the raw string unchanged when
    nothing matched, so the caller still errors with 'Unknown tool'. ``inline_args``
    is a dict recovered from an embedded ``{...}`` object, or ``None``.
    """
    name = (raw_name or "").strip()
    if name in valid_names:
        return name, None

    # Pull out an embedded JSON object as candidate args (e.g. '... {"app_id": 5}').
    inline_args: dict | None = None
    brace = name.find("{")
    if brace != -1:
        try:
            parsed = json.loads(name[brace:])
            if isinstance(parsed, dict):
                inline_args = parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # If the name IS a JSON object there's no name to recover — only args.
    if name.startswith("{"):
        return name, inline_args

    # Normalise: replace every char that isn't a tool-name char with a space.
    # This turns unicode/punctuation noise ('Räikk', '{}', a unicode space) into
    # word boundaries WITHOUT gluing tokens together the way ascii-strip would
    # (e.g. 'get_calendar<nbsp>Räikk{}' → 'get_calendar  R ikk  ').
    norm = re.sub(r"[^A-Za-z0-9_]+", " ", name)
    # Longest-first so 'get_item_files' wins over 'get_item'.
    ordered = sorted(valid_names, key=len, reverse=True)
    # 1. Valid tool name appearing as a whole word anywhere in the string.
    for cand in ordered:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(cand)}(?![A-Za-z0-9_])", norm):
            return cand, inline_args
    # 2. Glued with no separator ('get_calendarRäikk') — fall back to a prefix match
    #    on the raw name (longest-first keeps it unambiguous).
    for cand in ordered:
        if name.startswith(cand):
            return cand, inline_args

    return name, inline_args


def _extract_balanced_json(text: str, start: int) -> str | None:
    """Return text[start:] up to and including the '}' that balances text[start]=='{',
    respecting strings/escapes. None if unbalanced."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def _tool_name_for_json(text: str, brace_pos: int, valid_names: set[str]) -> str:
    """Infer which tool a standalone JSON object at ``brace_pos`` belongs to.

    (1) An identifier glued immediately before the '{' (``create_flow{...}``,
        ``create_flow({...})``, or a garbled ``get_appXYZ{...}``) — recovered via
        _recover_tool_name. (2) Otherwise the LAST valid tool name mentioned in the
        preceding ~240 chars of prose (models often write "Calling create_flow …"
        then dump the args as a bare JSON block). Empty string if neither resolves."""
    prefix = text[:brace_pos]
    m = re.search(r"([A-Za-z_][A-Za-z0-9_]{1,60})[\"']?\s*[:(]?\s*$", prefix)
    if m:
        name, _ = _recover_tool_name(m.group(1), valid_names)
        if name in valid_names:
            return name
    window = prefix[-240:]
    best_pos, best_name = -1, ""
    for vn in valid_names:
        for mm in re.finditer(r"\b" + re.escape(vn) + r"\b", window):
            if mm.start() > best_pos:
                best_pos, best_name = mm.start(), vn
    return best_name


def _extract_text_tool_calls(text: str, valid_names: set[str]) -> list[dict[str, Any]]:
    """Recover tool calls a model wrote as plain TEXT instead of using the structured
    function-calling format — e.g. `get_app{"app_id":123}`, `create_flow({...})`, a
    garbled `get_appXYZ{"app_id":123}`, OR a bare JSON block whose tool name is only
    named in the preceding prose ("Calling create_flow …\\n{...}"). Scans TOP-LEVEL
    JSON objects, attributes each to a tool via _tool_name_for_json, and returns those
    that resolve to a real tool with a valid JSON-object argument."""
    if not text or "{" not in text:
        return []
    calls: list[dict[str, Any]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        obj = _extract_balanced_json(text, i)
        if not obj:
            i += 1
            continue
        try:
            args = json.loads(obj)
        except (json.JSONDecodeError, ValueError):
            i += 1
            continue
        if isinstance(args, dict):
            name = _tool_name_for_json(text, i, valid_names)
            if name in valid_names:
                calls.append({"name": name, "args": args})
        i += len(obj)  # skip past this object (don't re-scan its nested braces)
    return calls


def _collect_ids(obj: Any, key_names: set[str], out: set[int]) -> None:
    """Recursively harvest integer id values stored under any of ``key_names``."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in key_names and isinstance(v, (int, str)) and str(v).isdigit():
                out.add(int(v))
            else:
                _collect_ids(v, key_names, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_ids(item, key_names, out)


def _guard_object_ids(
    name: str,
    args: dict[str, Any],
    user_ids: set[str],
    seen_task: set[int],
    seen_item: set[int],
    created_task: list[int],
    created_item: list[int],
) -> list[str]:
    """Anti-hallucination: correct a task/item id the model invented.

    Models sometimes call a follow-up tool (e.g. set_reminder after create_task)
    with an id that was NEVER returned by any tool and NEVER given by the user —
    typically a transposed or item-vs-task-confused number. When that happens and
    exactly one object of that type was created (or seen) in this run, substitute
    the real id. Conservative: only acts on the unambiguous single-object case.
    Mutates ``args`` in place; returns human-readable correction notes.
    """
    targets: list[tuple[str, str]] = []
    if "ref_id" in args:
        rt = str(args.get("ref_type", "")).lower()
        if rt in ("task", "item"):
            targets.append(("ref_id", rt))
    if "task_id" in args:
        targets.append(("task_id", "task"))
    if "item_id" in args:
        targets.append(("item_id", "item"))

    notes: list[str] = []
    for key, typ in targets:
        try:
            cid = int(args.get(key))
        except (TypeError, ValueError):
            continue
        if str(cid) in user_ids:
            continue  # the user supplied this id — trust it
        seen = seen_task if typ == "task" else seen_item
        if cid in seen:
            continue  # a tool result surfaced this id — it's real
        created = created_task if typ == "task" else created_item
        target: int | None = None
        if len(created) == 1:
            target = created[0]
        elif not created and len(seen) == 1:
            target = next(iter(seen))
        if target is not None and target != cid:
            args[key] = target
            notes.append(
                f"Corrected {key} {cid} → {target}: the {typ} id {cid} was never returned "
                f"by a tool nor given by you; used the {typ} from this conversation instead."
            )
    return notes


def _sanitize_tool_args(args: dict[str, Any], props: dict[str, Any]) -> dict[str, Any]:
    """Fix common small-model mistakes against strict MCP schemas:

    - drop ``null`` values (omit optional params rather than send null)
    - coerce string numbers to int/float and vice-versa per the declared type
    """
    out: dict[str, Any] = {}
    for key, value in args.items():
        if value is None:
            continue  # strict schemas reject null for typed fields — omit instead
        spec = props.get(key) or {}
        declared = spec.get("type")
        if declared in ("number", "integer") and isinstance(value, str):
            s = value.strip()
            try:
                value = int(s) if (declared == "integer" or s.isdigit()) else float(s)
            except ValueError:
                continue  # unparseable number — drop it
        elif declared == "integer" and isinstance(value, float) and value.is_integer():
            value = int(value)
        elif declared == "string" and not isinstance(value, str):
            value = str(value)
        out[key] = value
    return out


def _clean_fields(fields: Any) -> Any:
    """Strip junk the model sometimes injects into a Podio ``fields`` object.

    External_ids are slugs ("title", "last-name") — never purely numeric or empty —
    so drop those keys, and drop null/empty values. This removes artifacts like the
    stray ``"0": 3`` entry small models emit, which Podio rejects.
    """
    if not isinstance(fields, dict):
        return fields
    cleaned: dict[str, Any] = {}
    for key, value in fields.items():
        k = str(key).strip()
        if not k or k.isdigit():
            continue
        if value is None or (isinstance(value, str) and value.strip() == ""):
            continue
        cleaned[k] = value
    return cleaned


async def run_podio_agent(
    message: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Run one turn of the Podio agent against Podio's hosted MCP server.

    ``history`` is a list of prior ``{"role": "user"|"assistant", "content": str}``
    turns. Returns ``{"success", "reply", "steps", "model"}`` where ``steps`` is a
    trace of every Podio MCP tool the model invoked.
    """
    # Active model is a 'provider:model' string chosen in the UI (Ollama or Google).
    from app.services.settings_service import get_setting

    model = (await get_setting("agent_model")) or f"ollama:{model_gateway.route_model('chat')}"

    # Discover Podio's tools live from the MCP server (requires an OAuth session).
    try:
        all_specs = await podio_mcp.list_tools()
    except Exception as exc:  # noqa: BLE001
        logger.warning("podio_agent_no_tools", error=str(exc))
        return {
            "success": False,
            "reply": "",
            "steps": [],
            "error": f"Podio is not connected: {exc}",
        }

    # Trim to the core tool set to keep small local models fast (fall back to all).
    tool_specs = [t for t in all_specs if t["function"]["name"] in _CORE_TOOLS] or all_specs
    # Safety: hide destructive write tools unless the conversation implies a change.
    # Consider the last few turns too, so a create flow that spans turns (agent asks
    # for field values → user replies with just the values) keeps the write tools.
    recent_text = " ".join(
        [t.get("content", "") for t in (history or [])[-4:] if t.get("role") == "user"]
        + [message]
    )
    wants_write = _wants_write(recent_text)
    if not wants_write:
        tool_specs = [t for t in tool_specs if t["function"]["name"] not in _WRITE_TOOLS]

    # Merge our custom Podio-files MCP server's tools (file attach, update, delete, download).
    # Destructive write tools are gated on write-intent; read-only file tools (download_file,
    # get_item_files) are available whenever the REST connection is live.
    # When a tool name exists in BOTH the hosted MCP and our REST server (e.g. update_item),
    # our REST version takes precedence.
    files_tool_names: set[str] = set()
    wants_read_tools = any(
        w in (message or "").lower()
        for w in (
            "download", "read file", "show file", "view file", "file content",
            "what does the file", "open file", "list file", "files on", "attached file",
            "calendar", "reminder", "recurrence",
            "conversation", "revision", "history",
            "export", "clone", "duplicate",
            "webhook", "flow", "workflow", "automation",
            "reference", "label", "task",
            # recent-activity / "what changed today" intents → get_activity_stream
            "recent", "recently", "latest", "today", "activity", "stream",
            "last updated", "last edited", "just created", "did i", "this week",
        )
    )
    try:
        if await podio_files.is_connected() and (wants_write or wants_read_tools):
            files_specs = await podio_files.list_tools()
            # If no write intent, expose only the read-only / non-destructive tool subset.
            if not wants_write:
                files_specs = [s for s in files_specs if s["function"]["name"] in _FILES_READ_ONLY]
            # clone_item creates a record — only expose it on explicit clone/duplicate
            # intent, never as a fallback the model can pick when create_item fails.
            if not _wants_clone(recent_text):
                files_specs = [s for s in files_specs if s["function"]["name"] != "clone_item"]
            files_tool_names = {s["function"]["name"] for s in files_specs}
            # Deduplicate: drop hosted MCP tools that we override via REST
            tool_specs = [
                t for t in tool_specs if t["function"]["name"] not in files_tool_names
            ] + files_specs
    except Exception as exc:  # noqa: BLE001
        logger.warning("podio_files_unavailable", error=str(exc))
        steps.append({"tool": "_files_client", "args": {}, "result": {"error": f"Files tools unavailable: {exc}"}})

    # For small local models, cap tool count to avoid context overflow + hallucination.
    provider_name, _ = model_gateway._split_model(model)
    if provider_name == "ollama":
        tool_specs = _filter_tools_for_ollama(tool_specs, message)

    valid_names = {s["function"]["name"] for s in tool_specs}

    # Prepend the REAL current date/time. Without this the model falls back to its
    # training-data date (e.g. it insisted "today" was 16 July 2024) and computes all
    # relative dates — "upcoming", "overdue", calendar windows — from that stale date.
    now = datetime.now().astimezone()
    date_header = (
        f"CURRENT DATE AND TIME: It is {now:%A, %d %B %Y, %H:%M} "
        f"({now:%Z%z}; ISO date {now:%Y-%m-%d}). This is the authoritative current "
        "date — always use it, NEVER your training-data date, for any date reasoning "
        "(today, tomorrow, this week, upcoming, overdue, next month) and for any date "
        "argument you pass to a tool. If the user asks what today's date is, answer with "
        f"{now:%A, %d %B %Y}.\n\n"
    )

    # Scope the agent to the user's selected workspace, if one is chosen.
    system_prompt = date_header + _SYSTEM_PROMPT
    try:
        selected = await podio_mcp.get_selected_workspace()
    except Exception:  # noqa: BLE001
        selected = None
    selected_space_id = selected.get("space_id") if selected else None
    if selected_space_id:
        system_prompt += (
            f"\n\nThe active workspace is '{selected.get('name')}' "
            f"(space_id={selected_space_id}) in organization '{selected.get('org_name')}'. "
            f"ALWAYS pass space_id={selected_space_id} to any tool that needs a space_id — "
            "NEVER ask the user for the space ID, you already have it. Only use a different "
            "space_id if the user explicitly names another workspace."
        )

    if files_tool_names:
        system_prompt += (
            "\n\nFILE ATTACHMENT / IMAGE: You can use a file the user already uploaded to Podio. "
            "When the conversation says a file was uploaded with a numeric file_id, FIRST resolve "
            "the target record's item_id (via get_items on the right app), THEN:\n"
            "- if the user wants to set it as a PICTURE / PHOTO / AVATAR / PROFILE IMAGE on the "
            "record, call set_item_image(file_id=<given>, item_id=<resolved>);\n"
            "- otherwise (attach/add a document/file to the record) call "
            "attach_file_to_item(file_id=<given>, item_id=<resolved>).\n"
            "NEVER invent a file_id or item_id — use the file_id from the conversation and a real "
            "item_id you looked up.\n"
            "- Check the result of the attach/image tool. Only say the file was attached/set when the "
            "tool returned success. If it returned an error, the file was NOT added — do not say it was.\n"
            "- If set_item_image fails because the app has no image field, that means the IMAGE was not "
            "set. If the user wants the file on the record, you MUST then call "
            "attach_file_to_item(file_id, item_id) AND confirm its success result before telling the "
            "user it was attached. Do not describe the file as attached until attach_file_to_item "
            "actually succeeds.\n"
            "- 'add it to the files' / 'attach it' → call attach_file_to_item and verify the result. "
            "Never claim it is 'already attached' without first calling get_item_files to confirm."
        )

    # Map each tool to its parameter schema so we can sanitize args + auto-fill the workspace.
    tool_schemas = {
        s["function"]["name"]: (s["function"].get("parameters") or {}).get("properties", {}) or {}
        for s in tool_specs
    }

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for turn in history or []:
        role = turn.get("role")
        content = turn.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    steps: list[dict[str, Any]] = []
    final_text = ""
    # Bounded retries for the "claimed a write that didn't succeed" correction so a
    # persistently-hallucinating model can't loop forever (falls through to discard).
    hallucination_retries = 0
    _MAX_HALLUCINATION_RETRIES = 2

    # Provenance tracking for the id anti-hallucination guard: ids the user typed,
    # ids surfaced by tool results, and ids created during this run.
    user_provided_ids: set[str] = set(
        re.findall(
            r"\d{5,}",
            " ".join(
                [t.get("content", "") for t in (history or []) if t.get("role") == "user"]
                + [message]
            ),
        )
    )
    seen_task_ids: set[int] = set()
    seen_item_ids: set[int] = set()
    created_task_ids: list[int] = []
    created_item_ids: list[int] = []
    # Most recent get_app field schema this turn — used to append the real field list
    # if the model claims it "fetched the fields" but doesn't actually list them.
    last_app_fields: list[dict[str, Any]] = []
    last_app_name: str = ""

    for _ in range(_MAX_STEPS):
        assistant_msg = await model_gateway.chat(
            messages, tools=tool_specs, model=model, num_predict=_NUM_PREDICT
        )
        # When the model returns tool calls, its text content is almost always
        # hallucinated speculation about the result — strip it so it doesn't
        # contaminate the context on the next turn and confuse the model into
        # "answering" from fake data instead of the real tool result.
        # Use None (not "") — Mistral rejects empty-string content on tool-call messages.
        if assistant_msg and assistant_msg.get("tool_calls"):
            assistant_msg = {**assistant_msg, "content": None}
        messages.append(assistant_msg or {})

        tool_calls = (assistant_msg or {}).get("tool_calls") or []
        if not tool_calls:
            candidate = (assistant_msg or {}).get("content", "") or ""
            # Weak models often serialise tool calls as TEXT (sometimes with a garbled
            # name, e.g. get_appXYZ{"app_id":...}) instead of using the structured
            # format — so the call never runs and the wizard stalls. Recover them and
            # EXECUTE the read/discovery ones (e.g. get_app to show fields) so the flow
            # progresses. Write tools are NOT auto-executed from text (a text
            # create_flow could be the model merely describing intent); those fall
            # through to the correction re-prompt below.
            recovered = _extract_text_tool_calls(candidate, valid_names)
            safe = [r for r in recovered if r["name"] not in _WRITE_TOOL_NAMES]
            if safe:
                logger.info("podio_agent_text_tool_calls_executed",
                            names=[r["name"] for r in safe])
                tool_calls = [
                    {"function": {"name": r["name"], "arguments": r["args"]}} for r in safe
                ]
                # Rewrite the just-appended assistant turn as a proper tool-call turn so
                # the following tool results are provider-valid (FIFO-matched).
                messages[-1] = {"role": "assistant", "content": None, "tool_calls": tool_calls}
        if not tool_calls:
            # Order matters: a serialised tool call (text_tool_call) must be caught
            # BEFORE template_vars / non_ascii. A text create_flow whose payload happens
            # to contain a {{placeholder}} (very common in flow comment values) would
            # otherwise be misclassified as template_vars and SILENTLY DROPPED — the loop
            # would break instead of re-prompting the model to make the real structured
            # call, so the tool never runs.
            bad_reason = (
                "garbage" if _is_garbage_reply(candidate) else
                "hallucination" if _is_hallucinated_write(candidate, steps) else
                "text_tool_call" if _has_text_tool_call(candidate) else
                "template_vars" if _has_template_vars(candidate) else
                "non_ascii" if _has_non_ascii_garbage(candidate) else
                None
            )
            if bad_reason:
                logger.warning("podio_agent_bad_reply_discarded", reason=bad_reason)
                final_text = ""
                # For text_tool_call, inject a specific correction before the closing re-prompt
                if bad_reason == "text_tool_call":
                    messages.append({
                        "role": "user",
                        "content": (
                            "You wrote a tool call as plain text/JSON instead of using the "
                            "function-calling channel, so it did NOT run. Do NOT print JSON, "
                            "code blocks, or a '{...}' object in your reply — invoke the tool "
                            "using the structured function-calling format right now. "
                            "Also: never put {{placeholder}} / {{field}} template variables in "
                            "any argument value — Podio does not substitute them. A flow "
                            "comment must be LITERAL text (it cannot inject the changed field's "
                            "value; that needs GlobiFlow). Use plain wording like "
                            "\"The Category was updated.\""
                        ),
                    })
                    # Re-enter the loop so the model can make the real call
                    continue
                # Claimed a write/attach/set succeeded but no write tool actually
                # succeeded this turn. Force the model to either DO it or report the
                # real failure — never let the false success reach the user.
                if bad_reason == "hallucination" and hallucination_retries < _MAX_HALLUCINATION_RETRIES:
                    hallucination_retries += 1
                    errored = [
                        f"{s.get('tool')} → {json.dumps((s.get('result') or {}), default=str)[:200]}"
                        for s in steps if _step_errored(s)
                    ]
                    detail = (
                        " The following tool call(s) FAILED this turn: " + "; ".join(errored)
                        if errored else
                        " No write tool was successfully called this turn."
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            "STOP. You just claimed an action succeeded, but the tool "
                            "results do NOT confirm it." + detail + " Check each tool "
                            "result before confirming. If the action still needs to be "
                            "done and is possible, make the correct tool call NOW (e.g. "
                            "if set_item_image failed because the app has no image field, "
                            "call attach_file_to_item instead). If it genuinely cannot be "
                            "done, tell the user plainly that it FAILED and why — do not "
                            "claim success. Never say 'attached'/'added'/'created'/'set' "
                            "unless a tool returned success for that exact action."
                        ),
                    })
                    continue
            else:
                final_text = candidate
                # If the model claims it "fetched the fields" but didn't actually list
                # them, append the real field list so the user can choose.
                if (last_app_fields
                        and _claims_to_present_fields(final_text)
                        and _count_field_labels_shown(final_text, [f.get("label") for f in last_app_fields]) < 2):
                    logger.info("podio_agent_field_list_appended", app=last_app_name)
                    final_text = final_text.rstrip() + "\n\n" + _render_field_list(last_app_name, last_app_fields)
            break

        for call in tool_calls:
            fn = call.get("function", {}) or {}
            raw_name = fn.get("name", "")

            # Small models routinely corrupt the tool-name field (args JSON, reasoning
            # text, or unicode noise appended/prepended, with any or no separator).
            # Recover the real name + any embedded args robustly.
            name, inline_args = _recover_tool_name(raw_name, valid_names)
            if name != raw_name:
                logger.warning(
                    "podio_agent_tool_name_recovered", raw_name=raw_name, recovered=name
                )
            if inline_args and not fn.get("arguments"):
                fn = {**fn, "arguments": inline_args}

            props = tool_schemas.get(name, {})
            args = _sanitize_tool_args(_coerce_args(fn.get("arguments")), props)

            # Forcibly remove space_id from tools where Podio rejects it at runtime,
            # regardless of whether the model or auto-fill added it.
            if name in _SPACE_ID_BLOCKED:
                args.pop("space_id", None)

            # Auto-fill the selected workspace when the model omits space_id.
            # Skip tools where Podio's API rejects space_id at runtime (e.g. get_tasks).
            if (selected_space_id and "space_id" in props
                    and not args.get("space_id") and name not in _SPACE_ID_BLOCKED):
                args["space_id"] = selected_space_id
            # get_tasks (custom REST) uses space_id and requires at least one filter.
            # Also strip the old 'space' key the model might still pass.
            if name in _SPACE_PARAM_TOOLS:
                args.pop("space", None)
                if selected_space_id and not args.get("space_id"):
                    args["space_id"] = selected_space_id

            # Strip junk/empty entries from a write tool's `fields` payload.
            if isinstance(args.get("fields"), dict):
                args["fields"] = _clean_fields(args["fields"])

            # Correct hallucinated/transposed task/item ids (e.g. set_reminder called
            # with a made-up task_id right after create_task returned the real one).
            id_corrections = _guard_object_ids(
                name, args, user_provided_ids,
                seen_task_ids, seen_item_ids, created_task_ids, created_item_ids,
            )
            for note in id_corrections:
                logger.warning("podio_agent_id_corrected", tool=name, note=note)

            logger.info("podio_agent_tool_call", tool=name, args=args)
            if name not in valid_names:
                result: dict[str, Any] = {"isError": True, "content": f"Unknown tool '{name}'"}
            else:
                try:
                    if name in files_tool_names:
                        result = await podio_files.call_tool(name, args)
                    else:
                        result = await podio_mcp.call_tool(name, args)
                except Exception as exc:  # noqa: BLE001
                    logger.error("podio_agent_tool_failed", tool=name, error=str(exc))
                    result = {"isError": True, "content": str(exc)}

            # get_app returns a large nested app object that gets truncated before the
            # model can read all field external_ids/types. Replace it with a compact,
            # complete field schema so create_item/update_item can be built correctly.
            if name == "get_app" and not (result or {}).get("isError"):
                result = _summarise_get_app_result(result)
                # The hosted MCP get_app often returns only a text summary (or nothing) —
                # no field schema. When that happens, fetch the real app object from our
                # REST client, which returns the full fields list directly from Podio.
                if isinstance(result, dict) and not result.get("fields"):
                    app_id_arg = args.get("app_id")
                    if app_id_arg:
                        try:
                            from app.services.podio_rest import podio_rest
                            raw_app = await podio_rest.get_app(int(app_id_arg))
                            rest_summary = _summarise_get_app_result({"data": raw_app})
                            if isinstance(rest_summary, dict) and rest_summary.get("fields"):
                                result = rest_summary
                                logger.info("podio_agent_get_app_rest_fallback", app_id=app_id_arg,
                                            fields=result.get("field_count"))
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("podio_agent_get_app_rest_fallback_failed",
                                           app_id=app_id_arg, error=str(exc))
                if isinstance(result, dict) and result.get("fields"):
                    last_app_fields = result.get("fields") or []
                    last_app_name = (result.get("app") or {}).get("name") or ""

            # Harvest ids from the result so later steps can validate against them.
            _collect_ids(result, {"task_id"}, seen_task_ids)
            _collect_ids(result, {"item_id"}, seen_item_ids)
            if name == "create_task":
                fresh: set[int] = set()
                _collect_ids(result, {"task_id"}, fresh)
                created_task_ids.extend(i for i in fresh if i not in created_task_ids)
            if name in ("create_item", "clone_item", "add_new_item"):
                fresh_i: set[int] = set()
                _collect_ids(result, {"item_id"}, fresh_i)
                created_item_ids.extend(i for i in fresh_i if i not in created_item_ids)

            step = {"tool": name, "args": args, "result": result}
            if id_corrections:
                step["note"] = " ".join(id_corrections)
            steps.append(step)

            content = json.dumps(result, default=str)
            if len(content) > _MAX_TOOL_OUTPUT_CHARS:
                # Prefix with a plain-text note so the model doesn't try to
                # "close" the broken JSON by emitting }}}}} or ))))) characters.
                content = (
                    f"[Tool result truncated — showing first {_MAX_TOOL_OUTPUT_CHARS} of "
                    f"{len(content)} chars. Summarise what you have; do not emit closing brackets.]\n"
                    + content[:_MAX_TOOL_OUTPUT_CHARS]
                )
            messages.append({"role": "tool", "content": content, "tool_name": name})

    # If the model kept calling tools without ever answering (or produced garbage),
    # explicitly ask it to summarise in plain prose, then fall back to a step-built
    # summary if it still outputs garbage.
    if not final_text:
        if steps:
            messages.append({
                "role": "user",
                "content": (
                    "Summarise what you just did in 1-2 plain sentences. "
                    "No JSON, no code blocks, no brackets — plain text only."
                ),
            })
        else:
            # No tools called yet — model hallucinated. Force it to call the tool.
            messages.append({
                "role": "user",
                "content": (
                    "You have not called any Podio tool yet. "
                    "You MUST call the appropriate tool now to complete this action. "
                    "Do NOT reply with text — make the tool call."
                ),
            })
        closing = await model_gateway.chat(messages, model=model, num_predict=_NUM_PREDICT_FINAL)
        candidate = (closing or {}).get("content", "") or ""
        if not _is_garbage_reply(candidate):
            final_text = candidate
        else:
            # Still garbage — build a minimal summary from step results directly.
            ok_tools = [s["tool"] for s in steps if not (s.get("result") or {}).get("isError")]
            err_tools = [s["tool"] for s in steps if (s.get("result") or {}).get("isError")]
            parts: list[str] = []
            if ok_tools:
                parts.append(f"Completed: {', '.join(ok_tools)}.")
            if err_tools:
                parts.append(f"Failed: {', '.join(err_tools)}.")
            final_text = " ".join(parts) or "Done — all Podio actions finished."

    return {"success": True, "reply": final_text, "steps": steps, "model": model}
