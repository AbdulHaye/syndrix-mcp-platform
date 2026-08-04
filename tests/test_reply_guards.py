"""Tests for the deterministic bad-reply guards in app/services/mycase_agent.py.

These catch replies that are structurally wrong regardless of the data behind
them — the class of failure where a weaker model returns something that is not an
answer at all, and every existing heuristic reads it as clean prose.
"""
from __future__ import annotations

import pytest

from app.services.mycase_agent import (
    _has_text_tool_call,
    _is_bare_tool_name,
    _unknown_tool_args,
)

_TOOLS = {"aggregate_clients", "aggregate_cases", "aggregate_invoices", "get_cases", "build_report"}


@pytest.mark.parametrize(
    "reply",
    [
        "aggregate_clients",              # the exact observed failure
        "  `aggregate_clients`. ",        # fenced/punctuated variants
        "**aggregate_cases**",
        "aggregate_cases get_cases",      # several names, still no answer
        "build_report.",
    ],
)
def test_a_reply_that_is_only_tool_names_is_rejected(reply: str):
    """Confirmed live: asked "show me clients who have more than one case", the
    model replied with the single word `aggregate_clients` and made no tool call.
    Nothing caught it — _has_text_tool_call needs a `(`/`{`, and the garbage
    heuristic judges noise ratios, so a lone identifier looks like clean prose."""
    assert _is_bare_tool_name(reply, _TOOLS) is True


@pytest.mark.parametrize(
    "reply",
    [
        "Found 5 cases — see the table below.",
        "I used aggregate_cases to find 12 clients with more than one case.",
        "There are 2,380 active cases across 8 attorneys.",
        "",
        "   ",
        "Here are your cases.",
        # A genuine long answer that happens to name tools must never be dropped.
        "I called aggregate_cases and then build_report to produce the workbook.",
    ],
)
def test_real_answers_are_not_rejected(reply: str):
    assert _is_bare_tool_name(reply, _TOOLS) is False


def test_an_unknown_identifier_is_not_treated_as_a_tool_name():
    """Only names the model actually has access to count — otherwise any
    one-word reply would be discarded."""
    assert _is_bare_tool_name("unknown_thing", _TOOLS) is False


def test_bare_call_syntax_is_still_caught_by_the_other_guard():
    """Regression guard for the sibling check: `tool(args)` with no JSON braces."""
    assert _has_text_tool_call("aggregate_cases(status='open')", _TOOLS) is True
    assert _has_text_tool_call("The client called twice about it.", _TOOLS) is False


@pytest.mark.parametrize(
    "reply",
    [
        # The exact observed reply — two stray Georgian characters between the
        # tool name and the '{' defeated both regexes, and 2 non-ASCII chars in
        # 62 stayed under the garbage threshold, so this shipped to the user.
        'aggregate_invoicesმწ{"group_by": "client_name", "paid": false}',
        'aggregate_cases  {"status": "open"}',
        "get_cases::{'a':1}",
        "build_report →(spec)",
    ],
)
def test_a_tool_name_glued_to_arguments_is_caught_however_it_is_mangled(reply: str):
    assert _has_text_tool_call(reply, _TOOLS) is True


@pytest.mark.parametrize(
    "reply",
    [
        "Found 5 cases — see the table below.",
        "I ran aggregate_cases and found 12 clients with more than one case.",
        "The breakdown by attorney is shown below (LANA JOSEPH: 1,589).",
    ],
)
def test_prose_mentioning_a_tool_is_not_mistaken_for_a_call(reply: str):
    assert _has_text_tool_call(reply, _TOOLS) is False


# ── unknown tool arguments ───────────────────────────────────────────────────

_PROPS = {"status": {}, "group_by": {}, "paid": {}, "limit": {}}


@pytest.mark.parametrize(
    "args, expected",
    [
        # build_report's parameter passed to aggregate_cases: with it silently
        # dropped the call applied NO filter, returned all 7,247 cases, and the
        # reply described them as "open cases with unpaid balances".
        ({"spec": {"summary": {}}, "status": "open"}, ["spec"]),
        # Silently ignored, so the requested ordering never happened.
        ({"sort_order": "desc", "paid": False}, ["sort_order"]),
        ({"bogus": 1, "alsoBogus": 2, "paid": True}, ["alsoBogus", "bogus"]),
    ],
)
def test_arguments_the_tool_does_not_accept_are_reported(args, expected):
    assert _unknown_tool_args(args, _PROPS) == expected


def test_valid_arguments_are_not_flagged():
    assert _unknown_tool_args({"status": "open", "paid": False, "limit": 5}, _PROPS) == []


