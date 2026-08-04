"""Tests for min_group_size / max_group_size — the HAVING primitive.

Why this exists: "clients who have more than one case" filters on how big each
GROUP is, not on any field of a single record. There was no way to express that,
so the agent grouped everything and tried to eyeball the large groups — which
returned all 2,380 cases and answered the question wrong (reported by the user).

These patch the HTTP layer rather than touching the live account, so the counting
logic is proven deterministically and in milliseconds.
"""
from __future__ import annotations

import unittest.mock as mock

import pytest

from app.services.mycase_rest import MyCaseREST


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


def _case(case_id: int, client: str, stage: str = "OPEN") -> dict:
    first, _, last = client.partition(" ")
    return {
        "id": case_id,
        "case_number": f"C-{case_id}",
        "name": f"Matter {case_id}",
        "case_stage": stage,
        "status": "open",
        "clients": [{"id": hash(client) % 1000, "first_name": first, "last_name": last}],
        "staff": [],
        "custom_field_values": [],
    }


# Ada has 3 cases, Bo has 2, Cy has 1 — so "more than one" must keep Ada+Bo only.
_CASES = [
    _case(1, "Ada Lovelace"), _case(2, "Ada Lovelace"), _case(3, "Ada Lovelace"),
    _case(4, "Bo Peep"), _case(5, "Bo Peep"),
    _case(6, "Cy Young"),
]


def _rest_with_cases(cases: list[dict]) -> MyCaseREST:
    rest = MyCaseREST()
    rest._custom_field_maps = mock.AsyncMock(return_value=({}, {}))  # type: ignore[method-assign]
    rest._staff_name_map = mock.AsyncMock(return_value={})  # type: ignore[method-assign]
    rest._case_scan_cap = mock.AsyncMock(return_value=10_000)  # type: ignore[method-assign]
    rest.get_cases = mock.AsyncMock(  # type: ignore[method-assign]
        return_value={"items": cases, "item_count": len(cases), "next_page_token": None}
    )
    return rest


@pytest.mark.anyio
async def test_clients_with_more_than_one_case():
    """The exact reported question."""
    rest = _rest_with_cases(_CASES)
    result = await rest.aggregate_cases(group_by="client_name", min_group_size=2)

    assert {g["name"] for g in result["groups"]} == {"Ada Lovelace", "Bo Peep"}
    assert {g["name"]: g["count"] for g in result["groups"]} == {"Ada Lovelace": 3, "Bo Peep": 2}
    assert result["total_groups"] == 2
    # Rows are narrowed too, so items/groups/totals all describe the same set —
    # the single-case client must not linger in the table.
    assert result["total_cases"] == 5
    assert {r["client_name"] for r in result["items"]} == {"Ada Lovelace", "Bo Peep"}


@pytest.mark.anyio
async def test_the_filter_is_disclosed_so_a_small_result_is_not_mistaken_for_a_small_dataset():
    rest = _rest_with_cases(_CASES)
    result = await rest.aggregate_cases(group_by="client_name", min_group_size=2)
    assert result["group_size_filter"] == {"min": 2, "max": None}
    assert result["groups_before_size_filter"] == 3, "3 clients existed before filtering"


@pytest.mark.anyio
async def test_max_group_size_finds_the_singletons():
    rest = _rest_with_cases(_CASES)
    result = await rest.aggregate_cases(group_by="client_name", max_group_size=1)
    assert [g["name"] for g in result["groups"]] == ["Cy Young"]
    assert result["total_cases"] == 1


@pytest.mark.anyio
async def test_min_and_max_together_select_an_exact_band():
    rest = _rest_with_cases(_CASES)
    result = await rest.aggregate_cases(group_by="client_name", min_group_size=2, max_group_size=2)
    assert [g["name"] for g in result["groups"]] == ["Bo Peep"]


@pytest.mark.anyio
async def test_no_group_meets_the_threshold_returns_an_honest_empty_result():
    rest = _rest_with_cases(_CASES)
    result = await rest.aggregate_cases(group_by="client_name", min_group_size=99)
    assert result["success"] is True
    assert result["groups"] == []
    assert result["total_cases"] == 0
    assert result["groups_before_size_filter"] == 3, "still says how many groups there really were"


@pytest.mark.anyio
async def test_without_the_filter_nothing_changes():
    """Regression guard: the HAVING clause must be inert when unused."""
    rest = _rest_with_cases(_CASES)
    result = await rest.aggregate_cases(group_by="client_name")
    assert result["total_cases"] == 6
    assert result["total_groups"] == 3
    assert "group_size_filter" not in result
    assert "groups_before_size_filter" not in result


