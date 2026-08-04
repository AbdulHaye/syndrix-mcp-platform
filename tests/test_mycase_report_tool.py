"""Tests for the agent-layer half of Excel reporting in app/services/mycase_agent.py.

`build_report` is deliberately NOT a MyCase MCP tool — it is intercepted inside the
agent loop so it can read rows the turn already fetched. That interception, the
dataset resolution, and the refuse-on-partial-data guard are what these cover;
workbook rendering itself is covered by test_report_builder.py.
"""
from __future__ import annotations

import pytest

from app.services.mycase_agent import (
    _auto_report_spec,
    _retarget_table_pointers,
    _run_build_report,
    _slim_reported_steps,
)


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


def _bucket(n: int = 5) -> dict:
    return {
        i: {
            "id": i,
            "case_number": f"C-{i}",
            "assigned_attorney": "LANA JOSEPH" if i % 2 else "EDDY LAGUERRE",
        }
        for i in range(n)
    }


_SPEC = {
    "title": "Active cases by attorney",
    "summary": {"group_by": "assigned_attorney", "metrics": [{"op": "count"}]},
    "detail_sheets": {"split_by": "assigned_attorney", "columns": ["case_number"]},
}


@pytest.mark.anyio
async def test_builds_a_report_from_rows_already_fetched_this_turn():
    result = await _run_build_report(
        {"spec": _SPEC, "dataset": "cases"},
        {"cases": _bucket()},
        None,
        [{"tool": "aggregate_cases", "args": {}, "result": {"success": True, "total_cases": 5}}],
        "bd_team",
    )
    assert result["success"] is True
    assert result["dataset"] == "cases"
    assert result["row_count"] == 5
    assert result["sheet_count"] == 3  # Summary + 2 attorneys
    assert result["download_url"] == f"/agent/mycase/reports/{result['report_id']}"
    assert result["filename"].endswith(".xlsx")
    # The summary that goes back to the model must be small and already computed.
    assert sorted(r["name"] for r in result["summary"]) == ["EDDY LAGUERRE", "LANA JOSEPH"]
    assert "items" not in result, "the whole point is that rows do NOT go back through the model"


@pytest.mark.anyio
async def test_dataset_defaults_to_the_most_recently_fetched_resource():
    result = await _run_build_report(
        {"spec": {"summary": {"group_by": "assigned_attorney", "metrics": [{"op": "count"}]}}},
        {"invoices": {1: {"id": 1}}, "cases": _bucket(2)},
        "cases",  # last_resource
        [],
        "bd_team",
    )
    assert result["success"] is True
    assert result["dataset"] == "cases"


@pytest.mark.anyio
async def test_refuses_when_the_source_fetch_was_limited():
    """A report built off a `limit`ed fetch would under-report while looking
    complete — the silent-truncation failure mode this codebase keeps hitting."""
    result = await _run_build_report(
        {"spec": _SPEC, "dataset": "cases"},
        {"cases": _bucket(5)},
        None,
        [{"tool": "aggregate_cases", "args": {"limit": 5},
          "result": {"success": True, "total_cases": 4312, "limited": True}}],
        "bd_team",
    )
    assert result["success"] is False
    assert "limit" in result["error"]
    assert "WITHOUT `limit`" in result["error"], "the error must say exactly how to fix it"


@pytest.mark.anyio
async def test_scan_cap_truncation_is_reported_but_not_fatal():
    """A MyCase-side scan cap is not our choice to make, so it degrades to a
    disclosed note rather than refusing the whole report."""
    result = await _run_build_report(
        {"spec": _SPEC, "dataset": "cases"},
        {"cases": _bucket(5)},
        None,
        [{"tool": "aggregate_cases", "args": {}, "result": {"success": True, "truncated": True}}],
        "bd_team",
    )
    assert result["success"] is True
    assert any("scan cap" in note for note in result["notes"])


@pytest.mark.anyio
async def test_unknown_dataset_lists_what_is_actually_available():
    result = await _run_build_report(
        {"spec": _SPEC, "dataset": "payments"},
        {"cases": _bucket(2)},
        "cases",
        [],
        "bd_team",
    )
    assert result["success"] is False
    assert "cases" in result["error"], "tell the model which datasets it can actually use"


@pytest.mark.anyio
async def test_nothing_fetched_yet_is_an_actionable_error():
    result = await _run_build_report({"spec": _SPEC}, {}, None, [], "bd_team")
    assert result["success"] is False
    assert "nothing fetched yet" in result["error"] or "nothing to report on" in result["error"]


@pytest.mark.anyio
async def test_a_bad_spec_returns_the_validation_reason_verbatim():
    result = await _run_build_report(
        {"spec": {"summary": {"group_by": "x", "metrics": [{"op": "sum"}]}}, "dataset": "cases"},
        {"cases": _bucket(2)},
        None,
        [],
        "bd_team",
    )
    assert result["success"] is False
    assert "requires a 'column'" in result["error"]


