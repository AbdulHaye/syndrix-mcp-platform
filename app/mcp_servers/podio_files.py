"""Custom MCP server: Podio file attachment.

Fills the gap in Podio's hosted MCP server (which cannot upload/attach files). The
file *bytes* never travel through the LLM — they are staged to Podio via the REST
API (POST /integrations/podio-files/upload) which returns a ``file_id``. The LLM
then calls these tools with IDs only to attach the staged file to a record.

This is a genuine, standalone MCP server:
  • mounted in-process at /podio-files-mcp (see app/main.py) for the agent, and
  • runnable on its own via ``python -m app.mcp_servers.podio_files`` (stdio) for any
    other MCP client.
"""
from __future__ import annotations

from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

logger = structlog.get_logger(__name__)

files_mcp = FastMCP("podio-files")

_REF_TYPES = {"item", "task", "comment", "status", "space"}


@files_mcp.tool(
    name="attach_file_to_item",
    description=(
        "Attach an already-uploaded Podio file to an item (e.g. a contact, lead, or any "
        "app record). Use this when the user has uploaded/staged a file and wants it on a "
        "specific record. You must pass the numeric file_id of the staged upload (provided "
        "to you in the conversation context) and the target item_id. Resolve a person/record "
        "name to its item_id first (via get_items) — never guess."
    ),
)
async def attach_file_to_item(file_id: int, item_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.attach_file(int(file_id), "item", int(item_id))
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("attach_file_to_item_failed", error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="attach_file",
    description=(
        "Attach an already-uploaded Podio file (by numeric file_id) to any object. ref_type "
        "must be one of: item, task, comment, status, space. ref_id is that object's numeric id. "
        "For a contact/lead/app record use ref_type='item'."
    ),
)
async def attach_file(file_id: int, ref_type: str, ref_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    rt = (ref_type or "").strip().lower()
    if rt not in _REF_TYPES:
        return {"success": False, "error": f"ref_type must be one of {sorted(_REF_TYPES)}; got '{ref_type}'."}
    try:
        result = await podio_rest.attach_file(int(file_id), rt, int(ref_id))
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("attach_file_failed", error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="set_item_image",
    description=(
        "Set an already-uploaded Podio file as an item's IMAGE / profile picture (e.g. a "
        "contact's or Test User's photo). Podio app items have no separate 'profile picture' — "
        "the item's image is an Image FIELD on its app, so this sets that field. Pass the numeric "
        "file_id of the staged upload and the target item_id. Use this (not attach_file_to_item) "
        "when the user asks to set a picture/photo/avatar/profile image on a record."
    ),
)
async def set_item_image(file_id: int, item_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.set_item_image(int(item_id), int(file_id))
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_item_image_failed", error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="update_item",
    description=(
        "Update one or more fields on an existing Podio item via the REST API. "
        "Pass item_id (integer) and fields as a dict mapping each field's external_id to its "
        "new value — same format as create_item (text→str, category→option id, "
        "phone/email→[{type,value}], relationship→item_id int). "
        "Use this to rename, change status, update contact details, etc. "
        "Get the exact external_ids from get_app before calling. Never call for read operations."
    ),
)
async def update_item(item_id: int, fields: dict) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.update_item(int(item_id), fields)
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_item_failed", item_id=item_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="download_file",
    description=(
        "Retrieve/download a file already attached to a Podio item by its file_id. "
        "For text-based files (plain text, CSV, JSON, Markdown, XML, HTML, YAML, log files) "
        "this returns the actual file content so you can read and process it. "
        "For binary files (images, PDFs, Office documents) it returns a download URL "
        "the user can open in their browser. "
        "To get the file_id first, call get_item_files(item_id)."
    ),
)
async def download_file(file_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    _TEXT_MIMETYPES = (
        "text/",
        "application/json",
        "application/xml",
        "application/x-yaml",
        "application/csv",
    )
    _MAX_INLINE_BYTES = 50_000  # 50 KB — stay well within context limits

    try:
        filename, mimetype, content_bytes = await podio_rest.download_file(int(file_id))
        is_text = any(mimetype.startswith(m) for m in _TEXT_MIMETYPES) or (
            not mimetype or mimetype == "application/octet-stream"
            and filename.rsplit(".", 1)[-1].lower() in (
                "txt", "csv", "json", "md", "yaml", "yml", "xml", "html", "log", "ini", "toml"
            )
        )
        result: dict[str, Any] = {
            "success": True,
            "file_id": file_id,
            "filename": filename,
            "mimetype": mimetype,
            "size": len(content_bytes),
            "download_url": f"http://localhost:8000/integrations/podio-files/download/{file_id}",
        }
        if is_text and len(content_bytes) <= _MAX_INLINE_BYTES:
            result["content"] = content_bytes.decode("utf-8", errors="replace")
        elif is_text:
            result["content"] = content_bytes[:_MAX_INLINE_BYTES].decode("utf-8", errors="replace")
            result["truncated"] = True
            result["note"] = f"Content truncated to {_MAX_INLINE_BYTES} bytes. Full file at download_url."
        else:
            result["note"] = "Binary file — use download_url to retrieve it."
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("download_file_failed", file_id=file_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_item_files",
    description=(
        "List all files/attachments on a Podio item. Returns each file's file_id, name, "
        "size, and mimetype. Use this FIRST when the user asks to delete, view, or manage "
        "a file on a record — you need the file_id before you can delete it."
    ),
)
async def get_item_files(item_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        files = await podio_rest.get_item_files(int(item_id))
        return {"success": True, "item_id": item_id, "files": files, "count": len(files)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_item_files_failed", item_id=item_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="delete_file",
    description=(
        "Permanently delete a FILE/ATTACHMENT from Podio by its file_id. "
        "Use this when the user wants to delete, remove, or detach an uploaded file. "
        "NEVER use delete_item for this — delete_item removes the entire record. "
        "Workflow: call get_item_files(item_id) to find the file_id → show the user "
        "the file name and ask for confirmation → then call delete_file(file_id). "
        "THIS CANNOT BE UNDONE."
    ),
)
async def delete_file(file_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.delete_file(int(file_id))
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_file_failed", file_id=file_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="delete_item",
    description=(
        "Permanently delete an entire Podio RECORD (item). THIS CANNOT BE UNDONE. "
        "⚠️  DO NOT use this to delete a file or attachment — use delete_file for that. "
        "Only use delete_item when the user explicitly says 'delete this record', "
        "'delete this contact', 'delete this agent', etc. — meaning the WHOLE PROFILE. "
        "ALWAYS fetch the item first (get_item) to confirm its name/title, show the user "
        "exactly what will be deleted (name + item_id), and WAIT for explicit confirmation "
        "before calling this tool. Never call speculatively."
    ),
)
async def delete_item(item_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.delete_item(int(item_id))
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_item_failed", item_id=item_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_recurrence",
    description=(
        "Retrieve the recurring schedule set on a Podio task. "
        "ref_type must be 'task'. ref_id is the task's numeric ID. "
        "Returns the full recurrence config including step (daily/weekly/monthly) and frequency options."
    ),
)
async def get_recurrence(ref_type: str, ref_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.get_recurrence(ref_type, int(ref_id))
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_recurrence_failed", ref_type=ref_type, ref_id=ref_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="set_recurrence",
    description=(
        "Create or update a recurring schedule on a Podio task (same endpoint for both). "
        "ref_type must be 'task'. ref_id is the task's numeric ID. "
        "schedule must include a 'step' key — 'daily', 'weekly', or 'monthly' — plus step-specific options: "
        "daily: no extra keys needed; "
        "weekly: add 'days_of_week' as a list of ints (0=Mon, 1=Tue, … 6=Sun); "
        "monthly: add 'day_of_month' as an int (1–31). "
        "Returns confirmation with the stored schedule."
    ),
)
async def set_recurrence(ref_type: str, ref_id: int, schedule: dict[str, Any]) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.set_recurrence(ref_type, int(ref_id), schedule)
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_recurrence_failed", ref_type=ref_type, ref_id=ref_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="delete_recurrence",
    description=(
        "Remove the recurring schedule from a Podio task. "
        "ref_type must be 'task'. ref_id is the task's numeric ID. "
        "Returns confirmation."
    ),
)
async def delete_recurrence(ref_type: str, ref_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.delete_recurrence(ref_type, int(ref_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_recurrence_failed", ref_type=ref_type, ref_id=ref_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_reminder",
    description=(
        "Retrieve the reminder set on a Podio task or item. "
        "ref_type must be 'task' or 'item'. ref_id is the object's numeric ID. "
        "Returns the remind_at datetime and/or remind_delta (minutes before due date)."
    ),
)
async def get_reminder(ref_type: str, ref_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.get_reminder(ref_type, int(ref_id))
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_reminder_failed", ref_type=ref_type, ref_id=ref_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="set_reminder",
    description=(
        "Set or update a reminder on a Podio task or item. ref_type must be 'task' or 'item'. "
        "ref_id is the object's numeric ID. Podio reminders are RELATIVE — they fire a number of "
        "minutes BEFORE the object's due date. Preferred: pass remind_delta (integer minutes before "
        "the due date, e.g. 1440 = one day before, 60 = one hour before). Alternatively pass "
        "remind_at (absolute 'YYYY-MM-DD HH:MM:SS') and it is converted to a delta using the "
        "object's due date — this REQUIRES the item/task to already have a due date, and the time "
        "must be before that due date. Returns the stored remind_delta."
    ),
)
async def set_reminder(
    ref_type: str,
    ref_id: int,
    remind_delta: int | None = None,
    remind_at: str | None = None,
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.set_reminder(
            ref_type, int(ref_id), remind_delta=remind_delta, remind_at=remind_at
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_reminder_failed", ref_type=ref_type, ref_id=ref_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="delete_reminder",
    description=(
        "Remove the reminder from a Podio task or item. "
        "ref_type must be 'task' or 'item'. ref_id is the object's numeric ID. "
        "Returns confirmation."
    ),
)
async def delete_reminder(ref_type: str, ref_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.delete_reminder(ref_type, int(ref_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_reminder_failed", ref_type=ref_type, ref_id=ref_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_calendar",
    description=(
        "Retrieve upcoming tasks and item due dates across the authenticated user's entire Podio account. "
        "Returns each event with title, due_date, type (task or item), ref_id, and a link to the source object. "
        "Optional date_from / date_to (YYYY-MM-DD) bound the window; omit them to default to today → +90 days."
    ),
)
async def get_calendar(date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        events = await podio_rest.get_calendar(date_from=date_from, date_to=date_to)
        return {"success": True, "events": events, "count": len(events)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_calendar_failed", error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_space_calendar",
    description=(
        "Retrieve calendar events (tasks and item due dates) scoped to a specific Podio workspace. "
        "Pass the space_id. Optional date_from / date_to (YYYY-MM-DD) bound the window; omit them to "
        "default to today → +90 days. Returns each event with title, due_date, type, ref_id, and link."
    ),
)
async def get_space_calendar(
    space_id: int, date_from: str | None = None, date_to: str | None = None
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        events = await podio_rest.get_space_calendar(int(space_id), date_from=date_from, date_to=date_to)
        return {"success": True, "space_id": space_id, "events": events, "count": len(events)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_space_calendar_failed", space_id=space_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_app_calendar",
    description=(
        "Retrieve calendar events for items with due dates in a specific Podio app. "
        "Pass the app_id. Optional date_from / date_to (YYYY-MM-DD) bound the window; omit them to "
        "default to today → +90 days. Returns each event with title, due_date, type, ref_id, and link."
    ),
)
async def get_app_calendar(
    app_id: int, date_from: str | None = None, date_to: str | None = None
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        events = await podio_rest.get_app_calendar(int(app_id), date_from=date_from, date_to=date_to)
        return {"success": True, "app_id": app_id, "events": events, "count": len(events)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_app_calendar_failed", app_id=app_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="list_linked_accounts",
    description=(
        "List the user's EXTERNAL calendars/accounts added via Podio's 'Add Calendar' "
        "(Google, Exchange, Live, etc.). These are NOT covered by get_calendar / "
        "get_space_calendar / get_app_calendar, which only return Podio-native items and tasks. "
        "Pass capability='calendar' to get only calendar accounts. Returns each account's "
        "linked_account_id, label, and provider — use the id with get_linked_account_calendar."
    ),
)
async def list_linked_accounts(
    capability: str | None = "calendar", provider: str | None = None
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        accounts = await podio_rest.list_linked_accounts(capability=capability, provider=provider)
        return {"success": True, "accounts": accounts, "count": len(accounts)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_linked_accounts_failed", error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_linked_account_calendar",
    description=(
        "Retrieve events from ONE externally added (linked-account) calendar — a calendar the "
        "user connected via Podio's 'Add Calendar' (Google/Exchange/Live). Get the "
        "linked_account_id from list_linked_accounts(capability='calendar') first. "
        "Optional date_from / date_to (YYYY-MM-DD) bound the window; omit for today → +90 days."
    ),
)
async def get_linked_account_calendar(
    linked_account_id: int, date_from: str | None = None, date_to: str | None = None
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        events = await podio_rest.get_linked_account_calendar(
            int(linked_account_id), date_from=date_from, date_to=date_to
        )
        return {
            "success": True,
            "linked_account_id": linked_account_id,
            "events": events,
            "count": len(events),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "get_linked_account_calendar_failed", linked_account_id=linked_account_id, error=str(exc)
        )
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="create_conversation",
    description=(
        "Start a new private Podio conversation thread with one or more users. "
        "participant_ids: list of numeric user_ids to include — use get_space_members to look them up. "
        "subject: the thread subject line. "
        "text: the opening message body. "
        "Returns the new conversation_id."
    ),
)
async def create_conversation(
    participant_ids: list[int], subject: str, text: str
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.create_conversation(participant_ids, subject, text)
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_conversation_failed", error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="reply_to_conversation",
    description=(
        "Add a reply to an existing Podio conversation thread. "
        "Pass the conversation_id and the reply text. "
        "Returns the new message_id and timestamp."
    ),
)
async def reply_to_conversation(conversation_id: int, text: str) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.reply_to_conversation(int(conversation_id), text)
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("reply_to_conversation_failed", conversation_id=conversation_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="list_conversations",
    description=(
        "List all Podio conversation threads for the authenticated user. "
        "Returns each thread's ID, subject, participants, last message preview, and unread count."
    ),
)
async def list_conversations() -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        convs = await podio_rest.list_conversations()
        return {"success": True, "conversations": convs, "count": len(convs)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_conversations_failed", error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_conversation",
    description=(
        "Retrieve a specific Podio conversation thread with all messages in full. "
        "Pass the conversation_id. Returns subject, participants, and the complete message history "
        "with sender name and timestamp for each message."
    ),
)
async def get_conversation(conversation_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        conv = await podio_rest.get_conversation(int(conversation_id))
        return {"success": True, **conv}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_conversation_failed", conversation_id=conversation_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="create_app",
    description=(
        "Create a new Podio app inside an existing workspace. "
        "space_id: the workspace to create the app in. "
        "name: the app's display name. "
        "item_name: what a single record in this app is called (e.g. 'Contact', 'Lead', 'Deal'). "
        "fields: optional list of initial field definitions — each must have a 'type' key "
        "and a 'config' dict with at minimum a 'label' (e.g. {'type': 'text', 'config': {'label': 'Name'}}). "
        "Returns the new app_id and its URL."
    ),
)
async def create_app(
    space_id: int,
    name: str,
    item_name: str,
    fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.create_app(int(space_id), name, item_name, fields=fields)
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_app_failed", space_id=space_id, name=name, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="invite_workspace_member",
    description=(
        "Invite a user to a Podio workspace. "
        "Pass space_id and identifier — either an email address or a numeric user_id. "
        "Returns confirmation with the invited identifier."
    ),
)
async def invite_workspace_member(space_id: int, identifier: str) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.invite_workspace_member(int(space_id), identifier)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("invite_workspace_member_failed", space_id=space_id, identifier=identifier, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="remove_workspace_member",
    description=(
        "Remove a member from a Podio workspace. "
        "Pass space_id and the numeric user_id of the member to remove. "
        "Use get_space_members to look up user IDs before calling."
    ),
)
async def remove_workspace_member(space_id: int, user_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.remove_workspace_member(int(space_id), int(user_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("remove_workspace_member_failed", space_id=space_id, user_id=user_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="update_workspace_member_role",
    description=(
        "Change a workspace member's access level. "
        "Pass space_id, the numeric user_id, and role — must be exactly 'admin', 'regular', or 'light'. "
        "Returns confirmation with the new role."
    ),
)
async def update_workspace_member_role(space_id: int, user_id: int, role: str) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.update_workspace_member_role(int(space_id), int(user_id), role)
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_workspace_member_role_failed", space_id=space_id, user_id=user_id, role=role, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="update_workspace",
    description=(
        "Update a Podio workspace's name, privacy setting, or URL label. "
        "Pass space_id and at least one of: name, privacy ('open' or 'closed'), url_label. "
        "Returns the updated workspace state."
    ),
)
async def update_workspace(
    space_id: int,
    name: str | None = None,
    privacy: str | None = None,
    url_label: str | None = None,
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    if name is None and privacy is None and url_label is None:
        return {"success": False, "error": "At least one of 'name', 'privacy', or 'url_label' must be provided."}
    try:
        result = await podio_rest.update_workspace(int(space_id), name=name, privacy=privacy, url_label=url_label)
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_workspace_failed", space_id=space_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="archive_workspace",
    description=(
        "Archive a Podio workspace, making it read-only and hidden from active views. "
        "This is reversible — use restore_workspace to undo. "
        "Archive before deleting so the action can be rolled back if needed."
    ),
)
async def archive_workspace(space_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.archive_workspace(int(space_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("archive_workspace_failed", space_id=space_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="restore_workspace",
    description=(
        "Restore a previously archived Podio workspace, making it active again. "
        "Pass the space_id of the archived workspace. Returns confirmation with status='active'."
    ),
)
async def restore_workspace(space_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.restore_workspace(int(space_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("restore_workspace_failed", space_id=space_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="create_workspace",
    description=(
        "Create a new Podio workspace inside an organisation. "
        "org_id: the organisation to create the workspace in — use get_organizations to look it up. "
        "name: the workspace display name. "
        "privacy: 'open' (all org members can join) or 'closed' (invite only). "
        "url_label: optional URL slug; Podio derives one from the name if omitted. "
        "Returns the new space_id and full workspace URL."
    ),
)
async def create_workspace(
    org_id: int,
    name: str,
    privacy: str,
    url_label: str | None = None,
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.create_workspace(int(org_id), name, privacy, url_label=url_label)
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_workspace_failed", org_id=org_id, name=name, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="delete_workspace",
    description=(
        "Permanently delete a Podio workspace and all its apps and data. THIS CANNOT BE UNDONE. "
        "Always confirm the workspace name with the user (via get_spaces_in_organization) "
        "and wait for explicit approval before calling this tool. Never call speculatively."
    ),
)
async def delete_workspace(space_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.delete_workspace(int(space_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_workspace_failed", space_id=space_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="delete_webhook",
    description=(
        "Permanently delete an active Podio webhook by its hook_id. THIS CANNOT BE UNDONE. "
        "Always call list_webhooks first to confirm the URL and event type, "
        "show the user exactly what will be removed, and wait for explicit confirmation "
        "before calling this tool. Never call speculatively."
    ),
)
async def delete_webhook(hook_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.delete_webhook(int(hook_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_webhook_failed", hook_id=hook_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="request_webhook_verification",
    description=(
        "Step 1 of 2 — trigger Podio to send a verification code to a webhook's callback URL. "
        "Pass the hook_id returned by create_webhook. "
        "After calling this, retrieve the code from your callback endpoint, "
        "then call validate_webhook_verification(hook_id, code) to complete activation."
    ),
)
async def request_webhook_verification(hook_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.request_webhook_verification(int(hook_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("request_webhook_verification_failed", hook_id=hook_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="validate_webhook_verification",
    description=(
        "Step 2 of 2 — submit the verification code to activate a Podio webhook. "
        "Pass the hook_id and the code received at your callback URL after calling "
        "request_webhook_verification. On success, the webhook becomes active and "
        "will start receiving events."
    ),
)
async def validate_webhook_verification(hook_id: int, code: str) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.validate_webhook_verification(int(hook_id), code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("validate_webhook_verification_failed", hook_id=hook_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="create_webhook",
    description=(
        "Register a new webhook on a Podio app or workspace. "
        "ref_type must be 'app' or 'space'. ref_id is its numeric ID. "
        "url is the callback URL that will receive POST requests — must use port 80 or 443 only. "
        "event_type is the event to subscribe to (e.g. 'item.create', 'item.update', 'item.delete'). "
        "Returns the new hook_id."
    ),
)
async def create_webhook(ref_type: str, ref_id: int, url: str, event_type: str) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.create_webhook(ref_type, int(ref_id), url, event_type)
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_webhook_failed", ref_type=ref_type, ref_id=ref_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="list_webhooks",
    description=(
        "List all webhooks registered on a Podio app or workspace. "
        "ref_type must be 'app' or 'space'. ref_id is its numeric ID. "
        "Returns each hook's ID, callback URL, event type, and verification status."
    ),
)
async def list_webhooks(ref_type: str, ref_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        hooks = await podio_rest.list_webhooks(ref_type, int(ref_id))
        return {"success": True, "ref_type": ref_type, "ref_id": ref_id, "hooks": hooks, "count": len(hooks)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_webhooks_failed", ref_type=ref_type, ref_id=ref_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_flow_effect_attributes",
    description=(
        "Discover the required attribute IDs needed to configure a specific Podio flow effect. "
        "Pass app_id and effect_type (e.g. 'task.create', 'item.update', 'comment.create'). "
        "Returns each attribute's ID, label, type, and whether it is required. "
        "Use this before building the 'attributes' dict for create_flow or the 'values' dict for update_flow."
    ),
)
async def get_flow_effect_attributes(app_id: int, effect_type: str) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        attrs = await podio_rest.get_flow_effect_attributes(int(app_id), effect_type)
        return {"success": True, "app_id": app_id, "effect_type": effect_type, "attributes": attrs, "count": len(attrs)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_flow_effect_attributes_failed", app_id=app_id, effect_type=effect_type, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_flow_possible_attributes",
    description=(
        "Return the dynamic expressions available for a specific attribute within a Podio flow effect. "
        "Pass app_id, effect_type (e.g. 'task.create'), and the attribute_id from get_flow_effect_attributes. "
        "Returns injectable expressions (e.g. {{item.creator}}, field values) with their label and type. "
        "Use this to discover valid values before populating an effect's attribute."
    ),
)
async def get_flow_possible_attributes(app_id: int, effect_type: str, attribute_id: str) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        options = await podio_rest.get_flow_possible_attributes(int(app_id), effect_type, attribute_id)
        return {
            "success": True,
            "app_id": app_id,
            "effect_type": effect_type,
            "attribute_id": attribute_id,
            "options": options,
            "count": len(options),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "get_flow_possible_attributes_failed",
            app_id=app_id,
            effect_type=effect_type,
            attribute_id=attribute_id,
            error=str(exc),
        )
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="delete_flow",
    description=(
        "Permanently delete a Podio automated workflow by its flow_id. THIS CANNOT BE UNDONE. "
        "Always call get_flow first to confirm the flow name and trigger type, "
        "show the user exactly what will be deleted, and wait for explicit confirmation "
        "before calling this tool. Never call speculatively."
    ),
)
async def delete_flow(flow_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.delete_flow(int(flow_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_flow_failed", flow_id=flow_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="update_flow",
    description=(
        "Update an existing Podio automated workflow. "
        "Pass flow_id and at least one of: name (rename), config (trigger config), "
        "or effects (replace the actions list). "
        "Effects on update must use the 'values' key for parameters — not 'attributes'. "
        "⚠️  The trigger type (item.create / item.update) cannot be changed. "
        "If you pass trigger_type and it differs from the current value, the tool returns "
        "an error instructing you to delete and recreate the flow instead."
    ),
)
async def update_flow(
    flow_id: int,
    name: str | None = None,
    config: dict[str, Any] | None = None,
    effects: list[dict[str, Any]] | None = None,
    trigger_type: str | None = None,
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    if name is None and config is None and effects is None:
        return {"success": False, "error": "At least one of 'name', 'config', or 'effects' must be provided."}
    try:
        result = await podio_rest.update_flow(
            int(flow_id), name=name, config=config, effects=effects, trigger_type=trigger_type
        )
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_flow_failed", flow_id=flow_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="create_flow",
    description=(
        "Create a new automated workflow on a Podio app. "
        "app_id: the app to attach the flow to. "
        "trigger_type: 'item.create', 'item.update', or 'item.delete'. "
        "name: a human-readable name for the flow. "
        "effects: list of actions. Each effect MUST have 'type' and 'attributes' (an ARRAY of {attribute_id, value} objects). "
        "Official attribute_id strings (from Podio docs): "
        "comment.create → attribute_id='comment.value'; "
        "status.create → attribute_id='status.value'; "
        "task.create → attribute_id='task.text' and optionally 'task.due' (int days), 'task.responsible'; "
        "item.update → attribute_id='item.field.{external_id}'. "
        "Example comment flow: [{\"type\":\"comment.create\",\"attributes\":[{\"attribute_id\":\"comment.value\",\"value\":\"text\"}]}] "
        "field_ids (optional, item.update only): list of NUMERIC field IDs from get_app. "
        "ref_type is always 'app' and is set automatically. Returns new flow_id on success."
    ),
)
async def create_flow(
    app_id: int,
    trigger_type: str,
    name: str,
    effects: list[dict[str, Any]],
    field_ids: list[int] | None = None,
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    config = {"field_ids": [int(f) for f in field_ids]} if field_ids else None
    try:
        result = await podio_rest.create_flow(int(app_id), trigger_type, name, effects, config=config)
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_flow_failed", app_id=app_id, trigger_type=trigger_type, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_flow",
    description=(
        "Get the full definition of a specific Podio automated workflow by its flow_id. "
        "Returns the flow name, trigger type (item.create or item.update), active status, "
        "trigger configuration, and the complete list of effects (actions) the flow performs."
    ),
)
async def get_flow(flow_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        flow = await podio_rest.get_flow(int(flow_id))
        return {"success": True, **flow}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_flow_failed", flow_id=flow_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_flow_context",
    description=(
        "Return all variable attributes available for injection into a Podio flow's effect expressions. "
        "Pass the flow_id. Each attribute includes its template name (e.g. {{item.creator}}), "
        "a human-readable label, and its data type. "
        "Use this to discover what variables are valid before building or editing a flow."
    ),
)
async def get_flow_context(flow_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        attributes = await podio_rest.get_flow_attributes(int(flow_id))
        return {"success": True, "flow_id": flow_id, "attributes": attributes, "count": len(attributes)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_flow_context_failed", flow_id=flow_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_app_flows",
    description=(
        "List all automated workflows configured on a Podio app. "
        "Pass the app_id. Returns each flow's ID, name, trigger type "
        "(item.create or item.update), and whether it is currently active."
    ),
)
async def get_app_flows(app_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        flows = await podio_rest.get_app_flows(int(app_id))
        return {"success": True, "app_id": app_id, "flows": flows, "count": len(flows)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_app_flows_failed", app_id=app_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_tasks",
    description=(
        "List Podio tasks. For INCOMPLETE tasks: ALWAYS pass space_id (active workspace). "
        "For COMPLETED tasks: do NOT pass space_id — Podio ignores it and returns nothing; "
        "just set completed=true with no space_id. "
        "Parameters: space_id (int, incomplete tasks only), responsible_user_id (int, optional), "
        "completed (bool, default false), limit (default 50), offset (default 0). "
        "Returns tasks with task_id, text, due_date, completed, assigned_to, ref_type, ref_id, ref_title."
    ),
)
async def get_tasks(
    space_id: int | None = None,
    responsible_user_id: int | None = None,
    completed: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    if not await podio_rest.is_connected():
        return {"success": False, "error": "Not connected to Podio Files. Use Connect Files first."}
    try:
        return await podio_rest.get_tasks(
            space_id=space_id,
            responsible_user_id=responsible_user_id,
            completed=completed,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_tasks_failed", error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_task_summary",
    description=(
        "Get aggregated task statistics across the user's Podio workspaces. "
        "Returns total task count, number of completed tasks, and number of overdue tasks. "
        "Read-only — use this for a quick health-check on outstanding work."
    ),
)
async def get_task_summary() -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.get_task_summary()
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_task_summary_failed", error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_task_count",
    description=(
        "Return the total number of active (incomplete) Podio tasks for the authenticated user. "
        "Optional: space_id to limit count to a specific workspace. "
        "Returns count (total), own (tasks assigned to the user), and reassigned (tasks delegated to others). "
        "Note: this endpoint counts incomplete tasks only — it does not filter by completed status. "
        "Use get_task_summary for a breakdown by time category (overdue/today/upcoming). "
        "Read-only and lightweight — use to gauge task load before listing."
    ),
)
async def get_task_count(
    space_id: int | None = None,
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.get_task_count(space_id=space_id)
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_task_count_failed", error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="remove_task_reference",
    description=(
        "Remove the reference link between a Podio task and the item it is attached to, "
        "making it a standalone task with no linked object. "
        "Pass the task_id. Returns confirmation with ref_type and ref_id set to null. "
        "Use get_task first to confirm the current reference before removing it."
    ),
)
async def remove_task_reference(task_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.remove_task_reference(int(task_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("remove_task_reference_failed", task_id=task_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_reference_tasks",
    description=(
        "Retrieve all tasks linked to a specific Podio item or object. "
        "Pass ref_type (one of: item, app, space, status) and the numeric ref_id. "
        "Returns a list of tasks in the same shape as get_task: "
        "task_id, text, description, due_date, completed, assigned_to, and ref details."
    ),
)
async def get_reference_tasks(ref_type: str, ref_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        tasks = await podio_rest.get_reference_tasks(ref_type, int(ref_id))
        return {"success": True, "ref_type": ref_type, "ref_id": ref_id, "tasks": tasks, "count": len(tasks)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_reference_tasks_failed", ref_type=ref_type, ref_id=ref_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_task_labels",
    description="List all task labels available to the authenticated Podio user. Returns each label's ID, name, and color.",
)
async def get_task_labels() -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        labels = await podio_rest.get_task_labels()
        return {"success": True, "labels": labels, "count": len(labels)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_task_labels_failed", error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="create_task_label",
    description=(
        "Create a new Podio task label with a name and color. "
        "color is a hex string without '#', e.g. 'FF5733' for orange or 'DCDCDC' for grey. "
        "Returns the new label_id, name, and color."
    ),
)
async def create_task_label(name: str, color: str) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.create_task_label(name, color)
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_task_label_failed", name=name, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="update_task_label",
    description=(
        "Update an existing Podio task label's name, color, or both. "
        "Pass label_id and at least one of name or color. "
        "color is a hex string without '#'. "
        "Use get_task_labels to look up available label IDs before calling."
    ),
)
async def update_task_label(
    label_id: int,
    name: str | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    if name is None and color is None:
        return {"success": False, "error": "At least one of 'name' or 'color' must be provided."}
    try:
        result = await podio_rest.update_task_label(int(label_id), name=name, color=color)
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_task_label_failed", label_id=label_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="delete_task_label",
    description=(
        "Permanently delete a Podio task label by its ID. THIS CANNOT BE UNDONE. "
        "Use get_task_labels to confirm the label name before deleting."
    ),
)
async def delete_task_label(label_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.delete_task_label(int(label_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_task_label_failed", label_id=label_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="rank_task",
    description=(
        "⚠️  UNAVAILABLE — Podio's task rank API has been removed (HTTP 410 Gone). "
        "This tool will always return an error. Task ordering can only be changed manually "
        "in the Podio UI. Do NOT call this tool; inform the user that reordering tasks "
        "via the API is no longer supported by Podio."
    ),
)
async def rank_task(
    task_id: int,
    before: int | None = None,
    after: int | None = None,
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    if before is None and after is None:
        return {"success": False, "error": "At least one of 'before' or 'after' must be provided."}
    try:
        return await podio_rest.rank_task(int(task_id), before=before, after=after)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rank_task_failed", task_id=task_id, before=before, after=after, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="uncomplete_task",
    description=(
        "Mark a previously completed Podio task back as incomplete. "
        "Pass the task_id. This is the inverse of the complete_task tool. "
        "Returns confirmation with completed=false."
    ),
)
async def uncomplete_task(task_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.uncomplete_task(int(task_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("uncomplete_task_failed", task_id=task_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="reassign_task",
    description=(
        "Reassign an existing Podio task to a different user. "
        "Pass task_id and the numeric user_id of the new assignee. "
        "To find a user_id, use get_space_members on the active workspace. "
        "Returns the full updated task state so you can confirm the new assignee."
    ),
)
async def reassign_task(task_id: int, user_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        task = await podio_rest.reassign_task(int(task_id), int(user_id))
        return {"success": True, **task}
    except Exception as exc:  # noqa: BLE001
        logger.warning("reassign_task_failed", task_id=task_id, user_id=user_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="update_task",
    description=(
        "Update a Podio task's text, description, due date, or label. "
        "Parameters: task_id (int, required), text (str, optional), description (str, optional), "
        "due_on (str 'YYYY-MM-DD HH:MM:SS', optional), label_id (int, optional). "
        "To assign a label: first call get_task_labels to get the label_id, then call update_task(task_id, label_id=<id>). "
        "At least one of text/description/due_on/label_id must be provided."
    ),
)
async def update_task(
    task_id: int,
    text: str | None = None,
    description: str | None = None,
    due_on: str | None = None,
    label_id: int | None = None,
) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    if not await podio_rest.is_connected():
        return {"success": False, "error": "Not connected to Podio Files. Use Connect Files first."}
    try:
        return await podio_rest.update_task(
            int(task_id),
            text=text,
            description=description,
            due_on=due_on,
            label_id=label_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_task_failed", task_id=task_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="delete_task",
    description=(
        "Permanently delete a Podio task. THIS CANNOT BE UNDONE. "
        "Always call get_task first to confirm the task text and assignee, "
        "show the user exactly what will be deleted, and wait for explicit confirmation "
        "before calling this tool. Never call speculatively."
    ),
)
async def delete_task(task_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        return await podio_rest.delete_task(int(task_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_task_failed", task_id=task_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_task",
    description=(
        "Retrieve a single Podio task by its ID with full details. "
        "Returns the task text, description, due date, assigned user, "
        "completion status, and any linked reference object (the item or app the task is attached to). "
        "Use this to inspect a specific task before updating or completing it."
    ),
)
async def get_task(task_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        task = await podio_rest.get_task(int(task_id))
        return {"success": True, **task}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_task_failed", task_id=task_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="update_item_field",
    description=(
        "Update one specific field on a Podio record without touching any other fields. "
        "Pass item_id, field_id (numeric field_id or external_id string), and the new value. "
        "Value format must match the field type: text→string, category→option id integer, "
        "phone/email→[{type, value}], relationship/app-reference→item_id integer, image→[file_id]. "
        "Use this instead of update_item when only a single field needs to change — "
        "it is safer and avoids accidentally clearing other fields."
    ),
)
async def update_item_field(item_id: int, field_id: str, value: Any) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.update_item_field(int(item_id), field_id, value)
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_item_field_failed", item_id=item_id, field_id=field_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_item_references",
    description=(
        "Retrieve all Podio records that link to (reference) a given record. "
        "Pass the item_id of the target record. "
        "Returns a list of referencing items — each with item_id, title, app_id, and app_name — "
        "so you can understand what else in the workspace depends on or relates to this record. "
        "Useful for impact analysis before editing or deleting a record."
    ),
)
async def get_item_references(item_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        references = await podio_rest.get_item_references(int(item_id))
        return {"success": True, "item_id": item_id, "references": references, "count": len(references)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_item_references_failed", item_id=item_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="revert_item_revision",
    description=(
        "Roll back a Podio record to an earlier revision. "
        "Pass the item_id and the revision_id to revert to. "
        "Removes that revision and restores the record to the state it was in before it. "
        "Returns the record's full current state after the revert so you can confirm what changed. "
        "⚠️  This modifies the live record. Call get_item_revisions first to show the user "
        "the available revisions and confirm which one to revert before calling this tool."
    ),
)
async def revert_item_revision(item_id: int, revision_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        item = await podio_rest.revert_item_revision(int(item_id), int(revision_id))
        return {"success": True, "item_id": item_id, "revision_id": revision_id, "item": item}
    except Exception as exc:  # noqa: BLE001
        logger.warning("revert_item_revision_failed", item_id=item_id, revision_id=revision_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_item_revisions",
    description=(
        "Retrieve the full change history of a Podio record. "
        "Pass the item_id. Returns a list of revisions — each with revision_id, "
        "the name of who made the change, and when it was made — "
        "so you can audit what changed over time or identify who last edited a record."
    ),
)
async def get_item_revisions(item_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        revisions = await podio_rest.get_item_revisions(int(item_id))
        return {"success": True, "item_id": item_id, "revisions": revisions, "count": len(revisions)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_item_revisions_failed", item_id=item_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="get_items_by_view",
    description=(
        "Filter records in a Podio app using a pre-saved view definition. "
        "Pass the app_id and view_id. Fetches the view's filter conditions and sort order, "
        "then applies them to the app's items. "
        "Returns the same shape as get_items: total count, filtered count, and an items list. "
        "Use this when the user references a named view or wants to reuse a saved filter."
    ),
)
async def get_items_by_view(app_id: int, view_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.get_items_by_view(int(app_id), int(view_id))
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_items_by_view_failed", app_id=app_id, view_id=view_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="export_app_xlsx",
    description=(
        "Export all records from a Podio app as an Excel (.xlsx) file. "
        "Pass the app_id. Returns a download_url the user can open in their browser "
        "to save the spreadsheet — the backend proxies the export so no Podio token "
        "is required in the browser. Use this when the user asks to export, download, "
        "or back up all records from an app."
    ),
)
async def export_app_xlsx(app_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    if not await podio_rest.is_connected():
        return {"success": False, "error": "Not connected to Podio Files. Use Connect Files first."}
    try:
        return {
            "success": True,
            "app_id": app_id,
            "download_url": f"http://localhost:8000/integrations/podio-files/export/{app_id}",
            "note": "Open download_url in a browser to save the Excel file.",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("export_app_xlsx_failed", app_id=app_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="clone_item",
    description=(
        "Duplicate an existing Podio record within the same app. "
        "Pass the item_id of the record to clone. "
        "Returns the new cloned item's item_id so you can reference or update it immediately. "
        "Use this when the user asks to copy, duplicate, or clone a record."
    ),
)
async def clone_item(item_id: int) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    try:
        result = await podio_rest.clone_item(int(item_id))
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("clone_item_failed", item_id=item_id, error=str(exc))
        return {"success": False, "error": str(exc)}


@files_mcp.tool(
    name="bulk_delete_items",
    description=(
        "Permanently delete multiple Podio records from an app in a single operation. "
        "Pass the app_id and a list of item_ids to remove. "
        "⚠️  THIS CANNOT BE UNDONE. "
        "Always confirm with the user before calling — show them the list of records "
        "(fetch each via get_item if needed) and wait for explicit approval. "
        "Returns the count of deleted records and the item_ids that were removed."
    ),
)
async def bulk_delete_items(app_id: int, item_ids: list[int]) -> dict[str, Any]:
    from app.services.podio_rest import podio_rest

    if not item_ids:
        return {"success": False, "error": "item_ids must not be empty."}
    try:
        result = await podio_rest.bulk_delete_items(int(app_id), [int(i) for i in item_ids])
        return {"success": True, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("bulk_delete_items_failed", app_id=app_id, count=len(item_ids), error=str(exc))
        return {"success": False, "error": str(exc)}


if __name__ == "__main__":
    # Standalone stdio server for external MCP clients.
    files_mcp.run()
