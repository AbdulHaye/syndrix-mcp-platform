"""Custom MCP server: MyCase read-only (GET) tools.

A genuine, standalone MCP server — mirrors the podio-files server's pattern
(app/mcp_servers/podio_files.py): built with FastMCP, called in-process by the agent
via app/services/mycase_client.py, and runnable standalone via
``python -m app.mcp_servers.mycase`` (stdio) for any other MCP client.

Scope: GET/read operations only (46 endpoints, matching every 'get'/'download' row in
the MyCase API's sidebar). Write operations (create/update/delete) are not wired up
yet — see CLAUDE.md session log.
"""
from __future__ import annotations

from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

logger = structlog.get_logger(__name__)

mycase_mcp = FastMCP("mycase")


async def _call(method_name: str, **kwargs: Any) -> dict[str, Any]:
    """Dispatch to a MyCaseREST method by name, normalising the result/error shape."""
    from app.services.mycase_rest import mycase_rest

    try:
        method = getattr(mycase_rest, method_name)
        result = await method(**kwargs)
        if isinstance(result, dict):
            return {"success": True, **result}
        return {"success": True, "data": result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("mycase_tool_failed", tool=method_name, error=str(exc))
        return {"success": False, "error": str(exc)}


# ── Calls ────────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_calls", description="Get all firm calls (call log) viewable by the authorized user. Supports filter[updated_after] for incremental sync and cursor pagination.")
async def get_calls(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_calls", updated_after=updated_after, page_size=page_size, page_token=page_token)


# ── Case Roles ───────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_case_roles", description="Get all firm case roles (e.g. 'Plaintiff', 'Defendant') viewable by the authorized user.")
async def get_case_roles(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_case_roles", updated_after=updated_after, page_size=page_size, page_token=page_token)


# ── Cases ────────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_cases", description="Get all firm cases/matters viewable by the authorized user. filter status can be 'open' or 'closed'. field_client is a comma-separated list to expand each case's nested client object beyond just id (valid: id,first_name,last_name,middle_name,email,cell_phone_number,work_phone_number,home_phone_number,fax_phone_number,contact_group,birthdate,created_at,updated_at). field_custom_field ONLY supports 'id,field_type' — there is NO 'name' or 'value' sub-field on it (passing either 400s). Each case's custom_field_values[] ALREADY includes 'value' by default with no expansion needed; to know WHICH custom field a value belongs to, match custom_field_values[].custom_field.id against the id from get_custom_fields() — do not try to expand the name via field_custom_field.")
async def get_cases(
    status: str | None = None, updated_after: str | None = None,
    field_client: str | None = None, field_custom_field: str | None = None,
    page_size: int | None = None, page_token: str | None = None,
) -> dict:
    return await _call(
        "get_cases", status=status, updated_after=updated_after,
        field_client=field_client, field_custom_field=field_custom_field,
        page_size=page_size, page_token=page_token,
    )


@mycase_mcp.tool(name="get_client_cases", description="Get all cases associated with a specific client (person). Requires the client's id.")
async def get_client_cases(client_id: int, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_client_cases", client_id=client_id, page_size=page_size, page_token=page_token)


@mycase_mcp.tool(name="get_case", description="Get one specific case/matter by its id — full detail incl. clients, companies, staff, billing info, custom fields, and office. field_client is a comma-separated list to expand each client beyond just id in ONE call (valid: id,first_name,last_name,middle_name,email,cell_phone_number,work_phone_number,home_phone_number,fax_phone_number,contact_group,birthdate,created_at,updated_at) — e.g. field_client='id,first_name,last_name,email' when asked for a case's client's name/email. field_custom_field ONLY supports 'id,field_type' (no 'name'/'value' — see get_cases' description for why).")
async def get_case(case_id: int, field_client: str | None = None, field_custom_field: str | None = None) -> dict:
    return await _call("get_case", case_id=case_id, field_client=field_client, field_custom_field=field_custom_field)


@mycase_mcp.tool(name="get_case_folder", description="Get the id of a case's ROOT document folder ONLY — for anything more (subfolders, the full structure), use get_case_folder_tree instead of manually chaining get_folder_documents/get_folder_subfolders from this. Do NOT use this (or folder-walking generally) just to answer 'show me the documents for case X' — get_case_documents already does that completely, in one call, and is simpler/more reliable.")
async def get_case_folder(case_id: int) -> dict:
    return await _call("get_case_folder", case_id=case_id)


@mycase_mcp.tool(
    name="get_case_folder_tree",
    description=(
        "Get a case's COMPLETE folder structure — the root folder and every subfolder "
        "(recursively, up to max_depth), each with its own documents — in ONE call. Use this for "
        "'get the folder structure for case X', 'show me the folders for case X', or anything "
        "asking about how a case's documents are organized. Do NOT try to build this yourself by "
        "chaining get_case_folder -> get_folder_subfolders -> get_folder_documents calls one at a "
        "time — for anything beyond a single trivial folder that's unreliable (a branch gets "
        "missed, or you never finish). Returns items[] — one row per folder, with folder_name, "
        "path (a readable breadcrumb like 'Root/Discovery/2024'), depth, document_count, and the "
        "document names directly inside it. NOTE: this is about ORGANIZATION/structure — if the "
        "user just wants the list of documents for a case, use get_case_documents instead, it's "
        "simpler and already complete."
    ),
)
async def get_case_folder_tree(case_id: int, max_depth: int = 6) -> dict:
    return await _call("get_case_folder_tree", case_id=case_id, max_depth=max_depth)


@mycase_mcp.tool(
    name="get_case_documents",
    description=(
        "THE tool for 'show/list/find all documents for case X' — per MyCase's own docs this "
        "returns EVERY document associated with the case, complete, in one call, regardless of "
        "which folder or subfolder it's actually filed in. This is simpler and more reliable "
        "than manually walking get_case_folder -> get_folder_subfolders -> get_folder_documents "
        "— do NOT do the folder-walk just to list a case's documents; only walk folders when the "
        "user specifically asks about folder/subfolder STRUCTURE, not for the document list "
        "itself. If this returns zero items, the case genuinely has no documents — do not then "
        "additionally check folders 'just in case', that would be redundant."
    ),
)
async def get_case_documents(case_id: int) -> dict:
    return await _call("get_case_documents", case_id=case_id)


@mycase_mcp.tool(name="get_case_notes", description="Get all notes attached to a specific case.")
async def get_case_notes(case_id: int) -> dict:
    return await _call("get_case_notes", case_id=case_id)


# ── Case Stages ──────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_case_stages", description="Get all firm case stages (the pipeline stages a case can be in, as configured in MyCase) viewable by the authorized user.")
async def get_case_stages(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_case_stages", updated_after=updated_after, page_size=page_size, page_token=page_token)


# ── Clients (People) ─────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_clients", description="Get all firm clients (people, not companies) viewable by the authorized user. Filter by first_name, last_name, email, or any phone number field to search.")
async def get_clients(
    first_name: str | None = None, last_name: str | None = None, email: str | None = None,
    cell_phone_number: str | None = None, home_phone_number: str | None = None,
    work_phone_number: str | None = None, updated_after: str | None = None,
    page_size: int | None = None, page_token: str | None = None,
) -> dict:
    return await _call(
        "get_clients", first_name=first_name, last_name=last_name, email=email,
        cell_phone_number=cell_phone_number, home_phone_number=home_phone_number,
        work_phone_number=work_phone_number, updated_after=updated_after,
        page_size=page_size, page_token=page_token,
    )


@mycase_mcp.tool(name="get_client", description="Get one specific client (person) by their id — full detail incl. address, phones, notes, people_group, and their cases.")
async def get_client(client_id: int) -> dict:
    return await _call("get_client", client_id=client_id)


@mycase_mcp.tool(name="get_client_notes", description="Get all notes attached to a specific client (person).")
async def get_client_notes(client_id: int) -> dict:
    return await _call("get_client_notes", client_id=client_id)


@mycase_mcp.tool(name="get_client_message_threads", description="Get all message threads (client communication) associated with a specific client.")
async def get_client_message_threads(client_id: int) -> dict:
    return await _call("get_client_message_threads", client_id=client_id)


@mycase_mcp.tool(
    name="aggregate_clients",
    description=(
        "Build a filtered, grouped, COUNTED contact (client) report — the client equivalent of "
        "aggregate_cases. Use this for 'contacts created this month/this week/on X' or any "
        "request that filters/groups/counts clients — do NOT call get_clients and eyeball-"
        "filter/count the results yourself.\n\n"
        "created_after/created_before (YYYY-MM-DD, inclusive): filter on the client's own "
        "created_at — MyCase has no server-side filter for this.\n"
        "group_by: any field name present on a client record (e.g. 'status'). "
        "min_group_size/max_group_size filter on how many CLIENTS fall in each group (e.g. "
        "group_by='email', min_group_size=2 for shared email addresses).\n"
        "DO NOT use this for 'clients who have more than one CASE' — a client record carries no "
        "case count, so there is nothing here to filter on. That is a cases question: call "
        "aggregate_cases(group_by='client_name', min_group_size=2). Grouping clients by 'id' is "
        "always wrong (unique per client, so every group has exactly one row) and is rejected.\n\n"
        "field_filters: {client field name: value}, with the same three conventions as "
        "custom_field_filters — \"\" = the field is BLANK, \"*\" = the field HAS any value, "
        "anything else = substring match. 'Clients without an email address' is "
        "field_filters={'email': ''}; 'clients that have a phone number' is "
        "field_filters={'cell_phone_number': '*'}.\n\n"
        "Returns total_clients, a `groups` array ([{name, count}], sorted highest-count-first) "
        "when group_by is given, and the flat `items` array."
    ),
)
async def aggregate_clients(
    created_after: str | None = None,
    created_before: str | None = None,
    field_filters: dict | None = None,
    group_by: str | None = None,
    min_group_size: int | None = None,
    max_group_size: int | None = None,
    limit: int | None = None,
) -> dict:
    return await _call(
        "aggregate_clients", created_after=created_after, created_before=created_before,
        field_filters=field_filters, group_by=group_by,
        min_group_size=min_group_size, max_group_size=max_group_size, limit=limit,
    )


@mycase_mcp.tool(
    name="find_duplicate_clients",
    description=(
        "Firm-wide duplicate-CONTACT scan by email AND by phone number — use this for 'find "
        "duplicate contacts', 'clients with the same email/phone'. MyCase has no server-side "
        "'find duplicates' endpoint, so this walks every client once and groups by normalized "
        "email / normalized phone in Python, returning ONLY groups with more than one client — "
        "do NOT try to spot duplicates yourself from a raw get_clients list, that's unreliable "
        "at any real firm size.\n\n"
        "Returns duplicate_email_groups/duplicate_phone_groups (counts) plus by_email/by_phone "
        "arrays, each entry listing the matching clients (id, name, email, phone numbers)."
    ),
)
async def find_duplicate_clients() -> dict:
    return await _call("find_duplicate_clients")


# ── Companies ────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_companies", description="Get all firm companies (organization contacts) viewable by the authorized user. Filter by name, email, or phone number to search.")
async def get_companies(
    name: str | None = None, email: str | None = None, main_phone_number: str | None = None,
    fax_phone_number: str | None = None, updated_after: str | None = None,
    page_size: int | None = None, page_token: str | None = None,
) -> dict:
    return await _call(
        "get_companies", name=name, email=email, main_phone_number=main_phone_number,
        fax_phone_number=fax_phone_number, updated_after=updated_after,
        page_size=page_size, page_token=page_token,
    )


@mycase_mcp.tool(name="get_company", description="Get one specific company by its id — full detail incl. address, notes, associated cases and clients.")
async def get_company(company_id: int) -> dict:
    return await _call("get_company", company_id=company_id)


# ── Custom Fields ────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_custom_fields", description="Get all firm custom field DEFINITIONS (not values) viewable by the authorized user — name, parent_type (case/client/company/expense/time/time_and_expense), field_type (short_text/long_text/numeric/boolean/date/list/currency), and list_options for list-type fields.")
async def get_custom_fields(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_custom_fields", updated_after=updated_after, page_size=page_size, page_token=page_token)


@mycase_mcp.tool(name="get_custom_field", description="Get one specific custom field definition by its id.")
async def get_custom_field(custom_field_id: int) -> dict:
    return await _call("get_custom_field", custom_field_id=custom_field_id)


@mycase_mcp.tool(name="get_custom_field_list_options", description="Get the selectable list options (key/option pairs) for a custom field whose field_type is 'list'.")
async def get_custom_field_list_options(custom_field_id: int) -> dict:
    return await _call("get_custom_field_list_options", custom_field_id=custom_field_id)


# ── Documents ────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_documents", description="Get all firm documents (firm-wide, not scoped to one case) viewable by the authorized user.")
async def get_documents(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_documents", updated_after=updated_after, page_size=page_size, page_token=page_token)


@mycase_mcp.tool(name="get_document", description="Get one specific document's metadata by its id (name, filename, path, folder, associated case).")
async def get_document(document_id: int) -> dict:
    return await _call("get_document", document_id=document_id)


@mycase_mcp.tool(
    name="find_cases_with_documents",
    description=(
        "Find cases that actually HAVE documents attached — use this for 'give me N cases that "
        "have documents/files' or similar. MyCase has NO server-side filter for this; do NOT try "
        "to answer it by sampling a few cases from get_cases and checking if they happen to have "
        "documents — that gives wrong/incomplete answers. This tool scans every document "
        "firm-wide, tallies which case each belongs to, and returns the `limit` cases with the "
        "MOST documents attached — each returned case includes a document_count field. Also "
        "returns documents_scanned/distinct_cases_with_documents for context."
    ),
)
async def find_cases_with_documents(limit: int = 5) -> dict:
    return await _call("find_cases_with_documents", limit=limit)


@mycase_mcp.tool(name="get_document_versions_all", description="Get ALL document versions firm-wide (every version of every document). For one document's versions use get_document_versions instead.")
async def get_document_versions_all(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_document_versions_all", updated_after=updated_after, page_size=page_size, page_token=page_token)


@mycase_mcp.tool(name="get_document_versions", description="Get all versions of ONE specific document, given its id.")
async def get_document_versions(document_id: int) -> dict:
    return await _call("get_document_versions", document_id=document_id)


@mycase_mcp.tool(name="download_document", description="Get a temporary download URL (valid ~1 minute) for a document's current version, given its id. Returns the URL — never embed document bytes in a reply.")
async def download_document(document_id: int) -> dict:
    return await _call("download_document", document_id=document_id)


@mycase_mcp.tool(name="download_document_version", description="Get a temporary download URL (valid ~1 hour) for one specific version of a document, given the document id and version_number.")
async def download_document_version(document_id: int, version_number: int) -> dict:
    return await _call("download_document_version", document_id=document_id, version_number=version_number)


# ── Events (calendar) ────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_events", description="Get all firm calendar events viewable by the authorized user — includes start/end time, all_day, private, event_type, location, associated case, and attending staff.")
async def get_events(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_events", updated_after=updated_after, page_size=page_size, page_token=page_token)


# ── Expenses ─────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_expenses", description="Get all firm billing expenses viewable by the authorized user — activity_name, cost, units, billable flag, associated case/staff/invoices.")
async def get_expenses(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_expenses", updated_after=updated_after, page_size=page_size, page_token=page_token)


@mycase_mcp.tool(name="get_expense", description="Get one specific expense entry by its id.")
async def get_expense(expense_id: int) -> dict:
    return await _call("get_expense", expense_id=expense_id)


# ── Firm / Me ────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_firm", description="Get the firm (law office) of the current authorized user — firm name and MyCase URL.")
async def get_firm() -> dict:
    return await _call("get_firm")


@mycase_mcp.tool(name="get_me", description="Get the current authorized user's own staff profile (id, name, email, title, default hourly rate). Use this to answer 'who am I' / resolve the current user's own id.")
async def get_me() -> dict:
    return await _call("get_me")


# ── Folders ──────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_folder_documents", description="Get the first level of documents directly inside a folder, given the folder's id.")
async def get_folder_documents(folder_id: int, updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_folder_documents", folder_id=folder_id, updated_after=updated_after, page_size=page_size, page_token=page_token)


@mycase_mcp.tool(name="get_folder_subfolders", description="Get the first level of subfolders directly inside a folder, given the folder's id.")
async def get_folder_subfolders(folder_id: int, updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_folder_subfolders", folder_id=folder_id, updated_after=updated_after, page_size=page_size, page_token=page_token)


# ── Invoices ─────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_invoices", description="Get firm invoices viewable by the authorized user. By default ONLY invoices with online payments enabled are returned — pass only_allowed_online_payments=false to see ALL invoices, otherwise some may be silently missing.")
async def get_invoices(updated_after: str | None = None, only_allowed_online_payments: bool | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call(
        "get_invoices", updated_after=updated_after,
        only_allowed_online_payments=only_allowed_online_payments,
        page_size=page_size, page_token=page_token,
    )


@mycase_mcp.tool(name="get_invoices_by_date", description="Find invoices by an EXACT date or date range on created_at, updated_at, invoice_date, or due_date. Use this for ANY 'invoices created/due/dated on|before|after X' request — get_invoices only supports updated_after (a floor on created-OR-updated time), which is NOT the same as an exact creation date and has no relation to invoice_date/due_date at all; using get_invoices alone for a date-specific question returns the wrong set. on = exact day (YYYY-MM-DD); after/before = inclusive range bounds (combine for a range). Returns only the matching invoices, already filtered — do not filter get_invoices' raw output yourself. Defaults to ALL matching invoices regardless of online-payment status (only_allowed_online_payments=False unless you pass True yourself) — unlike raw get_invoices, this does not silently drop invoices with online payments disabled.")
async def get_invoices_by_date(
    date_field: str = "created_at", on: str | None = None, after: str | None = None, before: str | None = None,
    only_allowed_online_payments: bool | None = None, max_invoices: int | None = None,
) -> dict:
    kwargs = {"date_field": date_field, "on": on, "after": after, "before": before, "only_allowed_online_payments": only_allowed_online_payments}
    if max_invoices is not None:
        kwargs["max_invoices"] = max_invoices
    return await _call("get_invoices_by_date", **kwargs)


@mycase_mcp.tool(
    name="aggregate_invoices",
    description=(
        "Filter/sort/limit invoices FIRM-WIDE — the invoice equivalent of aggregate_cases, for "
        "the same reason: get_invoices has NO server-side filter for status or balance-due at "
        "all (only updated_after), so 'top N unpaid invoices' / 'overdue invoices' / 'invoices "
        "over $X owed' has no targeted endpoint. Use this for ANY such request — NEVER call "
        "get_invoices and try to eyeball-filter/sort/limit its raw output yourself, and NEVER "
        "just page_size=N a raw get_invoices call expecting a filter to have been applied — it "
        "has none, so that returns the first N invoices UNFILTERED regardless of what was asked.\n\n"
        "status: exact match (case-insensitive) against a real MyCase invoice status — overdue, "
        "paid, partial, draft, unsent, sent, forwarded.\n"
        "paid: True = fully paid (balance_due<=0); False = NOT fully paid — this is what "
        "'unpaid'/'invoices that haven't been paid' means (covers overdue/partial/draft/unsent/"
        "sent/forwarded in one filter, not just status='overdue'). Prefer `paid=False` over "
        "guessing a status for a plain 'unpaid' request.\n"
        "min_balance_due: only invoices owing at least this much.\n"
        "invoice_date_after/invoice_date_before, due_date_after/due_date_before (YYYY-MM-DD, "
        "inclusive): combine a status/paid filter with a date range in the SAME call.\n"
        "sort_by: 'balance_due' (default, DESCENDING — largest amount owed first, the usual "
        "meaning of 'top N unpaid invoices'), 'due_date' (ASCENDING — most overdue/soonest-due "
        "first), or 'invoice_date' (DESCENDING — most recent first).\n"
        "limit: caps items to the first N sorted/filtered rows for a 'top N' request — "
        "total_invoices still reports the TRUE full-match count regardless of limit.\n\n"
        "group_by (optional): 'client_name'/'client', 'assigned_attorney'/'lead_attorney', or "
        "'status' — use this for 'unpaid invoices grouped by client' or 'invoices by assigned "
        "attorney'. An invoice itself has no client/attorney field, only a case link — this "
        "resolves both via the linked case (same join aggregate_cases uses), NOT a guess. Adds "
        "a top-level `groups` array ([{name, count, total_balance_due}], sorted by "
        "total_balance_due descending — read this directly for the breakdown) and a "
        "`group_name` column on every row.\n\n"
        "Each row includes a computed balance_due column (total_amount - paid_amount, already "
        "numeric even though this account sometimes returns those as strings) — present it, "
        "don't recompute it."
    ),
)
async def aggregate_invoices(
    status: str | None = None,
    paid: bool | None = None,
    min_balance_due: float | None = None,
    invoice_date_after: str | None = None,
    invoice_date_before: str | None = None,
    due_date_after: str | None = None,
    due_date_before: str | None = None,
    group_by: str | None = None,
    min_group_size: int | None = None,
    max_group_size: int | None = None,
    sort_by: str = "balance_due",
    limit: int | None = None,
    only_allowed_online_payments: bool | None = None,
) -> dict:
    return await _call(
        "aggregate_invoices", status=status, paid=paid, min_balance_due=min_balance_due,
        invoice_date_after=invoice_date_after, invoice_date_before=invoice_date_before,
        due_date_after=due_date_after, due_date_before=due_date_before, group_by=group_by,
        min_group_size=min_group_size, max_group_size=max_group_size,
        sort_by=sort_by, limit=limit, only_allowed_online_payments=only_allowed_online_payments,
    )


@mycase_mcp.tool(name="get_invoice_payments", description="Get all firm invoice payments viewable by the authorized user. Filter by payable_id (the invoice's id) or status (e.g. 'success', 'pending', 'failure').")
async def get_invoice_payments(payable_id: str | None = None, status: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_invoice_payments", payable_id=payable_id, status=status, page_size=page_size, page_token=page_token)


@mycase_mcp.tool(
    name="aggregate_payments",
    description=(
        "Build a filtered, grouped, COUNTED/SUMMED payment report — use this for 'all payments "
        "received', 'payments by attorney/client', or any request about invoice PAYMENTS "
        "(actual money received) rather than invoices themselves. get_invoice_payments with no "
        "filters already returns every firm payment (confirmed live: 9,314 records in this "
        "account) but that raw list is too large to eyeball-count/sum yourself — this tool walks "
        "every page and computes the totals in code.\n\n"
        "status: exact match on MyCase's own payment status ('success', 'pending', 'failure').\n"
        "case_id: pass this for 'payment history for case X' — payments already carry "
        "`case: {id}` directly, no join needed (get_case_payments is a shortcut for exactly this).\n"
        "date_after/date_before (YYYY-MM-DD, inclusive): filter on the payment's own date.\n"
        "group_by: 'attorney', 'client', 'case', or 'status' (default) — e.g. group_by='attorney' "
        "for 'payments by assigned attorney' (each payment already carries its own attorney "
        "directly, the most direct source for this). aggregate_invoices(group_by='assigned_"
        "attorney') also answers an attorney breakdown, resolved via each invoice's linked case — "
        "use whichever matches what the user actually asked about (payments received vs. "
        "invoice/balance amounts); either way, never guess an attorney/client link yourself.\n\n"
        "Returns total_payments, total_amount (sum of amount across every surviving payment), "
        "and a `groups` array ([{name, count, total_amount}], sorted by total_amount "
        "descending — read this directly for a breakdown, don't re-sum items yourself), plus "
        "the flat `items` array."
    ),
)
async def aggregate_payments(
    status: str | None = None,
    case_id: int | None = None,
    date_after: str | None = None,
    date_before: str | None = None,
    group_by: str | None = None,
    min_group_size: int | None = None,
    max_group_size: int | None = None,
    limit: int | None = None,
) -> dict:
    return await _call(
        "aggregate_payments", status=status, case_id=case_id, date_after=date_after,
        date_before=date_before, group_by=group_by,
        min_group_size=min_group_size, max_group_size=max_group_size, limit=limit,
    )


@mycase_mcp.tool(
    name="get_case_payments",
    description=(
        "Payment history for ONE case, by its id — use this for 'payment history for case X'/"
        "'payments received on case X'. Do NOT try to answer this via get_case_invoices or by "
        "fabricating a tool call — this is the real, exact tool for it."
    ),
)
async def get_case_payments(case_id: int) -> dict:
    return await _call("get_case_payments", case_id=case_id)


# ── Leads ────────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_leads", description="Get all firm intake leads viewable by the authorized user. Filter by first_name, last_name, email, or any phone number field to search.")
async def get_leads(
    first_name: str | None = None, last_name: str | None = None, email: str | None = None,
    cell_phone_number: str | None = None, home_phone_number: str | None = None,
    work_phone_number: str | None = None, updated_after: str | None = None,
    page_size: int | None = None, page_token: str | None = None,
) -> dict:
    return await _call(
        "get_leads", first_name=first_name, last_name=last_name, email=email,
        cell_phone_number=cell_phone_number, home_phone_number=home_phone_number,
        work_phone_number=work_phone_number, updated_after=updated_after,
        page_size=page_size, page_token=page_token,
    )


@mycase_mcp.tool(name="get_lead", description="Get one specific lead by its id — full detail incl. status, approved flag, referral source, and referred_by.")
async def get_lead(lead_id: int) -> dict:
    return await _call("get_lead", lead_id=lead_id)


@mycase_mcp.tool(
    name="aggregate_leads",
    description=(
        "Build a filtered, grouped, COUNTED lead (prospect) report — the lead equivalent of "
        "aggregate_cases. Use this for ANY request that filters/groups/counts leads — do NOT "
        "call get_leads and eyeball-filter/count the results yourself, this tool does it exactly "
        "in code and walks every matching page internally.\n\n"
        "status: EXACT match, case-insensitive — a lead's status is a literal firm-defined "
        "string (confirmed real values in this account: 'NEED FOLLOW-UP', 'New Lead', 'Need "
        "consultation', 'UNDECIDED', 'NOT FOUND YET'). Use status='NEED FOLLOW-UP' for "
        "'prospects/leads that need follow-up'. Do NOT substring-match — 'NEED FOLLOW-UP' and "
        "'New Lead' share no useful substring.\n"
        "created_after/created_before (YYYY-MM-DD, inclusive): filter on the lead's own "
        "created_at — MyCase has no server-side filter for this.\n"
        "assigned_attorney: a lead has no attorney of its own — once converted it links to a "
        "case (`case: {id}`), and that case may have a lead_lawyer-flagged staff member, same "
        "convention as aggregate_cases. Pass assigned_attorney='' for 'prospects with NO "
        "assigned attorney' (covers leads with no linked case at all, or a linked case with no "
        "lead_lawyer set), or a substring to match one attorney's name.\n"
        "group_by: 'status' (default) or 'assigned_attorney'/'lead_attorney'.\n\n"
        "Returns the same shape as aggregate_cases: total_leads, total_groups, group_by_field, "
        "a `groups` array ([{name, count}], sorted highest-count-first — read this directly for "
        "any breakdown/'which status has the most' question), and a flat `items` array (each "
        "lead's full fields plus resolved assigned_attorney, group_name, lead_count). "
        "limit caps how many rows come back in items without affecting the true totals/groups."
    ),
)
async def aggregate_leads(
    status: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    assigned_attorney: str | None = None,
    group_by: str | None = None,
    min_group_size: int | None = None,
    max_group_size: int | None = None,
    limit: int | None = None,
) -> dict:
    return await _call(
        "aggregate_leads", status=status, created_after=created_after, created_before=created_before,
        assigned_attorney=assigned_attorney, group_by=group_by,
        min_group_size=min_group_size, max_group_size=max_group_size, limit=limit,
    )


# ── Locations ────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_locations", description="Get all firm locations (offices/courts) viewable by the authorized user.")
async def get_locations(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_locations", updated_after=updated_after, page_size=page_size, page_token=page_token)


# ── Notes ────────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_note", description="Get one specific note by its id, regardless of whether it's attached to a case, client, or company.")
async def get_note(note_id: int) -> dict:
    return await _call("get_note", note_id=note_id)


# ── People Groups ────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_people_groups", description="Get all firm people groups (client tagging/grouping categories) viewable by the authorized user.")
async def get_people_groups(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_people_groups", updated_after=updated_after, page_size=page_size, page_token=page_token)


# ── Practice Areas ───────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_practice_areas", description="Get all firm practice areas (case categorization, e.g. 'Family Law', 'Personal Injury') viewable by the authorized user.")
async def get_practice_areas(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_practice_areas", updated_after=updated_after, page_size=page_size, page_token=page_token)


# ── Referral Sources ─────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_referral_sources", description="Get all firm referral sources (how leads/clients found the firm) viewable by the authorized user.")
async def get_referral_sources(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_referral_sources", updated_after=updated_after, page_size=page_size, page_token=page_token)


# ── Staff ────────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_staff", description="Get all firm staff members viewable by the authorized user. filter status can be 'active' or 'inactive'.")
async def get_staff(status: str | None = None, updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_staff", status=status, updated_after=updated_after, page_size=page_size, page_token=page_token)


@mycase_mcp.tool(name="get_individual_staff", description="Get one specific staff member by their id.")
async def get_individual_staff(staff_id: int) -> dict:
    return await _call("get_individual_staff", staff_id=staff_id)


# ── Tasks ────────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_tasks", description="Get all firm tasks viewable by the authorized user — name, priority, due_date, completed flag, associated case and staff.")
async def get_tasks(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_tasks", updated_after=updated_after, page_size=page_size, page_token=page_token)


# ── Time Entries ─────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_time_entries", description="Get all firm billable time entries viewable by the authorized user — activity_name, hours, rate, billable flag, UTBMS codes, associated case/staff/invoices.")
async def get_time_entries(updated_after: str | None = None, page_size: int | None = None, page_token: str | None = None) -> dict:
    return await _call("get_time_entries", updated_after=updated_after, page_size=page_size, page_token=page_token)


@mycase_mcp.tool(name="get_time_entry", description="Get one specific time entry by its id.")
async def get_time_entry(time_entry_id: int) -> dict:
    return await _call("get_time_entry", time_entry_id=time_entry_id)


# ── Webhooks ─────────────────────────────────────────────────────────────────────

@mycase_mcp.tool(name="get_webhook_subscriptions", description="Get all current webhook subscriptions for this firm, including each subscription's model, url, subscribed actions, and hmac_key (used to verify webhook payload signatures).")
async def get_webhook_subscriptions() -> dict:
    return await _call("get_webhook_subscriptions")


@mycase_mcp.tool(
    name="describe_entity_fields",
    description=(
        "List the fields available on a MyCase entity — including THIS FIRM'S OWN custom "
        "fields, read live — plus what each notable field actually means. Pass an entity "
        "('cases', 'leads', 'invoices', 'payments', 'clients', 'staff') or omit it for all.\n\n"
        "CALL THIS FIRST whenever the user names a field, date or attribute you are not "
        "certain maps to a real field — e.g. 'SOL date', 'entry date', 'processing agent', "
        "'case type', 'jurisdiction'. Guessing a field name, or assuming a field doesn't "
        "exist, is how questions get answered wrong: 'cases with a missing SOL date' failed "
        "purely because nothing told the agent that `sol_date` is a real native case field.\n\n"
        "Returns per entity: notable_fields (name -> meaning, including COMPUTED fields like "
        "assigned_attorney and client_name that these tools calculate but MyCase does not "
        "return) and custom_fields (exact name + type — pass them verbatim as a "
        "custom_field_filters key or group_by value). Never hardcode or guess a custom field "
        "name; they differ per firm."
    ),
)
async def describe_entity_fields(entity: str | None = None) -> dict:
    return await _call("describe_entity_fields", entity=entity)


# ── Case reporting (deterministic, computed server-side — not LLM-computed) ──────

@mycase_mcp.tool(
    name="aggregate_cases",
    description=(
        "Build a filtered, grouped, COUNTED case report — use this for ANY request that "
        "asks to filter/exclude cases and then count or group them (e.g. 'case count per "
        "assigned agent', 'how many active X cases', 'break down cases by stage'). "
        "Do NOT try to compute counts yourself by calling get_cases and reading through the "
        "results — with more than a handful of cases that is unreliable (large result sets "
        "get truncated before you see them all) and this tool does the counting exactly, "
        "in code, no matter how many cases match. It walks EVERY matching page internally.\n\n"
        "practice_area: substring-matches the case's built-in Practice Area field.\n"
        "custom_field_filters VALUE CONVENTIONS — all three, do not confuse them:\n"
        "  \"\"  (empty string) = the field is BLANK. Use for 'cases with NO assigned attorney'.\n"
        "  \"*\" (asterisk)     = the field HAS ANY value. Use for 'cases WITH an assigned "
        "attorney', 'cases that have a Processing Agent'. Read the question's polarity "
        "carefully: 'with X' and 'without X' need opposite values here, and getting it backwards "
        "returns exactly the wrong set (confirmed live — 'Criminal cases WITH assigned "
        "attorneys' was answered with the 49 UNASSIGNED ones, when 188 of the 237 do have one).\n"
        "  anything else       = case-insensitive SUBSTRING match on the value.\n"
        "custom_field_filters: {custom field NAME: substring value}, e.g. "
        "{\"CASE TYPE\": \"Asylum\"} — resolve exact field names via get_custom_fields() first, "
        "don't guess. Matches ANY value containing the substring (so 'Asylum' matches both "
        "'Asylum - Affirmative' and 'Asylum - Defense').\n"
        "case_stages: list of EXACT case_stage strings to KEEP — use this whenever the user "
        "wants cases IN a specific stage (e.g. 'cases where Case Stage is CLOSED'). Matches "
        "EXACTLY (not a substring), so 'CLOSED' will NOT also pull in 'CLOSED WITH BALANCE'.\n"
        "exclude_case_stages: list of EXACT case_stage strings to drop — use this when the user "
        "wants everything EXCEPT some stages.\n"
        "Both case_stages and exclude_case_stages need the real stage names resolved via "
        "get_case_stages() first; a user's loose description ('Closed', 'Immigration Documents "
        "Submitted') must be mapped to the real stage strings before calling this — this tool "
        "does not fuzzy-match stages. Do NOT rely on group_by=case_stage alone to answer a "
        "'cases where stage is X' request — group_by only labels/counts every surviving row by "
        "its group, it does not filter which rows survive; pass case_stages to actually filter.\n"
        "group_by: either a builtin field (practice_area, case_stage, status, billing_type, "
        "sol_date), 'assigned_attorney'/'client_name' (computed — see below), OR a custom field "
        "NAME (e.g. 'PROCESSING AGENT') to group and count by. ALWAYS pass the exact dimension "
        "the user named — do NOT omit this and let it silently default to practice_area when "
        "the user asked about a DIFFERENT dimension. 'stage' means group_by='case_stage', NOT "
        "practice_area — these are different fields (confirmed live: omitting group_by for "
        "'which case stage has the most cases' silently defaulted to practice_area and gave a "
        "wrong answer). 'attorney'/'lawyer' means 'assigned_attorney'; 'client' means "
        "'client_name'. The returned `groups` array (see below) already has the per-group counts "
        "sorted highest-first — for 'which X has the most/fewest cases', read `groups[0]`/"
        "`groups[-1]` directly, do NOT scan the flat `items` list yourself to find the max.\n\n"
        "opened_after/opened_before, closed_after/closed_before, updated_after/updated_before "
        "(all YYYY-MM-DD, inclusive): filter on the case's opened_date/closed_date/updated_at. "
        "created_after/created_before filter on the case's own created_at — use these (NOT "
        "opened_date) for 'recently created cases'/'cases created this month'. sol_date_after/"
        "sol_date_before filter on `sol_date`, MyCase's native statute-of-limitations date field "
        "on a case (it is a REAL builtin field, not a custom field). For 'cases with a missing/"
        "no SOL date', pass custom_field_filters={\"sol_date\": \"\"} instead (sol_date is a "
        "valid custom_field_filters/group_by key exactly like any other builtin field). MyCase "
        "has NO server-side filter for opened_date/closed_date/created_at/sol_date at all — this "
        "computes them client-side, so they are always exact regardless of dataset size.\n\n"
        "min_group_size/max_group_size: filter on HOW MANY ROWS ARE IN EACH GROUP (a SQL "
        "HAVING clause). This is the ONLY correct way to answer 'clients who have more than one "
        "case' (group_by='client_name', min_group_size=2), 'attorneys with at least 10 open "
        "cases' (group_by='assigned_attorney', status='open', min_group_size=10) or 'practice "
        "areas with only one case' (max_group_size=1). Do NOT instead group everything and try "
        "to pick out the large groups yourself — confirmed live that this returns every matching "
        "case (2,380 rows) and answers the question wrong. Groups outside the range are dropped "
        "entirely from items/groups/total_cases; `groups_before_size_filter` tells you how many "
        "groups there were beforehand.\n"
        "days_to_close_min/days_to_close_max: for a DURATION question about a case's OWN "
        "opened_date vs closed_date ('cases closed within 1 month/30 days of opening', 'took "
        "longer than 90 days to close') — do NOT approximate this with opened_after/"
        "opened_before/closed_after/closed_before, those are independent absolute date-range "
        "floors/ceilings across the whole matching set and cannot express 'this case's own two "
        "dates were close together'. Pass days_to_close_max=30 for 'within 1 month' (1 month = "
        "30 days here). Only cases with BOTH opened_date and closed_date set are matched; each "
        "surviving row gets a `days_to_close` column with the real computed gap.\n\n"
        "Returns a flat items[] — one row per surviving case, with EVERY field MyCase returns "
        "for that case (id, case_number, name, case_stage, practice_area, status, clients, "
        "staff, etc.) as its own column, PLUS client_name (resolved from the case's clients) "
        "and assigned_attorney (resolved from the staff member flagged lead_lawyer=true — "
        "'(unassigned)' if none). Custom fields are ALSO broken out into their own column "
        "labelled with the real field name (e.g. 'CASE TYPE', 'PROCESSING AGENT') instead of "
        "one nested blob. PROCESSING AGENT additionally gets a 'PROCESSING AGENT (original)' "
        "column holding the raw, unmodified value — the main 'PROCESSING AGENT' column is "
        "whitespace-cleaned and blank/null values become '(unassigned)'. Plus that case's "
        "group_name and the group's case_count. Report-level totals (total_cases, total_groups, "
        "group_by_field, report_date) are returned once at the top level, not repeated per row, "
        "along with a `groups` array — [{name, count}, ...] sorted highest-count-first — the "
        "correct source for ANY 'group X by Y' or 'which Y has the most/fewest' request: render "
        "one line/row per group directly from `groups` (do not just dump the flat `items` list "
        "as a single undifferentiated table when the user asked for a breakdown). Present each "
        "item's fields as-is (each field, including each custom field, as its own column) — "
        "no further math needed.\n\n"
        "limit: use this whenever the user asked for a SPECIFIC NUMBER of cases (e.g. 'show me "
        "5 immigration cases') — do NOT use search_cases for this (search_cases is only for "
        "finding one already-identified case by name/number, it has no practice-area/limit "
        "concept). limit caps how many rows come back in items; total_cases/total_groups/each "
        "row's case_count still reflect the TRUE full-match totals, not the limited count — "
        "cases_shown tells you how many rows were actually returned and limited (bool) tells "
        "you whether more existed than were shown.\n"
        "include_invoices: set True whenever the request also wants each case's invoices/"
        "billing (e.g. '...and their invoices', '...with billing info'). This fetches every "
        "firm invoice ONCE (sized to the firm's real total, capped at 50000 as a safety ceiling — "
        "ALL of them regardless of online-payment status, not just the online-payable subset) "
        "and attaches each returned case's own invoices "
        "directly onto its row (invoice_count, outstanding_invoice_total, invoices[]) in the "
        "SAME call — do NOT call get_case_invoices in a loop, once per case, instead: that "
        "re-scans MyCase's entire invoice list from scratch on every single call (slow), and it "
        "is easy to forget to repeat it for every case, which is exactly why 'N cases and their "
        "invoices' requests have come back with cases but only one case's invoices, or none at "
        "all. If the invoice scan itself fails or times out, the case rows are still returned "
        "successfully with an `invoices_error` field explaining what happened instead of "
        "losing the whole report — check for that field and report it plainly rather than "
        "assuming every case's invoices were empty."
    ),
)
async def aggregate_cases(
    practice_area: str | None = None,
    custom_field_filters: dict | None = None,
    case_stages: list | None = None,
    exclude_case_stages: list | None = None,
    group_by: str | None = None,
    status: str | None = None,
    updated_after: str | None = None,
    updated_before: str | None = None,
    opened_after: str | None = None,
    opened_before: str | None = None,
    closed_after: str | None = None,
    closed_before: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    sol_date_after: str | None = None,
    sol_date_before: str | None = None,
    days_to_close_min: int | None = None,
    days_to_close_max: int | None = None,
    min_group_size: int | None = None,
    max_group_size: int | None = None,
    limit: int | None = None,
    include_invoices: bool = False,
) -> dict:
    return await _call(
        "aggregate_cases", practice_area=practice_area, custom_field_filters=custom_field_filters,
        case_stages=case_stages, exclude_case_stages=exclude_case_stages, group_by=group_by,
        status=status, updated_after=updated_after, updated_before=updated_before,
        opened_after=opened_after, opened_before=opened_before,
        closed_after=closed_after, closed_before=closed_before,
        created_after=created_after, created_before=created_before,
        sol_date_after=sol_date_after, sol_date_before=sol_date_before,
        days_to_close_min=days_to_close_min, days_to_close_max=days_to_close_max,
        min_group_size=min_group_size, max_group_size=max_group_size,
        limit=limit, include_invoices=include_invoices,
    )


@mycase_mcp.tool(
    name="search_cases",
    description=(
        "Find case(s) by a loose text query against case_number OR case name — use this for "
        "ANY 'find/get the case numbered X' or 'find the case named/about X' request. MyCase's "
        "API has NO server-side filter for case_number or name (get_cases only supports "
        "filter[status] and filter[updated_after]), so do NOT call get_cases and try to "
        "eyeball-match the case yourself from a large unfiltered page — that's unreliable and "
        "exposes a pile of irrelevant cases. This tool walks every page internally. A NUMERIC "
        "query is checked as an exact match against case_number first, then the case's own "
        "internal numeric id (a purely numeric query is always an identifier lookup, never a "
        "substring search). A TEXT query is matched (substring, case-insensitive) against "
        "case_number OR name — e.g. query='Ogbuehi' matches by name. An exact case_number/id "
        "match always wins over a coincidental name substring hit elsewhere (a case's display "
        "name often embeds a padded number too, e.g. '01597-Smith'). "
        "If the whole query doesn't match as one literal substring, it automatically falls back "
        "to multi-word matching: filler words (case, matter, for, the, a, an, of, and, in, on, "
        "re) are stripped, and a case matches if EVERY remaining significant word appears "
        "somewhere in case_number + name + practice_area + that case's CASE TYPE custom field "
        "value (any order) — so a natural description like 'ASYLUM Case for MOISE PIERRE' finds "
        "a case whose NAME is just '01639-Moise Pierre MOISE PIERRE-IMMIGRATION' but whose CASE "
        "TYPE custom field value is 'Asylum' — the word 'asylum' may genuinely not be anywhere "
        "in the case name at all, only in that field. Still not fuzzy/typo-tolerant, and a query "
        "needs at least 2 significant words for this fallback to trigger (a single word already "
        "matches via plain substring). Returns "
        "match_count and cases_scanned so you know if nothing matched vs. it matched everything. "
        "query is REQUIRED — never call this with only status and no query. This tool takes ONLY "
        "query and status; it does NOT accept client_id or field_client. For a case's client "
        "details use get_case(case_id, field_client='id,first_name,last_name,email') or "
        "get_client(client_id) instead — not this tool. "
        "NOT for 'show me N cases of type X' (e.g. 'show me 5 immigration cases') — that is a "
        "filtered-and-limited LISTING, not a text search for one already-identified case; use "
        "aggregate_cases(practice_area=..., limit=N) instead, which has an actual limit concept "
        "and can also attach invoices in the same call."
    ),
)
async def search_cases(query: str, status: str | None = None) -> dict:
    return await _call("search_cases", query=query, status=status)


@mycase_mcp.tool(
    name="get_case_invoices",
    description=(
        "Get ALL invoices for ONE case — the correct tool for 'invoices for case X' / "
        "'invoices related to the ASYLUM case for MOISE PIERRE' style requests. Pass EITHER "
        "case_id (if already known) OR case_query (a case_number, numeric case id, or case name/"
        "description — resolved exactly like search_cases: a numeric query is checked against "
        "case_number then id; a text query is substring-matched against case_number OR name). "
        "get_invoices has NO server-side case filter, so this does the case lookup AND the "
        "invoice filtering internally, in one call. "
        "IMPORTANT: if case_query matches NO case, this returns immediately with matched_case=null "
        "and an empty items[] — invoices are NOT scanned in that situation (there's nothing to "
        "filter against, and doing so would waste a full firm-wide fetch for a guaranteed-empty, "
        "misleading result). Report exactly that plainly ('no case found matching X') and suggest "
        "an alternative (search by client name, or ask for the exact case id/case number) — do NOT "
        "then call get_invoices yourself to keep looking; that repeats the exact mistake this tool "
        "exists to prevent. If case_query matches MULTIPLE cases, `candidates` lists them — ask the "
        "user which one before doing anything else. "
        "Defaults to ALL of the case's invoices regardless of online-payment status "
        "(only_allowed_online_payments=False unless you pass True yourself) — unlike raw "
        "get_invoices, this does not silently drop invoices with online payments disabled."
    ),
)
async def get_case_invoices(
    case_id: int | None = None, case_query: str | None = None,
    only_allowed_online_payments: bool | None = None, max_invoices: int | None = None,
) -> dict:
    kwargs = {"case_id": case_id, "case_query": case_query, "only_allowed_online_payments": only_allowed_online_payments}
    if max_invoices is not None:
        kwargs["max_invoices"] = max_invoices
    return await _call("get_case_invoices", **kwargs)


# ── UTBMS code reference (static lookup, not a MyCase API endpoint) ──────────────

@mycase_mcp.tool(
    name="lookup_utbms_code",
    description=(
        "Look up what a UTBMS (LEDES Code Set II 1999B) activity or task code means — "
        "use this when a time entry's utbms_activity_code or utbms_task_code needs "
        "explaining. Not a MyCase API call; this is a static reference table."
    ),
)
async def lookup_utbms_code(code: str) -> dict:
    from app.services.mycase_utbms import lookup_utbms_code as _lookup

    result = _lookup(code)
    if result is None:
        return {"success": False, "error": f"'{code}' is not a recognised UTBMS activity or task code."}
    return {"success": True, **result}


if __name__ == "__main__":
    mycase_mcp.run()