@pytest.mark.anyio
async def test_report_is_retrievable_from_the_store_for_download():
    from app.services.report_store import report_store

    result = await _run_build_report(
        {"spec": _SPEC, "dataset": "cases"}, {"cases": _bucket()}, None, [], "bd_team",
    )
    stored = await report_store.get(result["report_id"])
    assert stored is not None
    assert stored["team"] == "bd_team", "team scoping is what the download endpoint checks"
    assert stored["payload"][:2] == b"PK", "an .xlsx is a zip container"


@pytest.mark.anyio
async def test_a_stored_report_survives_a_process_restart():
    """Regression for a real failure: with Redis unavailable (this dev box runs
    Redis 5, which redis-py 8's RESP3 handshake can't talk to), reports were held
    in process memory and vanished the next time uvicorn --reload restarted — a
    successfully-built report 404'd on download."""
    from app.services.report_store import ReportStore, report_store

    report_id = await report_store.put(b"PK-fake", "r.xlsx", "bd_team")
    fresh = ReportStore()  # stands in for a restarted process: no shared memory
    recovered = await fresh.get(report_id)
    assert recovered is not None, "a built report must still be downloadable after a restart"
    assert recovered["payload"] == b"PK-fake"


@pytest.mark.anyio
@pytest.mark.parametrize("bad_id", ["../../../etc/passwd", "a/b", "..", "x" * 100, "", "a b"])
async def test_report_ids_cannot_walk_the_filesystem(bad_id: str):
    """The id arrives straight off a URL path and is used to build a filename."""
    from app.services.report_store import report_store

    assert await report_store.get(bad_id) is None


# ── payload slimming ─────────────────────────────────────────────────────────


def test_slimming_only_touches_resources_that_were_reported():
    steps = [
        {"tool": "aggregate_cases", "args": {}, "result": {"success": True, "items": [{"id": 1}, {"id": 2}]}},
        {"tool": "get_invoices", "args": {}, "result": {"success": True, "items": [{"id": 9}]}},
    ]
    out = _slim_reported_steps(steps, {"cases"})
    assert "items" not in out[0]["result"], "reported rows are dropped from the wire payload"
    assert out[0]["result"]["items_omitted"] == 2
    assert out[1]["result"]["items"] == [{"id": 9}], "unreported resources are untouched"


def test_slimming_is_a_no_op_when_no_report_was_built():
    steps = [{"tool": "aggregate_cases", "args": {}, "result": {"items": [{"id": 1}]}}]
    assert _slim_reported_steps(steps, set()) is steps


# ── automatic workbook for grouped results ───────────────────────────────────

_GROUPS = [{"name": "LANA JOSEPH", "count": 1147}, {"name": "EDDY LAGUERRE", "count": 79}]
_BIG = [{"id": i, "balance_due": 10.0} for i in range(300)]
# A workbook is only built when the USER asked to see the data broken down.
_ASKED = "show invoices by assigned attorney"


def _grouped(items=None, groups=None, **over):
    return {
        "success": True, "group_by_field": "assigned_attorney",
        "groups": _GROUPS if groups is None else groups,
        "items": _BIG if items is None else items, **over,
    }


def test_a_large_grouped_result_becomes_a_per_group_workbook():
    """Reported: "the data it is giving is still 1 full excel sheet". The chat can
    only render ONE flat table per resource, so a breakdown has to be a workbook —
    and the model only volunteered build_report when the question said "excel"."""
    rows = [{"id": i, "balance_due": 10.0, "assigned_attorney": "LANA JOSEPH"} for i in range(300)]
    spec = _auto_report_spec(
        "aggregate_invoices", {"group_by": "assigned_attorney"}, _grouped(items=rows), set(), _ASKED
    )
    assert spec is not None
    assert spec["dataset"] == "invoices"
    assert spec["spec"]["summary"]["group_by"] == "assigned_attorney"
    assert spec["spec"]["detail_sheets"]["split_by"] == "assigned_attorney"
    # Money columns get totalled per group, not just counted.
    assert {"op": "sum", "column": "balance_due"} in spec["spec"]["summary"]["metrics"]


def test_splitting_falls_back_to_group_name_when_rows_lack_the_column():
    """Invoice rows have no attorney field of their own. Splitting on a column the
    rows don't carry silently collapses every group into one "(none)" sheet —
    confirmed live: 8 real attorney groups produced a 2-sheet workbook. Every
    aggregate_* row sets `group_name`, so that is the safe fallback."""
    rows = [{"id": i, "balance_due": 10.0} for i in range(300)]  # no attorney column
    spec = _auto_report_spec(
        "aggregate_invoices", {"group_by": "assigned_attorney"}, _grouped(items=rows), set(), _ASKED
    )
    assert spec["spec"]["detail_sheets"]["split_by"] == "group_name"
    assert spec["spec"]["summary"]["group_by"] == "group_name"
    assert "assigned attorney" in spec["spec"]["title"], "title still names the real field"


