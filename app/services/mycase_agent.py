from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime
from typing import Any

import structlog

from app.services.model_gateway import model_gateway
from app.services.mycase_client import mycase_client

logger = structlog.get_logger(__name__)

_MAX_STEPS = 20
_MAX_TOOL_OUTPUT_CHARS = 6000
_NUM_PREDICT = 1200
_NUM_PREDICT_FINAL = 1500

# ── Ollama tool capping ──────────────────────────────────────────────────────────
# 46 read tools would overwhelm a small local model's tool-selection reasoning (the
# same problem the Podio agent solved — see CLAUDE.md session log). Cap to the core
# discovery tools + whatever the message's keywords suggest is relevant.

_OLLAMA_MAX_TOOLS = 16
_OLLAMA_TOOL_ALWAYS = {"get_cases", "get_case", "get_clients", "get_client", "get_me", "get_firm"}
_OLLAMA_TOOL_PRIORITY: dict[str, set[str]] = {
    "case": {"get_cases", "get_case", "get_case_stages", "get_case_roles", "get_client_cases", "search_cases"},
    "case number": {"search_cases"}, "find the case": {"search_cases"}, "find case": {"search_cases"},
    "client": {"get_clients", "get_client", "get_client_cases", "get_client_notes", "get_client_message_threads"},
    "company": {"get_companies", "get_company"},
    "lead": {"get_leads", "get_lead"},
    "document": {"get_documents", "get_document", "get_case_documents", "get_document_versions",
                 "get_document_versions_all", "download_document", "download_document_version",
                 "find_cases_with_documents"},
    "folder": {"get_case_folder", "get_case_folder_tree", "get_folder_documents", "get_folder_subfolders"},
    "folder structure": {"get_case_folder_tree"}, "subfolder": {"get_folder_subfolders", "get_case_folder_tree"},
    "event": {"get_events"}, "calendar": {"get_events"}, "meeting": {"get_events"},
    "expense": {"get_expenses", "get_expense"},
    "invoice": {"get_invoices", "get_invoices_by_date", "get_case_invoices", "get_invoice_payments"}, "payment": {"get_invoice_payments"},
    "created on": {"get_invoices_by_date"}, "due on": {"get_invoices_by_date"}, "invoiced": {"get_invoices_by_date"},
    "invoices for": {"get_case_invoices"}, "invoices related to": {"get_case_invoices"},
    "billing": {"get_invoices", "get_invoice_payments", "get_expenses", "get_time_entries"},
    "time entry": {"get_time_entries", "get_time_entry", "lookup_utbms_code"}, "hours": {"get_time_entries"},
    "utbms": {"lookup_utbms_code"}, "ledes": {"lookup_utbms_code"},
    "report": {"aggregate_cases", "get_custom_fields", "get_case_stages", "get_practice_areas"},
    "count": {"aggregate_cases", "get_custom_fields", "get_case_stages"},
    "group by": {"aggregate_cases"}, "grouped by": {"aggregate_cases"}, "per agent": {"aggregate_cases"},
    "breakdown": {"aggregate_cases"}, "how many": {"aggregate_cases"},
    "task": {"get_tasks"},
    "staff": {"get_staff", "get_individual_staff", "get_me"},
    "note": {"get_note", "get_case_notes", "get_client_notes"},
    "custom field": {"get_custom_fields", "get_custom_field", "get_custom_field_list_options"},
    "webhook": {"get_webhook_subscriptions"},
    "location": {"get_locations"},
    "practice area": {"get_practice_areas"},
    "referral": {"get_referral_sources"},
    "people group": {"get_people_groups"}, "group": {"get_people_groups"},
    "message": {"get_client_message_threads"},
    "call": {"get_calls"},
}