@pytest.mark.anyio
async def test_group_size_filter_composes_with_ordinary_row_filters():
    """The row filter must run FIRST, so counts reflect only matching rows: with
    stage=CLOSED excluded, Ada drops to 2 cases and Bo to 1 — so Bo falls out."""
    cases = _CASES + [_case(7, "Bo Peep", stage="CLOSED")]
    cases[2]["case_stage"] = "CLOSED"  # one of Ada's three
    cases[4]["case_stage"] = "CLOSED"  # one of Bo's two
    rest = _rest_with_cases(cases)
    result = await rest.aggregate_cases(
        group_by="client_name", exclude_case_stages=["CLOSED"], min_group_size=2
    )
    assert [g["name"] for g in result["groups"]] == ["Ada Lovelace"]
    assert result["groups"][0]["count"] == 2


# ── invoices ─────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_invoices_support_the_same_primitive():
    """'Clients with more than one unpaid invoice'."""
    rest = MyCaseREST()
    invoices = [
        {"id": 1, "case": {"id": 1}, "total_amount": "100.0", "paid_amount": "0", "status": "sent"},
        {"id": 2, "case": {"id": 1}, "total_amount": "200.0", "paid_amount": "0", "status": "sent"},
        {"id": 3, "case": {"id": 2}, "total_amount": "50.0", "paid_amount": "0", "status": "sent"},
    ]
    rest._invoice_scan_cap = mock.AsyncMock(return_value=10_000)  # type: ignore[method-assign]
    rest._case_scan_cap = mock.AsyncMock(return_value=10_000)  # type: ignore[method-assign]
    rest._staff_name_map = mock.AsyncMock(return_value={})  # type: ignore[method-assign]
    rest.get_invoices = mock.AsyncMock(  # type: ignore[method-assign]
        return_value={"items": invoices, "item_count": 3, "next_page_token": None}
    )
    rest.get_cases = mock.AsyncMock(  # type: ignore[method-assign]
        return_value={
            "items": [_case(1, "Ada Lovelace"), _case(2, "Cy Young")],
            "item_count": 2, "next_page_token": None,
        }
    )

    result = await rest.aggregate_invoices(paid=False, group_by="client_name", min_group_size=2)
    assert [g["name"] for g in result["groups"]] == ["Ada Lovelace"]
    assert result["groups"][0]["count"] == 2
    assert result["total_invoices"] == 2, "totals describe the filtered set"
    assert result["groups_before_size_filter"] == 2


# ── wrong-tool guard ─────────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["id", "uuid", "client_id", "ID"])
async def test_grouping_clients_by_their_own_id_is_rejected_with_the_right_alternative(field: str):
    """Observed twice live: asked for "clients who have more than one case", the
    model called aggregate_clients(group_by="id") — a field that is unique per
    client, so no group can ever exceed 1. Rejected BEFORE the slow, 504-prone
    /clients walk, with the correct call named in the error."""
    rest = MyCaseREST()
    rest._client_scan_cap = mock.AsyncMock(return_value=10)  # type: ignore[method-assign]
    rest.get_clients = mock.AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("must fail before touching /clients")
    )

    with pytest.raises(ValueError) as exc:
        await rest.aggregate_clients(group_by=field, min_group_size=2)

    message = str(exc.value)
    assert "aggregate_cases(group_by='client_name', min_group_size=2)" in message
    assert "cases question" in message


@pytest.mark.anyio
async def test_clients_can_still_be_grouped_by_a_real_shared_field():
    """The guard must not block a legitimate use — e.g. shared email addresses."""
    rest = MyCaseREST()
    clients = [
        {"id": 1, "email": "shared@example.com"},
        {"id": 2, "email": "shared@example.com"},
        {"id": 3, "email": "solo@example.com"},
    ]
    rest._client_scan_cap = mock.AsyncMock(return_value=10)  # type: ignore[method-assign]
    rest.get_clients = mock.AsyncMock(  # type: ignore[method-assign]
        return_value={"items": clients, "item_count": 3, "next_page_token": None}
    )
    result = await rest.aggregate_clients(group_by="email", min_group_size=2)
    assert [g["name"] for g in result["groups"]] == ["shared@example.com"]
    assert result["total_clients"] == 2
    assert result["groups_before_size_filter"] == 2


# ── contradictory filters ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_blank_check_and_a_date_range_on_the_same_field_is_rejected():
    """Confirmed live: asked for open cases with an SOL date in the next 30 days,
    the model sent custom_field_filters={"sol_date": ""} (field is EMPTY) together
    with sol_date_after/before (field is in a RANGE). Nothing satisfies both, so it
    returned zero rows — and the agent reported "no open cases" as fact when the
    real answer was 92."""
    rest = _rest_with_cases(_CASES)
    with pytest.raises(ValueError) as exc:
        await rest.aggregate_cases(
            status="open",
            custom_field_filters={"sol_date": ""},
            sol_date_after="2026-08-05",
            sol_date_before="2026-09-04",
        )
    assert "Contradictory filter" in str(exc.value)
    assert "sol_date_after/sol_date_before" in str(exc.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field, kwargs",
    [
        ("opened_date", {"opened_after": "2026-01-01"}),
        ("closed_date", {"closed_before": "2026-01-01"}),
        ("created_at", {"created_after": "2026-01-01"}),
    ],
)
async def test_the_same_contradiction_is_caught_on_every_range_field(field, kwargs):
    rest = _rest_with_cases(_CASES)
    with pytest.raises(ValueError, match="Contradictory filter"):
        await rest.aggregate_cases(custom_field_filters={field: ""}, **kwargs)