def test_a_tool_that_declares_no_schema_is_left_alone():
    """Otherwise every argument to such a tool would be rejected outright."""
    assert _unknown_tool_args({"anything": 1}, {}) == []


# ── grouped answers must contain the breakdown ───────────────────────────────

from app.services.mycase_agent import _ensure_group_breakdown  # noqa: E402

_GROUPED_STEP = [{
    "tool": "aggregate_cases",
    "args": {"group_by": "assigned_attorney", "status": "open"},
    "result": {"success": True, "groups": [
        {"name": "LANA JOSEPH", "count": 1589},
        {"name": "(unassigned)", "count": 640},
        {"name": "EDDY LAGUERRE", "count": 122},
    ]},
}]


def test_a_grouped_answer_missing_its_breakdown_gets_one_appended():
    """Observed: the same question listed every attorney on one run and on the next
    said only "see the breakdown above" — pointing at nothing, with the rows now in
    a workbook so the numbers appeared nowhere on screen."""
    out = _ensure_group_breakdown(
        "Found 2,380 open cases — see the breakdown above.", _GROUPED_STEP
    )
    assert "LANA JOSEPH" in out and "1,589" in out
    assert "EDDY LAGUERRE" in out


def test_a_reply_that_already_lists_the_groups_is_left_alone():
    text = "Breakdown: LANA JOSEPH 1,589 cases; (unassigned) 640 cases; EDDY LAGUERRE 122."
    assert _ensure_group_breakdown(text, _GROUPED_STEP) == text


def test_nothing_is_appended_when_the_call_was_not_grouped():
    steps = [{"tool": "aggregate_cases", "args": {"status": "open"},
              "result": {"success": True, "groups": [{"name": "Immigration", "count": 5}]}}]
    text = "Found 5 cases."
    assert _ensure_group_breakdown(text, steps) == text


def test_money_totals_are_included_when_the_tool_returned_them():
    steps = [{"tool": "aggregate_invoices", "args": {"group_by": "client_name"},
              "result": {"success": True, "groups": [
                  {"name": "Ada Lovelace", "count": 2, "total_balance_due": 17500.0}]}}]
    out = _ensure_group_breakdown("Found 2 unpaid invoices.", steps)
    assert "$17,500.00" in out


# ── fabricated group names ───────────────────────────────────────────────────

from app.services.mycase_agent import _fabricated_group_names  # noqa: E402

_PRACTICE_GROUPED = [{
    "tool": "aggregate_cases",
    "args": {"practice_area": "Criminal"},
    "result": {"success": True, "group_by_field": "practice_area", "groups": [
        {"name": "Criminal", "count": 187}, {"name": "MERCY/CRIMINAL", "count": 1},
    ]},
}]


def test_a_relabelled_breakdown_with_an_invented_name_is_caught():
    """Caught live: a group_by="assigned_attorney" call failed with a MyCase 500,
    so the surviving result was grouped by PRACTICE AREA — and the reply presented
    it as "Breakdown by Assigned Attorney: LANA JOSEPH 187, JESSICA PRIVITERA 1".
    JESSICA PRIVITERA exists nowhere in the account."""
    reply = ("Found 188 Criminal cases with assigned attorneys.\n\n"
             "### Breakdown by Assigned Attorney\n"
             "- **LANA JOSEPH**: 187 cases\n"
             "- **JESSICA PRIVITERA**: 1 case")
    fabricated = _fabricated_group_names(reply, _PRACTICE_GROUPED)
    assert "LANA JOSEPH" in fabricated
    assert "JESSICA PRIVITERA" in fabricated


def test_a_breakdown_using_the_real_group_names_passes():
    reply = ("Found 188 Criminal cases.\n"
             "- **Criminal**: 187 cases\n"
             "- **MERCY/CRIMINAL**: 1 case")
    assert _fabricated_group_names(reply, _PRACTICE_GROUPED) == []


def test_a_label_that_embeds_a_real_group_name_is_tolerated():
    reply = "- Criminal cases: 187\n- MERCY/CRIMINAL: 1"
    assert _fabricated_group_names(reply, _PRACTICE_GROUPED) == []


def test_ordinary_prose_and_non_grouped_turns_are_untouched():
    assert _fabricated_group_names("Found 188 cases — see the table below.", _PRACTICE_GROUPED) == []
    ungrouped = [{"tool": "get_cases", "args": {}, "result": {"success": True, "items": [{"id": 1}]}}]
    assert _fabricated_group_names("- **Anything**: 5", ungrouped) == []
