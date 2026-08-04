"""Deterministic report spec → .xlsx workbook renderer.

WHY THIS EXISTS
---------------
Before this, an agent turn could only ever produce ONE flat table per resource
type (the frontend's `primaryResultGroups` merges every step of a resource into a
single table). So a perfectly reasonable request like "show active cases per
attorney, one sheet per attorney, plus a summary" had no code path that could
satisfy it — not because the prompt was wrong or a tool parameter was missing,
but because multi-sheet output did not exist anywhere in the system. Every new
presentation shape needed another hardcoded tool.

This module breaks that treadmill: the LLM emits a small declarative SPEC saying
how it wants the data presented (what to group by, what to split into sheets,
which columns, which aggregations) and this renders it. The LLM directs
PRESENTATION only — every count, sum and average below is computed here, in
plain deterministic Python, over the full row set.

That distinction is the whole point, and it is why this does not contradict the
project's long-standing "never let the LLM compute over many records" rule: that
rule exists because an LLM eyeballing truncated JSON and doing mental arithmetic
is unreliable. Nothing here asks it to. This is the safe, declarative form of the
"code execution" agent pattern — no arbitrary code runs, so it adds no new
attack surface, which matters because rows here contain untrusted third-party
text (client-written case notes) that the agent already treats as hostile input.

DELIBERATELY NOT PANDAS. All grouping/aggregation is plain dict-of-lists. At this
data scale (single-digit thousands of rows) it is more than fast enough, and it
keeps a large dependency out of the image.

This module is intentionally agent-agnostic — it knows nothing about MyCase or
Podio, only `list[dict]` + a spec — so the Podio Agent can adopt it unchanged.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from io import BytesIO
from typing import Any

import structlog
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

logger = structlog.get_logger(__name__)

# Excel's own hard limits, not ours.
_MAX_SHEET_NAME = 31
_MAX_ROWS_PER_SHEET = 1_048_575  # 1,048,576 minus the header row
_ILLEGAL_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")

# Ours, and both are disclosed in the output rather than applied silently.
#
# The sheet cap is not hypothetical: grouping this account's real cases by
# client_name produced 4,796 distinct groups. A 4,796-sheet workbook is unusable
# (and slow to write), so detail sheets are capped while the Summary sheet keeps
# EVERY group — you never lose the counts, only the per-group detail tabs.
_DEFAULT_MAX_DETAIL_SHEETS = 100
# How much of the summary is handed back to the LLM to narrate. The workbook
# always contains all of it; this only bounds what re-enters the model context.
_MAX_SUMMARY_ROWS_TO_CALLER = 50

_VALID_METRIC_OPS = {"count", "count_distinct", "sum", "avg", "min", "max"}

_HEADER_FONT = Font(bold=True)
_TITLE_FONT = Font(bold=True, size=14)


# ── value coercion ───────────────────────────────────────────────────────────


def _flatten_value(value: Any) -> Any:
    """Collapse a nested JSON value into something a spreadsheet cell can hold.

    Mirrors the frontend's `flattenValue` (frontend/lib/recordTable.ts) so the
    Excel export and the on-screen table/CSV never disagree about how a nested
    object renders. Scalars pass through untouched so numbers stay numeric.
    """
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value
    if isinstance(value, dict):
        # The common MyCase shapes: {"id": N}, or a person-ish object.
        name = " ".join(
            str(value[k]) for k in ("first_name", "last_name") if value.get(k)
        ).strip()
        if not name:
            name = str(value.get("name") or value.get("title") or "").strip()
        email = str(value.get("email") or "").strip()
        if name and email:
            return f"{name} <{email}>"
        if name:
            return name
        if email:
            return email
        if "id" in value:
            return value["id"]
        return "; ".join(f"{k}={v}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return "; ".join(str(_flatten_value(v)) for v in value if v is not None)
    return str(value)


# Only a string that is unambiguously a DECIMAL number becomes a real numeric
# cell. This is deliberately narrow: MyCase returns money fields as strings
# ("500.0" — a documented, previously-bug-causing quirk), and leaving those as
# text would make them un-summable in Excel. But a bare digit run must stay text,
# because that is what identifiers look like — "0012" would lose its leading zero
# and a 10-digit phone number would render in scientific notation.
_DECIMAL_STR = re.compile(r"^-?\d+\.\d+$")


def _as_float(value: Any) -> float | None:
    """Best-effort numeric coercion for METRICS only (never for display cells)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (ValueError, AttributeError):
            return None
    return None


