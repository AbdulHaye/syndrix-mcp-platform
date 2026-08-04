"""Seed Langfuse "datasets" with real prompts that previously exposed bugs in the
MyCase/Podio agents — this is the regression-test recommendation from the Anthropic
"Building Effective Agents" review (2026-07-22/23 session): every bug we found this
session (blank-filter logic, computed-field group_by, duration filtering, count
mismatches, off-topic scope leakage) was only caught by manually running one prompt
at a time and reading raw JSON. Freezing those prompts here means the NEXT prompt
change can be checked against them via Langfuse's dataset "Run experiment" feature
(or `Langfuse.run_experiment`) instead of needing another live debugging session.

Usage: python scripts/seed_langfuse_regression_dataset.py
Idempotent — re-running just adds new items for anything not already present isn't
deduplicated by Langfuse itself, so avoid running this more than once per new item
set; extend the ITEMS lists below as new real incidents are found instead.

Each item's `expected_output` is a plain-English description of correct behavior,
not a strict string match — grading against it (via run_experiment + an evaluator,
or manual review in the Langfuse UI) is a separate, deliberate follow-up step, not
automated by this script.
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from langfuse import Langfuse  # noqa: E402

MYCASE_DATASET = "mycase-agent-regressions"
PODIO_DATASET = "podio-agent-regressions"

MYCASE_ITEMS: list[dict] = [
    {
        "input": {"message": "get the cases where there is no Processing Agent"},
        "expected_output": (
            "aggregate_cases is called with custom_field_filters={'PROCESSING AGENT': ''}. "
            "Every returned row's PROCESSING AGENT value is genuinely blank/None — never a "
            "real agent name. The reply's headline 'Found N cases' count matches the number "
            "of rows actually in the table exactly."
        ),
        "metadata": {"category": "blank-filter", "incident": "2026-07-22 Processing Agent blank filter returned 1425 non-blank rows"},
    },
    {
        "input": {"message": "get the cases where there is no Lead attorney"},
        "expected_output": (
            "aggregate_cases succeeds using 'assigned_attorney' (or 'Lead Attorney') as a "
            "custom_field_filters/group_by key with an empty-string value — it must NOT error "
            "and fall back to an unfiltered call. Every returned row's assigned_attorney is "
            "'(unassigned)'. The reply's headline count matches the table row count exactly."
        ),
        "metadata": {"category": "computed-field-filter", "incident": "2026-07-22 assigned_attorney group_by errored, silent fallback to unfiltered call, fabricated count in reply"},
    },
    {
        "input": {"message": "get me all the cases where closed within 1 month of opening"},
        "expected_output": (
            "aggregate_cases is called with days_to_close_max=30 (NOT an approximation using "
            "opened_after/opened_before/closed_after/closed_before absolute date ranges). Every "
            "returned row has a days_to_close field between 0 and 30 inclusive. The reply's "
            "headline count matches the table row count exactly."
        ),
        "metadata": {"category": "duration-filter", "incident": "2026-07-22 duration-between-own-fields had no real filter, model approximated wrongly, reply count (4844) did not match table (7220)"},
    },
    {
        "input": {"message": "Who is Elon Musk and what companies does he own?"},
        "expected_output": (
            "The agent refuses — zero tool calls, no biographical/factual content about Elon "
            "Musk or his companies anywhere in the reply. A short redirect back to MyCase topics."
        ),
        "metadata": {"category": "scope-restriction", "incident": "2026-07-23 off-topic general knowledge question"},
    },
    {
        "input": {"message": "Can you explain what agentic AI and agent workflows are?"},
        "expected_output": (
            "The agent refuses — zero tool calls, no explanation of agentic AI / agent workflow "
            "concepts anywhere in the reply. A short redirect back to MyCase topics. (Before the "
            "SCOPE fix, this produced a full 500+ word unrelated essay.)"
        ),
        "metadata": {"category": "scope-restriction", "incident": "2026-07-23 off-topic AI-concept question — the original reported bug"},
    },
    {
        "input": {"message": "how many total cases do we have"},
        "expected_output": (
            "A genuine on-topic count question — the agent MUST still call get_cases/aggregate_cases "
            "and answer with the real item_count. This item exists to catch over-blocking: a future "
            "prompt change to the SCOPE rule must not cause this legitimate question to be refused."
        ),
        "metadata": {"category": "scope-restriction-sanity-check"},
    },
    # ── 2026-08-04 session: 16 real user-reported bugs, root-caused via live probes ──
    {
        "input": {"message": "Show recently created cases from MyCase."},
        "expected_output": (
            "aggregate_cases is called with created_after (the case's own created_at), NOT "
            "opened_after — opened_date is a separate case-management concept. The word "
            "'created' should not need to be present for this to work correctly."
        ),
        "metadata": {"category": "date-filter", "incident": "2026-08-04 'recently created cases' needed the literal word 'created' to work"},
    },
    {
        "input": {"message": "List all cases with missing SOL date."},
        "expected_output": (
            "aggregate_cases is called with custom_field_filters={'sol_date': ''} — sol_date is "
            "a REAL native MyCase case field (confirmed live), NOT a custom field, so it must be "
            "recognized directly rather than the agent saying it doesn't understand 'SOL date'."
        ),
        "metadata": {"category": "unknown-field", "incident": "2026-08-04 agent didn't understand the SOL date field; sol_date is a real builtin case field missing from _BUILTIN_CASE_FIELDS"},
    },
    {
        "input": {"message": "Find duplicate contacts based on email or phone number."},
        "expected_output": (
            "find_duplicate_clients is called. KNOWN LIMITATION on this account: MyCase's own "
            "/clients endpoint can return HTTP 504 even at page_size=1 (confirmed live, retries "
            "don't help) — if that happens, the agent must say so plainly, not claim zero "
            "duplicate contacts exist."
        ),
        "metadata": {"category": "missing-tool", "incident": "2026-08-04 no duplicate-contact detection tool existed at all"},
    },
    {
        "input": {"message": "Show all contacts created this month."},
        "expected_output": (
            "aggregate_clients is called ONCE with created_after/created_before covering the "
            "current month — not multiple manual queries. Same /clients 504 known-limitation "
            "caveat as the duplicate-contacts item applies."
        ),
        "metadata": {"category": "missing-tool", "incident": "2026-08-04 'contacts created this month' needed multiple queries and still filtered wrong"},
    },
    {
        "input": {"message": "Show prospects that need follow-up."},
        "expected_output": (
            "aggregate_leads is called with status='NEED FOLLOW-UP' (the real, exact literal "
            "status string confirmed live in this account) — NOT an unfiltered lead dump. Every "
            "returned row's status is genuinely 'NEED FOLLOW-UP'."
        ),
        "metadata": {"category": "missing-tool", "incident": "2026-08-04 'prospects that need follow-up' returned all leads unfiltered"},
    },
    {
        "input": {"message": "Find prospects that have no assigned attorney."},
        "expected_output": (
            "aggregate_leads is called with assigned_attorney='' — resolved via each lead's "
            "linked case's lead_lawyer staff flag (confirmed live: most leads genuinely have no "
            "linked case yet, so this should return a real, large, non-empty result, not nothing)."
        ),
        "metadata": {"category": "missing-tool", "incident": "2026-08-04 'prospects with no assigned attorney' returned no data"},
    },
    {
        "input": {"message": "Show number of active cases assigned to each attorney."},
        "expected_output": (
            "aggregate_cases is called with status='open' (or the real 'active' status string) "
            "and group_by='assigned_attorney'. The reply presents a per-attorney breakdown "
            "directly from the returned `groups` array (name+count per attorney) — it does NOT "
            "just say 'see the table below' and leave a flat, ungrouped case list standing in "
            "for the requested breakdown."
        ),
        "metadata": {"category": "grouping-presentation", "incident": "2026-08-04 grouped requests returned a flat ungrouped case list instead of a per-group breakdown"},
    },
    {
        "input": {"message": "Show staff members with their roles and permissions."},
        "expected_output": (
            "The agent states plainly that MyCase's API exposes no role/permission data for "
            "staff (confirmed live: only name/email/title/type/default_hourly_rate/active exist) "
            "— it does NOT fabricate roles/permissions from `title`/`type` as if they were an "
            "access-control system."
        ),
        "metadata": {"category": "unsupported-data", "incident": "2026-08-04 agent didn't understand the role/permission parameter — it genuinely does not exist in MyCase's API"},
    },
    {
        "input": {"message": "Find users who can access case management."},
        "expected_output": (
            "The agent states plainly that MyCase exposes no access-control/permissions data at "
            "all — it does not guess or silently return nothing with no explanation."
        ),
        "metadata": {"category": "unsupported-data", "incident": "2026-08-04 agent didn't know how to check this parameter — it genuinely does not exist"},
    },
    {
        "input": {"message": "List unpaid invoices grouped by client."},
        "expected_output": (
            "aggregate_invoices is called with paid=False and group_by='client_name' (resolved "
            "via each invoice's linked case, NOT the /clients endpoint). The reply presents a "
            "per-client breakdown from the returned `groups` array, not one flat unpaid-invoice "
            "list with no grouping applied."
        ),
        "metadata": {"category": "missing-feature", "incident": "2026-08-04 'unpaid invoices grouped by client' returned all unpaid invoices, ungrouped"},
    },
    {
        "input": {"message": "Show invoices by assigned attorney."},
        "expected_output": (
            "aggregate_invoices(group_by='assigned_attorney') or aggregate_payments(group_by="
            "'attorney') is called — real attorney names with real counts/amounts. The agent "
            "must NOT reply with a plain unpaid-invoices list (the original hallucination) — "
            "'by assigned attorney' must actually appear as the grouping dimension."
        ),
        "metadata": {"category": "hallucination", "incident": "2026-08-04 'invoices by assigned attorney' hallucinated a plain unpaid-invoices list instead of grouping by attorney"},
    },
    {
        "input": {"message": "Show all payments received in MyCase."},
        "expected_output": (
            "aggregate_payments is called (NOT a raw get_invoice_payments dump the agent tries "
            "to eyeball-sum). The reply states the real total_payments/total_amount — confirmed "
            "live this account has 9,314 payments totaling ~$8.16M — not 'no data'."
        ),
        "metadata": {"category": "missing-tool", "incident": "2026-08-04 'all payments received' returned no data — no deterministic payments tool existed"},
    },
    {
        "input": {"message": "Show payment history for a case 43820502"},
        "expected_output": (
            "get_case_payments(case_id=43820502) is called — a REAL tool call, not text that "
            "looks like a tool call leaking into the reply. If the case has no payments, the "
            "agent says so plainly instead of erroring or fabricating a call."
        ),
        "metadata": {"category": "tool-call-leak", "incident": "2026-08-04 'payment history for a case' produced a fake tool-call-shaped reply instead of a real answer — no matching tool existed"},
    },
    {
        "input": {"message": "List cases grouped by case stage."},
        "expected_output": (
            "aggregate_cases(group_by='case_stage') is called. The reply presents a per-stage "
            "breakdown directly from the returned `groups` array (name+count per stage) instead "
            "of dumping the flat, ungrouped case list and calling that 'grouped'."
        ),
        "metadata": {"category": "grouping-presentation", "incident": "2026-08-04 'cases grouped by case stage' returned all cases, ungrouped"},
    },
    {
        "input": {"message": "Show open cases by stage."},
        "expected_output": (
            "aggregate_cases(status='open', group_by='case_stage') is called. Same per-stage "
            "breakdown-from-`groups` requirement as the plain 'grouped by case stage' item."
        ),
        "metadata": {"category": "grouping-presentation", "incident": "2026-08-04 'open cases by stage' returned all cases, ungrouped"},
    },
    {
        "input": {"message": "Which stage has the highest number of cases?"},
        "expected_output": (
            "aggregate_cases(group_by='case_stage') is called — group_by must NOT be omitted — "
            "and the reply's claimed top stage matches groups[0] (the real, sorted-by-count-"
            "descending first entry) of the actual result, with the reply naming a real case "
            "STAGE value (e.g. 'CLOSED'), not a practice area."
        ),
        "metadata": {"category": "wrong-answer", "incident": "2026-08-04 'which stage has the highest number of cases' gave a wrong answer — no groups summary existed, only a flat truncatable row list"},
    },
    # ── 2026-08-04: group-size (HAVING) filtering ──
    {
        "input": {"message": "Show me clients who have more than one case"},
        "expected_output": (
            "aggregate_cases(group_by='client_name', min_group_size=2) — a single call. The reply "
            "presents the surviving GROUPS (each client + their case count), not individual cases. "
            "It must NOT call aggregate_clients/get_clients (a client record carries no case count, "
            "and that endpoint 504s on this account), and must NOT dump every matching case and "
            "eyeball which clients appear twice — the originally reported failure returned all "
            "2,380 cases ungrouped."
        ),
        "metadata": {
            "category": "group-size-having",
            "incident": "2026-08-04 'clients with more than one case' had no HAVING primitive; observed "
                        "failing two different ways — 2,380 ungrouped rows, and a wrong-tool call to "
                        "aggregate_clients that 504'd",
        },
    },
    {
        "input": {"message": "Which attorneys have at least 50 open cases?"},
        "expected_output": (
            "aggregate_cases(status='open', group_by='assigned_attorney', min_group_size=50). Reply "
            "lists only attorneys meeting the threshold, with counts, from the `groups` array."
        ),
        "metadata": {"category": "group-size-having"},
    },
    {
        "input": {"message": "Which practice areas have only one case?"},
        "expected_output": (
            "aggregate_cases(group_by='practice_area', max_group_size=1) — the max_group_size side "
            "of the same primitive."
        ),
        "metadata": {"category": "group-size-having"},
    },
    {
        "input": {"message": "Show me the top 5 clients by unpaid balance"},
        "expected_output": (
            "aggregate_invoices(paid=False, group_by='client_name'). CRITICAL: the reply must not "
            "present placeholder buckets as clients. ~3,222 case ids referenced by invoices return "
            "404 (the case was deleted in MyCase), so those invoices group under "
            "'(case deleted in MyCase)' / '(no case linked)'. A real reply once opened with "
            "\"Top groups: (no case) ($77,091), (unknown case) ($46,110)\" — reading as if two "
            "clients were named that. Placeholders must be reported separately as unmatched."
        ),
        "metadata": {
            "category": "unresolved-placeholder-groups",
            "incident": "2026-08-04 dangling invoice->case references surfaced as fake client names",
        },
    },
    # ── 2026-08-04: LLM-directed Excel reporting (build_report) ──
    {
        "input": {
            "message": "Show number of active cases assigned to each attorney, with each "
                       "attorney's cases on a separate sheet and a summary"
        },
        "expected_output": (
            "aggregate_cases is called with status='open' and group_by='assigned_attorney' and "
            "NO limit, then build_report is called with both a `summary` section (group_by="
            "'assigned_attorney', metrics [{op:count}]) and a `detail_sheets` section "
            "(split_by='assigned_attorney'). The reply states the totals and lists the "
            "per-attorney breakdown from the report's `summary`, and does NOT paste the "
            "download_url or re-list individual cases. A Download Excel button renders."
        ),
        "metadata": {
            "category": "excel-report",
            "incident": "2026-08-04 the chat could only ever render ONE flat table per resource, so "
                        "'one sheet per attorney plus a summary' had no code path at all",
        },
    },
    {
        "input": {"message": "Export all unpaid invoices to excel, grouped by client"},
        "expected_output": (
            "aggregate_invoices(paid=False, group_by='client_name') with no limit, then "
            "build_report with split_by/group_by on client_name. Reply gives the per-client "
            "breakdown plus a Download button — not a flat invoice dump."
        ),
        "metadata": {"category": "excel-report"},
    },
    {
        "input": {"message": "Give me 5 immigration cases"},
        "expected_output": (
            "A SMALL plain listing — aggregate_cases(practice_area='Immigration', limit=5) and a "
            "normal inline table. The agent must NOT build an Excel report for this: nothing was "
            "grouped or split, and forcing a workbook onto a 5-row listing would be a regression. "
            "This item exists to catch over-triggering of build_report."
        ),
        "metadata": {"category": "excel-report-sanity-check"},
    },
    {
        "input": {"message": "Which case stage has the highest number of cases?"},
        "expected_output": (
            "aggregate_cases is called with group_by='case_stage' EXPLICITLY passed — the reply "
            "must name a real case stage value (e.g. 'CLOSED', 'IMMIGRATION- SUBMITTED (MAIL/"
            "UPLOAD PACKAGE)'). The reply must NOT answer with a practice area name (e.g. "
            "'Immigration') — that means group_by was omitted and silently defaulted to "
            "practice_area instead of the case_stage dimension the user explicitly asked about."
        ),
        "metadata": {"category": "wrong-group-by-dimension", "incident": "2026-08-04 live agent-chat test: the model omitted group_by entirely, it defaulted to practice_area, and the reply answered with a practice area breakdown ('Immigration: 4,002 cases') instead of case stages — caught via a real end-to-end chat test, not just the underlying tool logic, which was already correct"},
    },
]

PODIO_ITEMS: list[dict] = [
    {
        "input": {"message": "Can you explain what agentic AI and agent workflows are?"},
        "expected_output": (
            "The agent refuses — zero tool calls, no explanation of agentic AI concepts anywhere "
            "in the reply. A short redirect back to Podio topics."
        ),
        "metadata": {"category": "scope-restriction", "incident": "2026-07-23 parity fix mirrored from the MyCase Agent SCOPE bug"},
    },
    {
        "input": {"message": "list the apps in our workspace"},
        "expected_output": (
            "A genuine on-topic request — the agent MUST call get_apps_in_space (or equivalent) "
            "and list the real apps. This item exists to catch over-blocking from the SCOPE rule."
        ),
        "metadata": {"category": "scope-restriction-sanity-check"},
    },
]


def seed(dataset_name: str, description: str, items: list[dict]) -> None:
    client = Langfuse()
    client.create_dataset(name=dataset_name, description=description)
    for item in items:
        client.create_dataset_item(
            dataset_name=dataset_name,
            input=item["input"],
            expected_output=item["expected_output"],
            metadata=item.get("metadata"),
        )
    client.flush()
    print(f"Seeded {len(items)} items into dataset '{dataset_name}'.")


if __name__ == "__main__":
    seed(
        MYCASE_DATASET,
        "Real prompts that previously exposed bugs in the MyCase Agent (aggregate_cases/"
        "aggregate_leads/aggregate_invoices/aggregate_payments filtering and grouping, missing "
        "native fields, scope restriction) — re-run after any change to mycase_agent.py's system "
        "prompt or mycase_rest.py's aggregate_* tools to catch a regression before a user does.",
        MYCASE_ITEMS,
    )
    seed(
        PODIO_DATASET,
        "Real prompts that previously exposed bugs in the Podio Agent (scope restriction) — "
        "re-run after any change to podio_agent.py's system prompt.",
        PODIO_ITEMS,
    )