def _filter_tools_for_ollama(tool_specs: list[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    msg_lower = (message or "").lower()
    priority: set[str] = set(_OLLAMA_TOOL_ALWAYS)
    for kw, names in _OLLAMA_TOOL_PRIORITY.items():
        if kw in msg_lower:
            priority.update(names)
    prioritised = [t for t in tool_specs if t["function"]["name"] in priority]
    rest = [t for t in tool_specs if t["function"]["name"] not in priority]
    return (prioritised + rest)[:_OLLAMA_MAX_TOOLS]


# ── Bad-reply / text-tool-call recovery (mirrors podio_agent.py's proven guards) ──

_REPEAT_RUN_RE = re.compile(r"([^\w\s])\1{14,}")


def _is_garbage_reply(text: str) -> bool:
    s = (text or "").strip()
    if not s or len(s) < 15:
        return False
    if _REPEAT_RUN_RE.search(s):
        return True
    noise = sum(1 for c in s if c in "{}[]|\\/ \n\t")
    if noise / len(s) > 0.65:
        return True
    punct = sum(1 for c in s if not c.isalnum() and not c.isspace())
    return punct / len(s) > 0.6


_TEXT_TOOL_CALL_RE = re.compile(r'\b[a-z][a-z0-9_]{2,}["\']?\s*[:(]?\s*\{\s*["\']')


def _has_text_tool_call(text: str) -> bool:
    return bool(_TEXT_TOOL_CALL_RE.search(text or ""))


def _has_non_ascii_garbage(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    non_ascii = sum(1 for c in s if ord(c) > 127)
    return non_ascii / len(s) > 0.1


def _recover_tool_name(raw_name: str, valid_names: set[str]) -> tuple[str, dict | None]:
    name = (raw_name or "").strip()
    if name in valid_names:
        return name, None
    inline_args: dict | None = None
    brace = name.find("{")
    if brace != -1:
        try:
            parsed = json.loads(name[brace:])
            if isinstance(parsed, dict):
                inline_args = parsed
        except (json.JSONDecodeError, ValueError):
            pass
    if name.startswith("{"):
        return name, inline_args
    norm = re.sub(r"[^A-Za-z0-9_]+", " ", name)
    ordered = sorted(valid_names, key=len, reverse=True)
    for cand in ordered:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(cand)}(?![A-Za-z0-9_])", norm):
            return cand, inline_args
    for cand in ordered:
        if name.startswith(cand):
            return cand, inline_args
    return name, inline_args


def _extract_balanced_json(text: str, start: int) -> str | None:
    depth, in_str, esc = 0, False, False
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


_CALL_NAME_KEYS = ("name", "tool", "tool_name", "function_name")
_CALL_ARGS_KEYS = ("arguments", "parameters", "args", "input")


def _self_describing_call(obj: dict[str, Any], valid_names: set[str]) -> tuple[str, dict[str, Any]] | None:
    fn = obj.get("function")
    if isinstance(fn, dict):
        inner = _self_describing_call(fn, valid_names)
        if inner:
            return inner
    name_raw = next(
        (obj[k] for k in _CALL_NAME_KEYS if isinstance(obj.get(k), str) and obj[k].strip()), None,
    )
    if name_raw is None:
        return None
    name, _ = _recover_tool_name(name_raw, valid_names)
    if name not in valid_names:
        return None
    args_val: Any = next((obj[k] for k in _CALL_ARGS_KEYS if k in obj), None)
    if isinstance(args_val, str):
        try:
            args_val = json.loads(args_val)
        except (json.JSONDecodeError, ValueError):
            args_val = {}
    if not isinstance(args_val, dict):
        args_val = {}
    return name, args_val


def _tool_name_for_json(text: str, brace_pos: int, valid_names: set[str]) -> str:
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
            parsed = json.loads(obj)
        except (json.JSONDecodeError, ValueError):
            i += 1
            continue
        if isinstance(parsed, dict):
            self_described = _self_describing_call(parsed, valid_names)
            if self_described:
                name, call_args = self_described
                calls.append({"name": name, "args": call_args})
            else:
                name = _tool_name_for_json(text, i, valid_names)
                if name in valid_names:
                    calls.append({"name": name, "args": parsed})
        i += len(obj)
    return calls


def _coerce_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _sanitize_tool_args(args: dict[str, Any], props: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in args.items():
        if value is None:
            continue
        spec = props.get(key) or {}
        declared = spec.get("type")
        if declared in ("number", "integer") and isinstance(value, str):
            s = value.strip()
            try:
                value = int(s) if (declared == "integer" or s.isdigit()) else float(s)
            except ValueError:
                continue
        elif declared == "integer" and isinstance(value, float) and value.is_integer():
            value = int(value)
        elif declared == "string" and not isinstance(value, str):
            value = str(value)
        elif declared == "boolean" and isinstance(value, str):
            value = value.strip().lower() in ("true", "1", "yes")
        out[key] = value
    return out


# ── "Claimed to show records but didn't" completeness guard (Session 18/20 pattern) ──

# Reference/config tools (mirrors the frontend's _REFERENCE_TOOLS) — the chat UI
# never auto-renders a table for these, so they must never be treated as "shown in
# the table below" by the completeness guard below; a reply about these needs the
# actual values printed in text, not a pointer at a table that doesn't exist.
_REFERENCE_TOOLS = frozenset({
    "get_case_stages", "get_case_roles", "get_practice_areas", "get_locations",
    "get_referral_sources", "get_people_groups", "get_custom_fields",
    "get_custom_field_list_options",
})

_LISTING_INTENT_WORDS = (
    "show", "list", "display", "give me", "get all", "see all", "all the",
    "what are the", "which ", "pull up", "everything in", "table",
)
_COUNT_ONLY_WORDS = ("how many", "count of", "number of", "total number", "how much")


def _wants_record_listing(message: str) -> bool:
    m = (message or "").lower()
    return any(w in m for w in _LISTING_INTENT_WORDS)


def _wants_count_only(message: str) -> bool:
    """A pure count question ('how many clients do we have') is correctly answered
    from item_count off a single, cheap call — it never needs the actual rows, so
    fetching only 1 is CORRECT, not incomplete. Only counts as count-only when there's
    no listing wording too ('how many are open, list them' still wants the rows)."""
    m = (message or "").lower()
    return any(w in m for w in _COUNT_ONLY_WORDS) and not _wants_record_listing(message)


# ── "Stated a number without ever calling the matching tool" guard ───────────────
# This agent is read-only, so the earlier assumption was "no write-hallucination
# guard needed" — but a model can just as easily fabricate a plausible-sounding
# READ result ("Found 1,023 leads") without ever successfully calling get_leads at
# all. Nothing else catches this: the completeness guard only fires when there IS
# real fetched data to compare against; if there's none, a confident, well-formed,
# entirely made-up number sails straight through as the final answer.

_RESOURCE_WORD_TO_KEY = {
    "client": "clients", "lead": "leads", "case": "cases", "company": "companies",
    "companies": "companies", "invoice": "invoices", "document": "documents",
    "contact": "contacts", "expense": "expenses", "task": "tasks", "event": "events",
    "note": "notes", "call": "calls", "staff": "staff",
}
_RESOURCE_CLAIM_RE = re.compile(
    r"\b\d[\d,]*\s+(?:\w+\s+){0,2}(client|lead|case|compan(?:y|ies)|invoice|document|contact|expense|task|event|note|call|staff)s?\b",
    re.IGNORECASE,
)


def _unverified_resource_claims(
    text: str, items_by_resource: dict[str, dict[Any, dict]], resource_totals: dict[str, int],
) -> list[str]:
    """Resources the reply states a specific number for, that were NEVER actually
    fetched (no items, no item_count) this turn — i.e. the number can only have been
    guessed, not read off a real tool result."""
    claimed: set[str] = set()
    for m in _RESOURCE_CLAIM_RE.finditer(text or ""):
        word = m.group(1).lower()
        resource = _RESOURCE_WORD_TO_KEY.get(word, word + "s")
        if resource not in items_by_resource and resource not in resource_totals:
            claimed.add(resource)
    return sorted(claimed)


def _row_id(item: dict[str, Any]) -> Any:
    """Not every tool's rows use 'id' as the identifying key — aggregate_cases uses
    'case_id' to avoid ambiguity with the report's own id-like fields. Check both."""
    return item.get("id") if item.get("id") is not None else item.get("case_id")


def _incomplete_pagination_notes(
    items_by_resource: dict[str, dict[Any, dict]], resource_totals: dict[str, int],
) -> list[str]:
    """MyCase's Item-Count header is the TRUE total across every page, not just the
    current one — if we stopped paginating (or the model never continued past page 1)
    before reaching that total, the table/CSV the user sees is silently incomplete.
    This is a data-integrity problem, not a "did the model phrase it right" one, so it
    is surfaced unconditionally — never silently let a claimed/shown count be wrong."""
    notes: list[str] = []
    for resource, total in resource_totals.items():
        fetched = len(items_by_resource.get(resource, {}))
        if fetched and fetched < total:
            notes.append(
                f"Note: only {fetched} of {total} total {resource} were fetched (pagination "
                f"stopped early) — the table/CSV below for {resource} is INCOMPLETE. Ask me to "
                f"continue and I'll fetch the rest."
            )
    return notes


def _augment_reply_with_missing_data(
    text: str, items_by_resource: dict[str, dict[Any, dict]],
    resource_totals: dict[str, int] | None = None, count_only: bool = False,
) -> str:
    # There used to also be a check here appending a "the data is shown in a table
    # below" pointer whenever the reply didn't literally contain every row's id —
    # from when models had to be forced to prove they weren't dodging. It's gone:
    # the frontend now ALWAYS renders the real result table from the raw tool data
    # (see primaryResultGroups), independent of the reply text, and the model is
    # now instructed to always give exactly that short pointer itself — so the old
    # check just produced a redundant, near-identical second copy of it every time.
    # A pure count question intentionally fetches only 1 row per resource (see
    # PAGINATION in the system prompt) — that's correct, not incomplete, so the
    # disclosure below would be actively misleading ("the table is incomplete")
    # for a question that never wanted a table in the first place.
    if not count_only:
        notes = _incomplete_pagination_notes(items_by_resource, resource_totals or {})
        if notes:
            logger.info("mycase_agent_incomplete_pagination", notes=notes)
            text = (text.rstrip() + "\n\n" + "\n\n".join(notes)) if text else "\n\n".join(notes)
    return text


# download_document / download_document_version — the chat UI renders a dedicated
# "Download" button for these (see mycase-agent/page.tsx's DownloadCard), sourced
# straight from the real tool result, not the model's text. The system prompt
# tells the model not to paste the raw presigned URL itself, but that's not
# reliably followed (a weaker model has been observed pasting it as a markdown
# link — the URL is long, only valid ~1 minute, and easy for the model to render
# as inert plain text instead of a working link), so it's stripped deterministically
# below rather than trusted to prompting alone.
_DOWNLOAD_TOOLS = {"download_document", "download_document_version"}
_RESOURCE_KEY_ALIASES = {"invoices_by_date": "invoices", "case_invoices": "invoices"}
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def _strip_download_link_urls(text: str, download_urls: list[str]) -> str:
    """Remove any markdown link (or bare occurrence) of a URL this turn actually
    returned from download_document/download_document_version, keeping just the
    link's label text — the real download affordance is the button the UI renders
    from the raw tool result, not anything in the reply text. Matches on
    scheme+host+path (ignoring the query string) as well as an exact match, since
    a model retyping a long presigned URL can subtly mangle the query params
    without meaning to."""
    if not download_urls or not text:
        return text

    def _base(u: str) -> str:
        p = urllib.parse.urlparse(u)
        return f"{p.scheme}://{p.netloc}{p.path}"

    exact = set(download_urls)
    bases = {_base(u) for u in download_urls}

    def _sub_link(m: re.Match) -> str:
        label, href = m.group(1), m.group(2)
        return label if (href in exact or _base(href) in bases) else m.group(0)

    text = _MD_LINK_RE.sub(_sub_link, text)
    # Defensive second pass: a bare (non-markdown) occurrence of the exact URL.
    for url in download_urls:
        text = text.replace(url, "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


_SYSTEM_PROMPT = """You are a MyCase legal practice management assistant. Execute tasks accurately using available tools. Never fabricate data.

CORE RULES
- This agent is READ-ONLY. You can look up/search/list data but CANNOT create, update, or delete anything in MyCase. If the user asks to create/update/delete a record, tell them plainly that's not supported yet — do not pretend to do it.
- Never guess or fabricate data — only return what tools return.
- NEVER write function calls like tool_name({args}) in plain text. Only use structured tool calls.
- NEVER output JSON or code blocks in plain text. Summarise tool results in clear prose or a Markdown table.
- KEEP LISTING REPLIES SHORT — but ONLY for multi-field RECORD data (cases, clients, invoices, documents, time entries, etc.): the chat UI already renders those as a full interactive table (every field, every row, any size) directly BELOW your reply, with its own "Download CSV" button, automatically and independent of anything you write. So for records, do NOT hand-type them into a Markdown table and do NOT retype the data as CSV text — both just waste tokens duplicating something already on screen. Reply with ONLY a one-line count/acknowledgment (e.g. "Found 10 cases — see the table below."). You have no separate file-generation ability and don't need one — never say you "can't produce a downloadable file"; the table is already there. Always say "below", never "above" — the table renders AFTER your reply text, not before it.
- THIS DOES NOT APPLY TO REFERENCE/CONFIG DATA (case stages, case roles, practice areas, locations, referral sources, people groups, custom field names — see KEY RESOURCES below): those are simple single-value lists that NEVER get an automatic table in the UI, no matter how many there are — there is no "table below" for them, ever. For these, you MUST print every actual value directly in your reply (a plain comma-separated list or short bulleted list is fine) — saying "see the table below" or just giving a count would leave the user with nothing, since no table exists to point at.
- NO UNSOLICITED ANALYSIS: do not add "Key Observations", breakdowns, trends, or commentary about the data unless the user explicitly asked for a summary, breakdown, or analysis. A plain "show me X" gets the one-line acknowledgment above and nothing more — extra analysis is wasted tokens the user didn't ask for.
- Always show each record's numeric `id` — it's needed to look up more detail (e.g. get_case, get_client) or reference the record later. BUT this id is an internal API identifier only — it is NOT searchable anywhere in the MyCase web app. Whenever a resource has its own human-readable identifier (a case's `case_number`, an invoice's number, etc.), always show and LEAD with that as the reference the user can actually use in MyCase; present the numeric id as a secondary "internal id" only, never as something to search for in MyCase's UI.
- Do not speculate about the MyCase web app's UI, URLs, or navigation (e.g. guessing a case detail page URL) — you have no documented information about the web app, only the API. If asked how to find something in the MyCase UI, say you don't have that information rather than guessing a URL or path that might be wrong.
- CHECK EVERY TOOL RESULT: a result containing "success": false or "error" means the call FAILED. Report failures plainly; never claim data exists when the call errored.
- Nested related objects (e.g. a case's "client", an invoice's "case") are returned as {"id": N} ONLY by default — to see more fields on them, call the resource's own get tool with that id (e.g. get_client(id) for a case's client).

PAGINATION
- List endpoints default to page_size=25 (max 1000). Every response includes item_count — the TRUE TOTAL across ALL pages, always accurate from page 1 — and next_page_token, present only when another page exists.
- "HOW MANY X do we have" / a pure count question: call the list endpoint ONCE (page_size=1 is enough — you don't need the rows) and report item_count VERBATIM as the answer. Do not paginate through everything just to count, and do NOT compute or estimate a count yourself from however many rows you can see — item_count is already the exact, complete answer; reporting anything other than that exact number (rounding, guessing, recalculating) is simply wrong.
- "LIST/SHOW ALL X" or anything that needs the actual records (not just a count), e.g. a CSV export: page_size=1000, and you MUST keep calling again with next_page_token until it comes back null/absent before you're done — stopping after page 1 leaves the table/CSV the user sees silently missing the rest, even though you may have already correctly reported the true item_count. (The pagination cursor expires after 3 days — irrelevant within one conversation.)
- filter updated_after (ISO 8601, e.g. "2024-01-01T00:00:00Z") narrows most list endpoints to recently changed records — use it for "what changed / what's new" requests.

DATES AND TIME ZONES
- Always include a timezone offset in any ISO 8601 date/time you pass to a tool (e.g. "2024-01-01T00:00:00Z" for UTC, or "2024-01-01T09:00:00-05:00" for EST). If you omit the offset, MyCase assumes UTC — this can silently shift a "today" filter by several hours for a user not in UTC.

ERROR HANDLING
- A tool result with "success": false means the call failed — read the "error" message, which includes MyCase's own description plus the affected field when available (e.g. a 422 names which field was invalid). Report the real reason to the user; never guess.
- A 401 usually means the access token expired (they last 24h) — tell the user to reconnect via Settings - MyCase or click Connect MyCase again. A 403 means a MyCase permission issue, not something you can work around. 429 (rate limited) is retried automatically — you won't normally see it.

KEY RESOURCES
- Cases (matters): get_cases / get_case / get_client_cases — status is "open" or "closed"; includes clients, companies, staff, billing_type, outstanding_balance, practice_area, case_stage.
- FINDING a specific case by number or name ("get the case numbered X", "find the case for X", "the case named X"): MyCase has NO server-side search/filter for case_number or name — call search_cases(query=X) instead of get_cases. search_cases walks every page internally and returns ONLY the matching case(s), so you never have to eyeball-match a case out of a large unrelated batch (unreliable, and exposes irrelevant cases to the user). Only fall back to get_case(id) if you already have the exact numeric id from earlier in the conversation or a prior tool result. search_cases takes ONLY `query` (required) and optional `status` — it does NOT accept client_id, field_client, or any other get_case/get_cases parameter; calling it that way just fails validation.
- A CASE'S CLIENT(S): a case's `clients` array holds `{"id": N, ...}` per client. PREFER ONE call — get_case(case_id, field_client="id,first_name,last_name,email") — which expands each client inline within the SAME case result, so the whole answer (case + client name/email) stays in one table instead of splitting into a second one. Only use the separate get_client(id) when the user is asking about a client directly, not via a case. If get_client(id) 404s, that id is a stale/orphaned reference (the client record no longer exists in MyCase) — say so plainly and do NOT retry the same id again; retrying an identical call that already failed wastes a step and never produces a different result.
- Clients (people) vs Companies vs Leads are separate resources — get_clients/get_client, get_companies/get_company, get_leads/get_lead. A "client" is always a person; a company is an org; a lead is a pre-intake prospect.
- Documents: "show/list/find all documents for case X" → get_case_documents(case_id) — this is the COMPLETE list for that case in ONE call, regardless of folder/subfolder. Do NOT walk get_case_folder/get_folder_subfolders/get_folder_documents just to list a case's documents. get_documents = firm-wide (all cases). get_document(id) = one document's own metadata. download_document / download_document_version return a temporary signed URL (do not fetch the bytes yourself).
- DOWNLOAD LINKS: after a successful download_document/download_document_version call, the UI automatically renders a real "Download" BUTTON below your reply — do NOT paste the raw download_url into your reply text as a markdown link (it's long, easy to mis-render as plain text the user has to copy/paste, and would just duplicate the button). Instead give a short acknowledgment and state the tool result's OWN `expires_in` value (never invent a duration — download_document is valid only ~1 minute, download_document_version ~1 hour), e.g. "Here's the download — the link expires in 1 minute, so click the button below right away." If the user reports the download failed with an XML/S3 error (anything mentioning "anonymous GET requests" or an `<Error>...</Error>` block), that means the link EXPIRED — it is not a bug or a broken document; just call download_document/download_document_version again for a fresh one (a new button will render).
- "Get the folder structure for case X" / "show me the folders/subfolders for case X": this IS a folder-structure request (unlike the document-list case above) — call get_case_folder_tree(case_id) once; it recursively returns the whole tree (every folder + its documents) in one call. Only use get_folder_subfolders(folder_id)/get_folder_documents(folder_id) individually when the user gives you a SPECIFIC known folder id and wants just that one level (e.g. "what's in folder 55").
- "Find N cases that HAVE documents": there is no server-side filter for this — use aggregate_cases-style deterministic tooling, NOT a manual sample. Call find_cases_with_documents(limit=N) — it scans every document firm-wide, tallies which cases they belong to, and returns the N cases with the most documents (each case row includes document_count). Do NOT just grab the first few cases from get_cases and hope some of them happen to have documents — that produces wrong/incomplete answers for exactly the reason aggregate_cases/search_cases exist: an LLM sampling a handful of records cannot reliably answer a question that requires checking across the whole dataset.
- Billing: get_invoices (NOTE: only invoices with online payments enabled are returned by default — pass only_allowed_online_payments=false to see all), get_invoice_payments, get_expenses, get_time_entries. Time entries may carry utbms_activity_code / utbms_task_code (LEDES billing codes) — use lookup_utbms_code(code) to explain what one means rather than guessing; an activity code always has an accompanying task code, but a task code can stand alone.
- INVOICES "CREATED/DUE/DATED on|before|after X": get_invoices has NO server-side filter for an exact date — its only date param (updated_after) is a floor on created-OR-updated time, NOT the same as "created on X", and has no relation to invoice_date/due_date at all. Using get_invoices alone for a date-specific question WILL return the wrong set (invoices merely touched/updated on that date, not created on it) — call get_invoices_by_date(date_field="created_at"|"updated_at"|"invoice_date"|"due_date", on=/after=/before=) instead; it returns only the matching invoices, already filtered. Never try to eyeball-filter get_invoices' raw output yourself by comparing dates in your head — a past incident showed this failing invisibly: the reply correctly said "4 matched" but the chat's own result table (built directly from the tool result, not your text) still showed all 20 unfiltered rows, since the underlying get_invoices call itself never actually filtered by date.
- INVOICES "FOR A CASE" ("invoices for case X", "invoices related to the Asylum case for Moise Pierre"): call get_case_invoices(case_id=... or case_query=...) — it resolves the case AND filters invoices in one call (get_invoices has no server-side case filter). CRITICAL — if it returns matched_case=null and an empty items[] (case_query matched no case), that means the case genuinely was not found: say so plainly (e.g. "No case found matching X") and suggest an alternative (search by the client's name, or ask the user for the exact case id/case number) exactly as instructed in its `note`. Do NOT then call get_invoices or get_cases yourself to keep looking "just in case" — a past incident did exactly that, burning a pointless full-firm scan of 1,000 invoices that could never have matched (there was no case to filter by) and confusing the user with an irrelevant "found 1,000 invoices in the system" aside. A failed lookup ends with a clear "not found" + your recommendation, never a fallback scan of an unrelated dataset. If it returns `candidates` (multiple cases matched), list them and ask the user to pick one before fetching anything else.
- Calendar: get_events. Tasks: get_tasks. Notes: get_case_notes / get_client_notes / get_note (by id).
- Reference/config data (rarely change): get_case_stages, get_case_roles, get_practice_areas, get_locations, get_referral_sources, get_people_groups, get_custom_fields (+ get_custom_field_list_options for list-type fields). IMPORTANT: these return the firm's DEFINED list of possible values (e.g. get_case_stages returns every stage NAME the firm has configured, however many that is) — this is config data, not case data. Its row count has NOTHING to do with how many cases are actually in any given stage; never present it, or its count, as if it were a filtered case result. These NEVER get an automatic table in the chat UI (unlike cases/clients/invoices/etc.) — when the user asks "what stages/roles/practice areas/locations/custom fields exist", you must list every actual value in your reply text, not just a count (see THIS DOES NOT APPLY TO REFERENCE/CONFIG DATA above).
- get_me = the current authorized user's own staff profile. get_firm = the firm's name/URL.

CUSTOM FIELDS (e.g. "Case Type", "Processing Agent", any firm-defined field on a case/client/company)
- A case/client/company's custom_field_values[] array ALREADY includes each value by default — {"custom_field": {"id": N}, "value": "...", ...}. You never need field[custom_field] to see the value.
- field[custom_field] (on get_cases/get_case) supports ONLY "id,field_type" — there is NO "name" or "value" option. Passing either 400s with "Unsupported field". Do not guess sub-field names — this is the complete list.
- To find a custom field's NAME (e.g. to know that field id 1130203 is "CASE TYPE"), call get_custom_fields() ONCE and match its id against custom_field_values[].custom_field.id in each case — do not try to fetch the name via field expansion, it isn't available that way.
- Filtering cases by a custom field's value (e.g. "Case Type = Asylum") is NOT a filter[...] query parameter — get_cases has no such filter. For a SINGLE case or a small known set, you may filter in your own reasoning. For anything involving counting/grouping/reporting across many cases, use aggregate_cases instead (see below) — do NOT try to hand-count from get_cases results.

REPORTS: FILTERING, EXCLUDING, AND COUNTING CASES
- Any request shaped like "count/group/breakdown of cases by X", "how many active Y cases", "case count per agent", etc. → call aggregate_cases. NEVER try to compute a count or group-by yourself by calling get_cases and reading through however many pages come back — beyond a handful of cases this is unreliable (large result sets don't fully fit in what you're shown) and produces wrong numbers even when it looks like it worked. aggregate_cases does the fetching, filtering, exclusion, and counting in code, accurate no matter how many cases match, and hands you back a small ready-to-present result.
- Before calling aggregate_cases, resolve every field reference the user gave you loosely, in plain language, into the EXACT values MyCase uses:
  1. A stage description like "Closed" or "Immigration Documents Submitted/Mailed/Uploaded" → call get_case_stages() and find the real stage strings (e.g. "CLOSED", "IMMIGRATION- SUBMITTED (MAIL/UPLOAD PACKAGE)"). Whether that resolved name goes into case_stages or exclude_case_stages depends on what the user asked: "where stage is X" / "in the X stage" → case_stages=["X"] (KEEPS only that stage); "excluding X" / "not in X" / "everything except X" → exclude_case_stages=["X"] (DROPS that stage). Do not guess or paraphrase these — pass the exact strings from get_case_stages(), and never substitute group_by for an actual stage filter (group_by only labels/counts, it does not remove non-matching rows).
  2. A custom field name like "Case Type" or "Processing Agent" → call get_custom_fields() to confirm the exact field name (e.g. "CASE TYPE", "PROCESSING AGENT") to use as a custom_field_filters key or group_by value.
  3. practice_area is a builtin field — pass the value as the user said it (e.g. "Immigration"), no lookup needed.
  4. status ("open"/"closed") is a separate dimension from case_stage — a case's status and its case_stage name can disagree (e.g. status=open while sitting in a stage literally named "CLOSED"); pass both exactly as the user described them, don't assume one implies the other.
- Then call aggregate_cases ONCE with all the resolved filters/exclusions/group_by together — its items[] result is already the complete, correctly-computed report (every field of each surviving case, plus that case's group_name/case_count); present it as-is (following the KEEP LISTING REPLIES SHORT rule above — state the count, the table is already shown), do not re-filter or re-count it yourself. Unlike get_cases/get_case, aggregate_cases already breaks each custom field out into its own column named with the real field name (e.g. "CASE TYPE") — there is no nested custom_field_values blob to unpack here. It also already resolves `client_name` and `assigned_attorney` (from the case's clients/lead_lawyer staff) into readable columns — never present the raw `clients`/`staff` id arrays instead. The `PROCESSING AGENT` column is already whitespace-cleaned with blank/null shown as "(unassigned)" — a separate `PROCESSING AGENT (original)` column holds the untouched raw value if the user specifically wants to see it.
- Date-range reporting ("cases opened/closed/updated between X and Y", "cases opened this quarter", etc.): pass opened_after/opened_before, closed_after/closed_before, and/or updated_after/updated_before (YYYY-MM-DD) to aggregate_cases — MyCase has no server-side filter for opened_date/closed_date at all, so these are computed exactly in Python; never try to eyeball-filter by date from a get_cases result yourself.
- "Days in current stage" / "how long has this case been in its stage": NOT available. MyCase's public API has no case-history/timeline endpoint — the per-stage day counts shown in MyCase's own web UI ("Case Timeline by Stage" widget) are computed internally by MyCase and are not exposed here. Say so plainly if asked; do not estimate this from `updated_at` (which changes on ANY case edit, not just a stage change) and present it as if it were the real answer.
- "Case Owner": not a real MyCase field or custom field in this account (confirmed against the actual custom field list) — if asked, say it isn't available rather than guessing or substituting a different field silently.

OUTPUT FORMAT
- For a plain listing/show request over RECORD data (cases, clients, invoices, etc.): one line — "Found X records — see the table below." Nothing more (see KEEP LISTING REPLIES SHORT above). If the user explicitly asks for a summary/breakdown/analysis on top of that, lead the human-readable identifier (case_number, invoice number, client/company name, etc.) rather than the internal numeric id when referencing specific records in your analysis.
- For a request about REFERENCE/CONFIG data (stages, roles, practice areas, locations, custom fields, etc.): list every actual value in the reply — there is no table to point at for these (see KEY RESOURCES above).
- If no records found: say so plainly, don't imply the search failed.
- Never output raw JSON."""


def _current_date_header() -> str:
    now = datetime.now().astimezone()
    return (
        f"CURRENT DATE AND TIME: It is {now:%A, %d %B %Y, %H:%M} ({now:%Z%z}; ISO date "
        f"{now:%Y-%m-%d}). Use this — never a training-data date — for any relative date "
        "reasoning (today, this week, overdue, upcoming) or filter[updated_after] value.\n\n"
    )


async def run_mycase_agent(message: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Run one turn of the MyCase agent. Mirrors run_podio_agent's shape, simplified
    for a read-only tool set (no write-gating needed) — but a read CAN still be
    hallucinated (a confident, plausible "Found 1,023 leads" with no real get_leads
    call behind it), so _unverified_resource_claims still guards against that below."""
    from app.services.settings_service import get_setting

    model = (await get_setting("mycase_agent_model")) or f"ollama:{model_gateway.route_model('chat')}"

    if not await mycase_client.is_connected():
        return {
            "success": False, "reply": "", "steps": [],
            "error": "MyCase is not connected. Add an access token in Settings - MyCase.",
        }

    tool_specs = await mycase_client.list_tools()
    provider_name, _ = model_gateway._split_model(model)
    if provider_name == "ollama":
        tool_specs = _filter_tools_for_ollama(tool_specs, message)
    valid_names = {s["function"]["name"] for s in tool_specs}
    tool_schemas = {
        s["function"]["name"]: (s["function"].get("parameters") or {}).get("properties", {}) or {}
        for s in tool_specs
    }

    system_prompt = _current_date_header() + _SYSTEM_PROMPT
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for turn in history or []:
        role, content = turn.get("role"), turn.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    steps: list[dict[str, Any]] = []
    final_text = ""
    items_by_resource: dict[str, dict[Any, dict[str, Any]]] = {}
    resource_totals: dict[str, int] = {}
    download_urls: list[str] = []
    count_only = _wants_count_only(message)

    for _ in range(_MAX_STEPS):
        assistant_msg = await model_gateway.chat(messages, tools=tool_specs, model=model, num_predict=_NUM_PREDICT)
        if assistant_msg and assistant_msg.get("tool_calls"):
            assistant_msg = {**assistant_msg, "content": None}
        elif not (assistant_msg or {}).get("content") and not (assistant_msg or {}).get("tool_calls"):
            # A genuinely empty turn (no content, no tool_calls) gets appended to
            # `messages` and can be RE-SENT on the next loop iteration (e.g. after a
            # bad-reply retry) — several providers (confirmed: Mistral) reject an
            # assistant message with neither as invalid ("must have content or
            # tool_calls, but not none"), so this stays a placeholder, never truly
            # empty, regardless of which provider produced the empty turn.
            assistant_msg = {**(assistant_msg or {}), "role": "assistant", "content": "(no response)"}
        messages.append(assistant_msg or {})

        tool_calls = (assistant_msg or {}).get("tool_calls") or []
        candidate = (assistant_msg or {}).get("content", "") or ""
        if not tool_calls:
            recovered = _extract_text_tool_calls(candidate, valid_names)
            if recovered:
                logger.info("mycase_agent_text_tool_calls_executed", names=[r["name"] for r in recovered])
                tool_calls = [{"function": {"name": r["name"], "arguments": r["args"]}} for r in recovered]
                messages[-1] = {"role": "assistant", "content": None, "tool_calls": tool_calls}

        if not tool_calls:
            unverified = _unverified_resource_claims(candidate, items_by_resource, resource_totals)
            bad_reason = (
                "garbage" if _is_garbage_reply(candidate) else
                "text_tool_call" if _has_text_tool_call(candidate) else
                "non_ascii" if _has_non_ascii_garbage(candidate) else
                "unverified_claim" if unverified else
                None
            )
            if bad_reason:
                logger.warning("mycase_agent_bad_reply_discarded", reason=bad_reason, unverified=unverified or None)
                final_text = ""
                if bad_reason == "text_tool_call":
                    messages.append({
                        "role": "user",
                        "content": (
                            "You wrote a tool call as plain text/JSON instead of using the "
                            "function-calling channel, so it did NOT run. Do NOT print JSON or "
                            "a '{...}' object in your reply — invoke the tool using the "
                            "structured function-calling format right now."
                        ),
                    })
                    continue
                if bad_reason == "unverified_claim":
                    messages.append({
                        "role": "user",
                        "content": (
                            f"You stated a specific number for {', '.join(unverified)} without ever "
                            "successfully calling the matching tool this turn (e.g. get_leads for "
                            "leads, get_clients for clients, get_companies for companies). You have "
                            "NO way to know real MyCase data without calling it — call the correct "
                            "tool now, then answer based on its ACTUAL result. Do not restate a "
                            "number you have not actually retrieved."
                        ),
                    })
                    continue
            else:
                final_text = _augment_reply_with_missing_data(candidate, items_by_resource, resource_totals, count_only)
            break

        for call in tool_calls:
            fn = call.get("function", {}) or {}
            raw_name = fn.get("name", "")
            name, inline_args = _recover_tool_name(raw_name, valid_names)
            if inline_args and not fn.get("arguments"):
                fn = {**fn, "arguments": inline_args}
            props = tool_schemas.get(name, {})
            args = _sanitize_tool_args(_coerce_args(fn.get("arguments")), props)

            logger.info("mycase_agent_tool_call", tool=name, args=args)
            if name not in valid_names:
                result: dict[str, Any] = {"success": False, "error": f"Unknown tool '{name}'"}
            else:
                try:
                    result = await mycase_client.call_tool(name, args)
                except Exception as exc:  # noqa: BLE001
                    logger.error("mycase_agent_tool_failed", tool=name, error=str(exc))
                    result = {"success": False, "error": str(exc)}

            if name in _DOWNLOAD_TOOLS and isinstance(result, dict) and result.get("success") is not False:
                url = result.get("download_url")
                if isinstance(url, str) and url:
                    download_urls.append(url)

            if name not in _REFERENCE_TOOLS and isinstance(result, dict) and isinstance(result.get("items"), list) and result["items"]:
                resource = name[4:] if name.startswith("get_") else name
                # get_invoices_by_date -> "invoices", so a claim like "Found 4
                # invoices" is checked against the SAME bucket get_invoices itself
                # would have populated — otherwise _unverified_resource_claims can't
                # find "invoices" and wrongly rejects an accurate reply as unverified.
                resource = _RESOURCE_KEY_ALIASES.get(resource, resource)
                bucket = items_by_resource.setdefault(resource, {})
                for it in result["items"]:
                    if isinstance(it, dict):
                        iid = _row_id(it)
                        bucket[iid if iid is not None else id(it)] = it
                item_count = result.get("item_count")
                if isinstance(item_count, int) and item_count > 0:
                    resource_totals[resource] = max(resource_totals.get(resource, 0), item_count)

            steps.append({"tool": name, "args": args, "result": result})

            content = json.dumps(result, default=str)
            if len(content) > _MAX_TOOL_OUTPUT_CHARS:
                # A dumb string cut past _MAX_TOOL_OUTPUT_CHARS can slice straight
                # through — or entirely past — a large items[] array, silently
                # dropping item_count/next_page_token from what the model ever sees.
                # That's exactly what caused wildly wrong "how many X" answers and
                # pagination that quietly stopped after page 1 — the model had no
                # real total to read and no way to know more pages existed, so it
                # fabricated a number instead. Spell the real values out in plain
                # English BEFORE the truncated blob, independent of JSON key order,
                # so they always survive regardless of which tool produced them.
                meta_bits = []
                if isinstance(result, dict):
                    if isinstance(result.get("item_count"), int):
                        meta_bits.append(f"item_count (TRUE TOTAL across ALL pages) = {result['item_count']}")
                    if result.get("next_page_token"):
                        meta_bits.append(
                            f"next_page_token = {result['next_page_token']!r} "
                            "(MORE PAGES REMAIN — call again with this page_token to continue)"
                        )
                    elif "next_page_token" in result:
                        meta_bits.append("next_page_token = null (this was the LAST page)")
                meta_line = ("[" + "; ".join(meta_bits) + "]\n") if meta_bits else ""
                content = (
                    meta_line
                    + f"[Tool result truncated — showing first {_MAX_TOOL_OUTPUT_CHARS} of "
                    f"{len(content)} chars. Use the item_count/next_page_token values above (not "
                    "anything guessed from the truncated rows below) — summarise what you have; do "
                    "not emit closing brackets.]\n"
                    + content[:_MAX_TOOL_OUTPUT_CHARS]
                )
            messages.append({"role": "tool", "content": content, "tool_name": name})

    if not final_text:
        if steps:
            messages.append({
                "role": "user",
                "content": (
                    "State in plain text, in one short line, what you found (e.g. a count) and that "
                    "the full table + Download CSV button are shown below. Do not retype the records "
                    "as a table or CSV, and do not add analysis unless already explicitly requested. "
                    "No JSON, no code blocks."
                ),
            })
        else:
            messages.append({
                "role": "user",
                "content": "You have not called any MyCase tool yet. You MUST call the appropriate tool now. Do NOT reply with text — make the tool call.",
            })
        closing = await model_gateway.chat(messages, model=model, num_predict=_NUM_PREDICT_FINAL)
        candidate = (closing or {}).get("content", "") or ""
        still_unverified = _unverified_resource_claims(candidate, items_by_resource, resource_totals)
        if still_unverified:
            logger.warning("mycase_agent_closing_reply_unverified", unverified=still_unverified)
        if not _is_garbage_reply(candidate) and not still_unverified:
            final_text = _augment_reply_with_missing_data(candidate, items_by_resource, resource_totals, count_only)
        else:
            ok_tools = [s["tool"] for s in steps if (s.get("result") or {}).get("success") is not False]
            err_tools = [s["tool"] for s in steps if (s.get("result") or {}).get("success") is False]
            parts: list[str] = []
            if ok_tools:
                parts.append(f"Completed: {', '.join(ok_tools)}.")
            if err_tools:
                parts.append(f"Failed: {', '.join(err_tools)}.")
            final_text = " ".join(parts) or "Done."

    final_text = _strip_download_link_urls(final_text, download_urls)
    return {"success": True, "reply": final_text, "steps": steps, "model": model}