def _cell(worksheet: Any, value: Any, *, bold: bool = False) -> Any:
    """Build a write-only cell that can never be interpreted as a formula.

    Spreadsheet formula injection (OWASP CWE-1236) is a real concern here: cell
    text originates from client-controlled MyCase records, and Excel executes any
    cell whose text begins with `=`, `+`, `-` or `@`. openpyxl makes this easy to
    hit by accident — its value setter treats a leading "=" as a formula.

    Rather than mangling the displayed text with a leading apostrophe, every
    string cell is pinned to data_type "s" (shared string), which Excel always
    renders literally. Numbers/dates keep their native types so they stay
    sortable and summable.
    """
    flat = _flatten_value(value)
    if isinstance(flat, str) and _DECIMAL_STR.match(flat.strip()):
        flat = float(flat)

    cell = WriteOnlyCell(worksheet, value=flat)
    if isinstance(flat, str):
        # MUST come after the value assignment — the setter is what would
        # otherwise have marked this as a formula.
        cell.data_type = "s"
    if bold:
        cell.font = _HEADER_FONT
    return cell


# ── sheet naming ─────────────────────────────────────────────────────────────


def _sanitize_sheet_name(raw: Any) -> str:
    """Excel sheet names: <=31 chars, no []:*?/\\, non-empty, not all-quotes."""
    name = str(_flatten_value(raw) if raw is not None else "").strip()
    name = _ILLEGAL_SHEET_CHARS.sub("-", name)
    name = name.strip("'").strip()
    if not name:
        name = "(blank)"
    return name[:_MAX_SHEET_NAME]


def _unique_sheet_name(name: str, used: set[str]) -> str:
    """De-collide names that are distinct in the data but identical once
    truncated to 31 chars (two long client names sharing a prefix is the
    realistic case). The suffix eats into the 31-char budget, never past it."""
    candidate = name
    n = 2
    while candidate.casefold() in used:
        suffix = f" ({n})"
        candidate = f"{name[: _MAX_SHEET_NAME - len(suffix)]}{suffix}"
        n += 1
    used.add(candidate.casefold())
    return candidate


# ── grouping / aggregation ───────────────────────────────────────────────────


def _group_label(row: dict[str, Any], column: str) -> str:
    """The group a row belongs to. Blank/missing collapses to a single explicit
    bucket rather than silently vanishing or splitting into ""/None/absent."""
    value = _flatten_value(row.get(column))
    label = str(value).strip()
    return label if label else "(none)"


def _sort_key(value: Any) -> tuple[int, float, str]:
    """Total ordering across mixed/missing types.

    Python 3 raises on `1 < "a"`, and real report columns are absolutely mixed
    (a null date beside a string date beside a number), so a naive sort would
    crash the whole report on one ragged row.
    """
    if value is None or value == "":
        return (2, 0.0, "")  # empties always sort last
    numeric = _as_float(value)
    if numeric is not None:
        return (0, numeric, "")
    return (1, 0.0, str(value).casefold())