@pytest.mark.anyio
async def test_a_blank_check_alone_still_works():
    """The guard must not break "cases with a missing SOL date"."""
    rest = _rest_with_cases(_CASES)
    result = await rest.aggregate_cases(custom_field_filters={"sol_date": ""})
    assert result["success"] is True


@pytest.mark.anyio
async def test_a_range_alone_still_works():
    rest = _rest_with_cases(_CASES)
    result = await rest.aggregate_cases(sol_date_after="2026-08-05", sol_date_before="2026-09-04")
    assert result["success"] is True


@pytest.mark.anyio
async def test_a_non_empty_filter_plus_a_range_is_fine():
    """Only the blank-check convention conflicts — a real value does not."""
    rest = _rest_with_cases(_CASES)
    result = await rest.aggregate_cases(
        custom_field_filters={"practice_area": "Immigration"}, sol_date_after="2026-08-05"
    )
    assert result["success"] is True


# ── "has a value" vs "is blank" ──────────────────────────────────────────────


def _cases_with_and_without_attorney() -> list[dict]:
    """3 cases with a lead attorney, 2 without."""
    out = []
    for i in range(5):
        c = _case(i, "Ada Lovelace")
        c["staff"] = [{"id": 7, "lead_lawyer": True}] if i < 3 else []
        out.append(c)
    return out


def _rest_with_attorneys(cases: list[dict]) -> MyCaseREST:
    rest = _rest_with_cases(cases)
    rest._staff_name_map = mock.AsyncMock(return_value={7: "LANA JOSEPH"})  # type: ignore[method-assign]
    return rest


@pytest.mark.anyio
async def test_asterisk_means_the_field_has_any_value():
    """The missing other half of the blank check. Asked to "show Criminal cases
    WITH assigned attorneys", the model had only the blank idiom and returned the
    exact opposite set — 49 unassigned, when 188 of 237 do have an attorney."""
    rest = _rest_with_attorneys(_cases_with_and_without_attorney())
    result = await rest.aggregate_cases(custom_field_filters={"assigned_attorney": "*"})
    assert result["total_cases"] == 3
    assert all(r["assigned_attorney"] == "LANA JOSEPH" for r in result["items"])


@pytest.mark.anyio
async def test_empty_string_still_means_blank():
    """The two conventions must stay exact opposites."""
    rest = _rest_with_attorneys(_cases_with_and_without_attorney())
    result = await rest.aggregate_cases(custom_field_filters={"assigned_attorney": ""})
    assert result["total_cases"] == 2
    assert all(r["assigned_attorney"] == "(unassigned)" for r in result["items"])


@pytest.mark.anyio
async def test_a_field_filtered_result_carries_its_denominator():
    """Without it a filtered subset gets narrated as the whole population — "All 49
    Criminal cases have no assigned attorney" when 237 exist and 188 have one."""
    rest = _rest_with_attorneys(_cases_with_and_without_attorney())
    result = await rest.aggregate_cases(custom_field_filters={"assigned_attorney": "*"})
    assert result["total_ignoring_field_filters"] == 5, "the unfiltered peer count"
    assert "3 of 5" in result["field_filter_note"]
    assert "never describe this result as 'all'" in result["field_filter_note"]


@pytest.mark.anyio
async def test_no_denominator_noise_when_no_field_filter_was_used():
    rest = _rest_with_attorneys(_cases_with_and_without_attorney())
    result = await rest.aggregate_cases(group_by="assigned_attorney")
    assert "total_ignoring_field_filters" not in result
    assert "field_filter_note" not in result


@pytest.mark.anyio
async def test_the_denominator_respects_the_other_filters():
    """The peer count is "cases matching everything EXCEPT the field filter", not
    the whole firm — otherwise the ratio would be meaningless."""
    cases = _cases_with_and_without_attorney()
    cases[0]["case_stage"] = "CLOSED"  # one of the attorneyed cases
    rest = _rest_with_attorneys(cases)
    result = await rest.aggregate_cases(
        exclude_case_stages=["CLOSED"], custom_field_filters={"assigned_attorney": "*"}
    )
    assert result["total_cases"] == 2
    assert result["total_ignoring_field_filters"] == 4, "excludes the CLOSED case on both sides"
