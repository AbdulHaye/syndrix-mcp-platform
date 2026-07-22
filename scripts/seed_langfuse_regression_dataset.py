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
        "Real prompts that previously exposed bugs in the MyCase Agent (aggregate_cases "
        "filtering, scope restriction) — re-run after any change to mycase_agent.py's system "
        "prompt or mycase_rest.py's aggregate_cases to catch a regression before a user does.",
        MYCASE_ITEMS,
    )
    seed(
        PODIO_DATASET,
        "Real prompts that previously exposed bugs in the Podio Agent (scope restriction) — "
        "re-run after any change to podio_agent.py's system prompt.",
        PODIO_ITEMS,
    )