def _sorted_rows(rows: list[dict[str, Any]], sort: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not sort or not sort.get("by"):
        return rows
    column = sort["by"]
    reverse = str(sort.get("dir", "asc")).lower() == "desc"
    return sorted(rows, key=lambda r: _sort_key(r.get(column)), reverse=reverse)


def _metric_label(metric: dict[str, Any]) -> str:
    op = metric["op"]
    column = metric.get("column")
    return op if op == "count" else f"{op} of {column}"


def _compute_metric(metric: dict[str, Any], rows: list[dict[str, Any]]) -> Any:
    op = metric["op"]
    if op == "count":
        return len(rows)

    column = metric["column"]
    if op == "count_distinct":
        return len({str(_flatten_value(r.get(column))) for r in rows})

    values = [v for v in (_as_float(r.get(column)) for r in rows) if v is not None]
    if not values:
        return 0
    if op == "sum":
        return round(sum(values), 2)
    if op == "avg":
        return round(sum(values) / len(values), 2)
    if op == "min":
        return min(values)
    return max(values)  # "max" — the only remaining validated op


def _derive_columns(rows: list[dict[str, Any]]) -> list[str]:
    """Union of keys in first-seen order — same rule as the frontend's
    `deriveColumns`, so an exported workbook and the on-screen table show the
    same columns in the same order."""
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return columns


# ── spec validation ──────────────────────────────────────────────────────────


def validate_spec(spec: Any) -> dict[str, Any]:
    """Raise ValueError with an actionable message, or return the spec.

    Errors here surface straight back to the model as the tool result, so each
    message names the offending value AND what to do instead — a bare "invalid
    spec" would just make it guess again.
    """
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object with a 'summary' and/or 'detail_sheets' key.")

    summary = spec.get("summary")
    detail = spec.get("detail_sheets")
    if summary is None and detail is None:
        raise ValueError(
            "spec must contain at least one of 'summary' (a grouped count/total sheet) "
            "or 'detail_sheets' (the underlying rows)."
        )

    if summary is not None:
        if not isinstance(summary, dict):
            raise ValueError("spec.summary must be an object.")
        if not summary.get("group_by"):
            raise ValueError("spec.summary.group_by is required — name the column to group by.")
        metrics = summary.get("metrics") or [{"op": "count"}]
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("spec.summary.metrics must be a non-empty list, e.g. [{\"op\": \"count\"}].")
        for metric in metrics:
            if not isinstance(metric, dict) or metric.get("op") not in _VALID_METRIC_OPS:
                raise ValueError(
                    f"Each metric needs an 'op' from {sorted(_VALID_METRIC_OPS)}; got {metric!r}."
                )
            if metric["op"] not in ("count",) and not metric.get("column"):
                raise ValueError(
                    f"Metric op '{metric['op']}' requires a 'column' (only 'count' works without one)."
                )
        summary["metrics"] = metrics

    if detail is not None:
        if not isinstance(detail, dict):
            raise ValueError("spec.detail_sheets must be an object.")
        columns = detail.get("columns")
        if columns is not None and (not isinstance(columns, list) or not columns):
            raise ValueError("spec.detail_sheets.columns must be a non-empty list, or omitted to include every column.")

    return spec


# ── rendering ────────────────────────────────────────────────────────────────


def _write_summary_sheet(
    workbook: Workbook,
    rows: list[dict[str, Any]],
    summary_spec: dict[str, Any],
    groups: dict[str, list[dict[str, Any]]],
    sheet_names: dict[str, str],
    title: str,
) -> list[dict[str, Any]]:
    group_by = summary_spec["group_by"]
    metrics = summary_spec["metrics"]

    worksheet = workbook.create_sheet(title="Summary")
    worksheet.column_dimensions["A"].width = 42
    for index in range(len(metrics)):
        worksheet.column_dimensions[get_column_letter(2 + index)].width = 16
    worksheet.column_dimensions[get_column_letter(2 + len(metrics))].width = 34

    title_cell = _cell(worksheet, title)
    title_cell.font = _TITLE_FONT
    worksheet.append([title_cell])
    worksheet.append([_cell(worksheet, f"Generated {datetime.now():%Y-%m-%d %H:%M}")])
    worksheet.append([_cell(worksheet, f"{len(rows):,} rows across {len(groups):,} groups")])
    worksheet.append([])

    header = [_cell(worksheet, group_by, bold=True)]
    header += [_cell(worksheet, _metric_label(m), bold=True) for m in metrics]
    header.append(_cell(worksheet, "Detail sheet", bold=True))
    worksheet.append(header)

    summary_rows: list[dict[str, Any]] = []
    for label, group_rows in groups.items():
        record: dict[str, Any] = {"name": label}
        for metric in metrics:
            record[_metric_label(metric)] = _compute_metric(metric, group_rows)
        record["sheet"] = sheet_names.get(label, "")
        summary_rows.append(record)

    sort = summary_spec.get("sort") or {"by": "count", "dir": "desc"}
    sort_by = sort.get("by") or "count"
    # Accept the friendly aliases the model is most likely to use ("count",
    # "name") alongside a literal metric label like "sum of total_amount".
    if sort_by == "name":
        key_field = "name"
    elif sort_by in {_metric_label(m) for m in metrics}:
        key_field = sort_by
    else:
        key_field = next(
            (_metric_label(m) for m in metrics if m["op"] == sort_by or m.get("column") == sort_by),
            _metric_label(metrics[0]),
        )
    reverse = str(sort.get("dir", "desc")).lower() == "desc"
    summary_rows.sort(key=lambda r: _sort_key(r.get(key_field)), reverse=reverse)

    for record in summary_rows:
        line = [_cell(worksheet, record["name"])]
        line += [_cell(worksheet, record[_metric_label(m)]) for m in metrics]
        line.append(_cell(worksheet, record["sheet"]))
        worksheet.append(line)

    return summary_rows


def _write_detail_sheets(
    workbook: Workbook,
    detail_spec: dict[str, Any],
    groups: dict[str, list[dict[str, Any]]],
    sheet_names: dict[str, str],
    notes: list[str],
) -> int:
    columns = detail_spec.get("columns")
    sort = detail_spec.get("sort")
    written = 0

    for label, group_rows in groups.items():
        sheet_name = sheet_names.get(label)
        if not sheet_name:  # past the sheet cap — already disclosed in `notes`
            continue
        worksheet = workbook.create_sheet(title=sheet_name)
        sheet_columns = columns or _derive_columns(group_rows)
        for index in range(len(sheet_columns)):
            worksheet.column_dimensions[get_column_letter(1 + index)].width = 22
        worksheet.freeze_panes = "A2"
        worksheet.append([_cell(worksheet, c, bold=True) for c in sheet_columns])

        ordered = _sorted_rows(group_rows, sort)
        if len(ordered) > _MAX_ROWS_PER_SHEET:
            notes.append(
                f"Sheet '{sheet_name}' holds {_MAX_ROWS_PER_SHEET:,} of {len(ordered):,} rows "
                "— Excel's per-sheet row limit."
            )
            ordered = ordered[:_MAX_ROWS_PER_SHEET]
        for row in ordered:
            worksheet.append([_cell(worksheet, row.get(c)) for c in sheet_columns])
        written += 1

    return written


def build_workbook(rows: list[dict[str, Any]], spec: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Render `rows` into an .xlsx per `spec`. Returns (xlsx_bytes, meta).

    `meta` is what goes back to the model, so it is deliberately small: totals,
    a capped summary, and `notes` — every limit that was actually applied,
    stated explicitly. Nothing is ever truncated silently.
    """
    validate_spec(spec)
    if not rows:
        raise ValueError("There are no rows to report on — fetch the data first, then build the report.")

    title = str(spec.get("title") or "Report").strip() or "Report"
    summary_spec = spec.get("summary")
    detail_spec = spec.get("detail_sheets")
    notes: list[str] = []

    # One grouping drives BOTH the summary and the detail tabs, so a group's
    # count on the Summary sheet can never disagree with its own detail sheet.
    group_column = None
    if summary_spec:
        group_column = summary_spec["group_by"]
    elif detail_spec and detail_spec.get("split_by"):
        group_column = detail_spec["split_by"]
    if detail_spec and detail_spec.get("split_by") and summary_spec:
        if detail_spec["split_by"] != summary_spec["group_by"]:
            group_column = detail_spec["split_by"]
            notes.append(
                f"Summary is grouped by '{summary_spec['group_by']}' but detail sheets are split "
                f"by '{detail_spec['split_by']}'; both use the same underlying rows."
            )

    groups: dict[str, list[dict[str, Any]]] = {}
    if group_column:
        for row in rows:
            groups.setdefault(_group_label(row, group_column), []).append(row)
    else:
        groups = {"Data": list(rows)}

    # Which groups get their own detail tab — biggest first, so a cap drops the
    # smallest groups rather than an arbitrary alphabetical tail.
    sheet_names: dict[str, str] = {}
    if detail_spec:
        splitting = bool(detail_spec.get("split_by")) or group_column is None
        max_sheets = int(detail_spec.get("max_detail_sheets") or _DEFAULT_MAX_DETAIL_SHEETS)
        if not splitting:
            sheet_names = {}  # a single combined sheet, written separately below
        else:
            ranked = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)
            used: set[str] = {"summary"}
            for label, _ in ranked[:max_sheets]:
                sheet_names[label] = _unique_sheet_name(_sanitize_sheet_name(label), used)
            if len(ranked) > max_sheets:
                notes.append(
                    f"{len(ranked):,} groups matched but only the {max_sheets} largest got their own "
                    f"detail sheet. The Summary sheet still lists all {len(ranked):,} groups with "
                    "their full counts."
                )
            renamed = {k: v for k, v in sheet_names.items() if v != k}
            if renamed:
                notes.append(
                    f"{len(renamed)} sheet name(s) were shortened to fit Excel's 31-character limit "
                    "— the Summary sheet's 'Detail sheet' column maps each group to its tab."
                )

    workbook = Workbook(write_only=True)

    summary_rows: list[dict[str, Any]] = []
    if summary_spec:
        summary_rows = _write_summary_sheet(workbook, rows, summary_spec, groups, sheet_names, title)

    sheet_count = 1 if summary_spec else 0
    if detail_spec:
        if sheet_names:
            sheet_count += _write_detail_sheets(workbook, detail_spec, groups, sheet_names, notes)
        else:
            # No split requested: every row on one sheet.
            combined = {"Data": list(rows)}
            sheet_count += _write_detail_sheets(
                workbook, detail_spec, combined, {"Data": "Data"}, notes
            )

    stream = BytesIO()
    workbook.save(stream)
    payload = stream.getvalue()

    capped_summary = summary_rows[:_MAX_SUMMARY_ROWS_TO_CALLER]
    if len(summary_rows) > _MAX_SUMMARY_ROWS_TO_CALLER:
        notes.append(
            f"Showing the top {_MAX_SUMMARY_ROWS_TO_CALLER} of {len(summary_rows):,} groups here; "
            "the workbook's Summary sheet contains every group."
        )

    meta = {
        "title": title,
        "sheet_count": sheet_count,
        "row_count": len(rows),
        "group_count": len(groups) if group_column else None,
        "summary": capped_summary,
        "notes": notes,
        "bytes": len(payload),
    }
    logger.info(
        "report_built",
        title=title, rows=len(rows), sheets=sheet_count,
        groups=len(groups) if group_column else None, size=len(payload),
    )
    return payload, meta


def suggest_filename(title: str, when: datetime | None = None) -> str:
    """A safe, readable download filename derived from the report title."""
    stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H%M")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(title or "report")).strip("-").lower() or "report"
    return f"{slug[:60]}_{stamp}.xlsx"