def test_a_small_grouped_result_stays_a_plain_table():
    """A workbook for a handful of rows is noise. The threshold is deliberately
    low (25): "open cases with an SOL date in the next 30 days, grouped by
    attorney" returned only 83 rows across 4 attorneys, and per-attorney sheets
    were exactly what was wanted — an earlier 200-row threshold blocked it."""
    small = [{"id": i} for i in range(10)]
    assert _auto_report_spec(
        "aggregate_invoices", {"group_by": "assigned_attorney"}, _grouped(items=small), set(), _ASKED
    ) is None


def test_a_modest_grouped_result_still_gets_a_workbook():
    """83 rows across 4 attorneys — the reported case that used to be skipped."""
    rows = [{"id": i, "assigned_attorney": "LANA JOSEPH"} for i in range(83)]
    groups = [{"name": n, "count": 1} for n in ("LANA", "UNASSIGNED", "ECLEYNNE", "EDDY")]
    spec = _auto_report_spec(
        "aggregate_cases", {"group_by": "assigned_attorney"}, _grouped(items=rows, groups=groups),
        set(), "open cases with SOL date within the next 30 days, grouped by attorney",
    )
    assert spec is not None


def test_no_workbook_when_grouping_was_only_an_intermediate_step():
    """"Find clients who have an overdue invoice but no upcoming appointment" got a
    101-sheet, 1,624-group invoice workbook nobody asked for. Grouping done on the
    way to some other answer must not sprout a workbook."""
    assert _auto_report_spec(
        "aggregate_invoices", {"group_by": "client_name"}, _grouped(), set(),
        "find clients who have an overdue invoice but no upcoming appointment",
    ) is None


def test_no_workbook_when_there_are_too_many_groups_to_be_readable():
    """1,624 groups capped to 100 sheets is a mess, not a report — the `groups`
    summary in the reply is the better answer at that scale."""
    many = [{"name": str(i), "count": 1} for i in range(1624)]
    assert _auto_report_spec(
        "aggregate_invoices", {"group_by": "client_name"}, _grouped(groups=many), set(),
        "show me invoices grouped by client",
    ) is None


def test_no_workbook_without_an_explicit_group_by():
    """group_by_field is populated even when nobody asked to group (it defaults to
    practice_area), so the ARGUMENT is what counts — otherwise every large
    aggregate result would silently sprout a workbook."""
    assert _auto_report_spec("aggregate_cases", {}, _grouped(), set(), _ASKED) is None


def test_no_workbook_for_a_single_group():
    one = [{"name": "OPEN", "count": 300}]
    assert _auto_report_spec(
        "aggregate_cases", {"group_by": "status"}, _grouped(groups=one), set(), _ASKED
    ) is None


def test_no_second_workbook_if_one_already_exists_for_that_resource():
    assert _auto_report_spec(
        "aggregate_invoices", {"group_by": "assigned_attorney"}, _grouped(), {"invoices"}, _ASKED
    ) is None


def test_no_workbook_for_a_failed_or_non_aggregate_call():
    assert _auto_report_spec(
        "aggregate_cases", {"group_by": "status"}, {"success": False}, set(), _ASKED
    ) is None
    assert _auto_report_spec("get_cases", {"group_by": "status"}, _grouped(), set(), _ASKED) is None


# ── "see the table below" when there is no table below ───────────────────────

_NO_ROWS = [{"tool": "aggregate_invoices", "result": {"success": True, "items_omitted": 6381}}]
_HAS_ROWS = [{"tool": "aggregate_cases", "result": {"success": True, "items": [{"id": 1}]}}]


def test_a_table_pointer_is_retargeted_when_the_rows_moved_into_a_workbook():
    """Observed live even with the prompt explicitly forbidding it: the reply said
    "see the table below" after the rows had been moved into the workbook, sending
    the user looking for something that isn't on screen."""
    out = _retarget_table_pointers(
        "Found 6,381 invoices grouped by assigned attorney — see the table below. "
        "Top groups: LANA JOSEPH (2,101).",
        _NO_ROWS,
    )
    assert "table below" not in out
    assert "workbook" in out
    assert "LANA JOSEPH (2,101)" in out, "the breakdown itself must survive untouched"


def test_a_table_pointer_is_left_alone_when_a_table_really_is_rendered():
    text = "Found 5 Immigration cases — see the table below."
    assert _retarget_table_pointers(text, _HAS_ROWS) == text


def test_a_reply_with_no_table_pointer_is_untouched():
    text = "Found 23 cases with a missing SOL date."
    assert _retarget_table_pointers(text, _NO_ROWS) == text


def test_slimming_keeps_the_non_row_fields_a_reply_may_cite():
    steps = [{"tool": "aggregate_cases", "args": {},
              "result": {"success": True, "total_cases": 4312, "groups": [{"name": "a", "count": 2}],
                         "items": [{"id": 1}]}}]
    result = _slim_reported_steps(steps, {"cases"})[0]["result"]
    assert result["total_cases"] == 4312
    assert result["groups"] == [{"name": "a", "count": 2}]
