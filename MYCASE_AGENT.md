# MyCase Agent

A second, independent chat agent in Syndrix — alongside the Podio Agent — that lets BD/Admin
users query MyCase (legal practice management software) in plain language. Built Sessions
23–25, heavily hardened in a long reliability-focused session afterward (search/aggregation
tools, pagination correctness, hallucination guards, UI redesign — all summarized below); see
`CLAUDE.md` for the full narrative session log. This file is the standalone reference for the
feature itself.

**Scope: read-only.** 46 real GET/download endpoints plus 7 deterministic tools built on top of
them (`aggregate_cases`, `search_cases`, `find_cases_with_documents`, `get_case_folder_tree`,
`get_invoices_by_date`, `get_case_invoices`, `lookup_utbms_code`) — **53 tools total**.
Create/update/delete operations are not implemented.

**Core design principle, learned the hard way over many fixes:** anything that requires
checking/aggregating/searching across MORE than a handful of records must be computed
**server-side in Python**, never left to the LLM to figure out by reading raw tool results. An
LLM sampling or eyeballing a truncated JSON blob reliably produces wrong answers once the real
dataset is bigger than what fits in one glance — silently, confidently, and often unrelated to
what actually exists in MyCase. Every deterministic tool below exists because a "let the model
call get_X and figure it out" version of that exact request was tried first and produced a wrong
or fabricated answer.

---

## Architecture

Mirrors the Podio Agent's file-per-layer pattern exactly, as its own independent stack:

```
app/services/mycase_rest.py      ← Thin REST client, one method per GET endpoint (46) + OAuth +
                                    5 deterministic tools (aggregate_cases, search_cases,
                                    find_cases_with_documents, get_case_folder_tree)
app/services/mycase_utbms.py     ← Static UTBMS/LEDES billing-code reference table (not an API call)
app/mcp_servers/mycase.py        ← Standalone FastMCP server, one @mcp.tool() per method (51 total)
app/services/mycase_client.py    ← In-process list_tools()/call_tool() adapter for the agent loop;
                                    also catches/reformats pydantic arg-validation errors into
                                    plain English instead of a raw dump
app/services/mycase_agent.py     ← Agent loop: LLM + MyCase tools → reply. Read-only (no
                                    write-gating), but DOES have a read-hallucination guard —
                                    see "Anti-hallucination measures" below
app/api/mycase_agent.py          ← POST /agent/mycase, session CRUD, GET /agent/mycase/status
app/api/integrations_mycase.py   ← OAuth connect/callback/disconnect (the "Connect MyCase" flow)
app/storage/models.py            ← MyCaseChatSession ORM model (separate table from PodioChatSession)
alembic/versions/a7c2e9f1b3d4_*  ← Migration for mycase_chat_sessions
alembic/versions/f4a7b9c1d2e3_*  ← (Podio's — unrelated, listed for revision-chain context only)

frontend/app/dashboard/mycase-agent/page.tsx  ← Chat UI (adapted from podio-agent, no workspace picker/file upload)
frontend/components/Sidebar.tsx               ← "MyCase Agent" top-level item, BD/Admin only
frontend/app/dashboard/settings/page.tsx      ← "MyCase" credentials card
```

Why separate tables/files instead of generalizing the Podio ones? Keeps the two agents' data,
migrations, and behavior fully independent — a bug or schema change in one can't affect the
other. The tradeoff is some duplicated boilerplate (session CRUD, text-tool-call recovery
helpers); acceptable for now, a shared-module extraction would be a reasonable future cleanup
but was not done to avoid destabilizing the already-shipped, heavily-tuned Podio agent.

---

## The real MyCase API

- **Data API base:** `https://external-integrations.mycase.com/v1` (distinct from the docs host
  `mycaseapi.stoplight.io`). Auth: `Authorization: Bearer <access_token>`.
- **Rate limit:** 25 requests/second per client. `mycase_rest.py`'s `_request()` retries up to 3
  times (1s/2s/4s backoff) on a 429 before giving up.
- **Pagination:** cursor-based. `page_size` (1–1000, default 25) + a `Link: <url>; rel="next"`
  response header carrying the next `page_token` (expires after 3 days — irrelevant within one
  chat). `Item-Count` header gives the total. Every `get_*` list method returns a uniform
  `{"items": [...], "item_count": int|None, "next_page_token": str|None}`.
- **Sparse fieldsets:** a few endpoints (notably Cases) accept `field[client]=id,first_name,...`
  to expand nested objects beyond their default `{"id": N}`-only shape.
- **Dates:** always include a UTC offset (e.g. `2024-01-01T00:00:00Z`) — MyCase silently assumes
  UTC if you omit it, which can shift a "today" filter by hours for a non-UTC user.
- **Errors:** structured as `{"errors": [{"description": "...", "source": {"pointer": "..."} |
  {"parameter": "..."}}]}` on every 4xx/5xx. `mycase_rest.py`'s `_extract_error_message()` parses
  this into a human-readable message (e.g. `"MyCase API 401: Unauthorized (Unauthorized - not
  authenticated; the access token may be expired, try reconnecting)"`), backed by a static
  `_ERROR_CODE_TEXT` map for all 8 documented codes:

  | Code | Meaning |
  |---|---|
  | 400 | Bad Request — request body format is incorrect |
  | 401 | Unauthorized — not authenticated / token expired |
  | 403 | Forbidden — insufficient MyCase permissions |
  | 404 | Not Found — bad path or unknown id |
  | 422 | Unprocessable Entity — a field is invalid / fails a business rule |
  | 429 | Too Many Requests — rate limited (auto-retried) |
  | 500 | Internal Server Error |
  | 503 | Service Unavailable — MyCase scheduled maintenance |

---

## OAuth — Authorization Code grant (confirmed against MyCase's real docs, Session 24)

1. **Browser redirect:** `GET https://auth.mycase.com/login_sessions/new?client_id=...&redirect_uri=...&response_type=code&state=...`
   — the user logs into MyCase directly (their own username/password; NOT anything Syndrix
   controls) and grants access.
   ⚠️ The authorizing MyCase user needs the **"Manage your firm's preferences, billing, and
   payment options"** permission set to Yes, or MyCase returns a Forbidden response instead of
   completing login.
2. **Token exchange:** `POST https://auth.mycase.com/tokens` with a **JSON** body (not
   form-encoded): `{client_id, client_secret, code, grant_type:"authorization_code", redirect_uri}`.
3. **Refresh** (same endpoint): `{client_id, client_secret, refresh_token, grant_type:"refresh_token"}`.
4. **Token response:** `{access_token, token_type:"Bearer", scope, refresh_token, expires_in:86400, firm_uuid}`.
   Access tokens last **24 hours**; refresh tokens last **2 weeks**.

The `redirect_uri` is fixed by MyCase support when the Client ID/Secret are issued — it cannot
be changed client-side; whatever is in Settings → MyCase → Redirect URI must match exactly.

### Frontend connect UX (`mycase-agent/page.tsx`'s `MyCaseConnection`)
"Connect MyCase" opens the MyCase login in a **new tab**, not the same tab — a blank popup is
opened synchronously on click (before the `await startMyCaseConnect()` call), then redirected to
the real authorize URL once it's fetched. This ordering matters: opening the tab *after* an
awaited async call gets silently blocked by most browsers' popup blockers, since the async gap
breaks the "direct result of a user gesture" association a browser requires to allow
`window.open`. The popup's `opener` is severed (`popup.opener = null`) for the same reason a
target="_blank" link normally gets `rel="noopener"` — prevents the new tab from reaching back
into the Syndrix window — while still keeping our own reference so we can set its location.
Because the OAuth callback redirects that new tab (not the original one), the original tab
re-checks connection status via a `window` `focus` event listener — switching back to it after
finishing login in the other tab auto-refreshes the "connected" badge with no manual reload
needed.

### Backend routes (`app/api/integrations_mycase.py`)
- `GET /integrations/mycase/connect` — returns `{"success": true, "authorize_url": "..."}`.
- `GET /integrations/mycase/callback` — no auth dependency (MyCase's own browser redirect);
  validates `state`, exchanges the code, redirects to `/dashboard/mycase-agent?mycase=connected`
  or `?mycase=error&reason=...`.
- `POST /integrations/mycase/disconnect` — clears `mycase_access_token` /
  `mycase_refresh_token` / `mycase_token_expiry`.

### Token refresh logic (`mycase_rest.py`)
`_get_valid_token()`: if `mycase_token_expiry` is set and still in the future, reuse the stored
access token. If a `mycase_refresh_token` exists but the expiry is unknown (e.g. a manually
pasted access+refresh pair, which has no accompanying `expires_in`) OR the expiry has passed, it
proactively refreshes — establishing a real expiry on success, so subsequent calls take the fast
path above. Only when there is genuinely NO refresh token at all does it fall back to just
reusing the pasted access token as-is with no validity check (nothing else it can do).

**Practical implication:** a token from the "Connect MyCase" browser flow auto-renews forever (as
long as it's used at least once every 2 weeks — MyCase refresh tokens expire after 2 weeks of
total inactivity, and each refresh issues a new one, extending the window). **A token pasted
directly into Settings → MyCase → Access Token, with NOTHING in the Refresh Token field, does
not auto-renew** — it silently 401s after 24h until a human pastes a fresh one. If you also paste
the matching Refresh Token (issued alongside the access token by whatever gave it to you), it
DOES auto-renew from then on, same as the Connect flow — this used to silently fail to work even
when both fields were filled in, because of the dead-code-path bug described above (a present
refresh token with unknown expiry was never actually attempted, just ignored). Connect MyCase
remains the simplest reliable path when you have credentials to log in with; the paste-both-tokens
route is the fallback when you only have tokens in hand (e.g. from another integration) and can't
run the browser flow.

---

## Settings (Settings → MyCase)

| Key | Set by | Notes |
|---|---|---|
| `mycase_client_id` | User, via Settings UI | From MyCase support |
| `mycase_client_secret` | User, via Settings UI (masked) | From MyCase support |
| `mycase_redirect_uri` | User, via Settings UI | Must exactly match what MyCase support registered |
| `mycase_access_token` | User (direct paste) OR OAuth exchange | Primary credential actually used for API calls |
| `mycase_refresh_token` | OAuth exchange, OR user paste via Settings UI | Pair with a manually-pasted Access Token to get auto-refresh without the browser flow |
| `mycase_token_expiry` | OAuth exchange only | Unix timestamp; absent for a directly-pasted token |
| `mycase_firm_uuid` | OAuth exchange only | Captured from the token response, not currently used elsewhere |
| `mycase_agent_model` | Model dropdown on the MyCase Agent page | `POST /llm/model` with `agent:"mycase"`; independent of Podio's `agent_model` |

All stored as plaintext in the `integration_settings` table (same pattern/caveat as every other
provider credential in this app — see `CLAUDE.md` §2 for the general plaintext-secrets note).

---

## The 53 tools

46 real MyCase GET/download endpoints + 1 static reference lookup + 6 deterministic
search/aggregation/traversal/date-filter tools, grouped by resource. Full per-endpoint schemas
(params, response fields) are documented as docstrings in `app/mcp_servers/mycase.py`; this is
just the inventory:

**Cases:** `get_cases`, `get_case`, `get_client_cases`, `get_case_folder`, `get_case_folder_tree`, `get_case_documents`, `get_case_notes`
**Case config:** `get_case_stages`, `get_case_roles`
**Clients (people):** `get_clients`, `get_client`, `get_client_notes`, `get_client_message_threads`
**Companies:** `get_companies`, `get_company`
**Leads:** `get_leads`, `get_lead`
**Custom fields:** `get_custom_fields`, `get_custom_field`, `get_custom_field_list_options`
**Documents:** `get_documents`, `get_document`, `get_document_versions_all`, `get_document_versions`, `download_document`, `download_document_version`
**Folders:** `get_folder_documents`, `get_folder_subfolders`
**Calendar:** `get_events`
**Billing:** `get_expenses`, `get_expense`, `get_invoices`, `get_invoices_by_date`, `get_invoice_payments`, `get_time_entries`, `get_time_entry`
**Tasks:** `get_tasks`
**Staff/firm:** `get_staff`, `get_individual_staff`, `get_me`, `get_firm`
**Reference data:** `get_locations`, `get_practice_areas`, `get_referral_sources`, `get_people_groups`
**Notes:** `get_note`
**Calls:** `get_calls`
**Webhooks:** `get_webhook_subscriptions`
**Reference lookup (not an API call):** `lookup_utbms_code` — LEDES Code Set II (1999B) billing
codes (28 activity codes, 245 task codes), kept as an on-demand tool rather than embedded in the
system prompt to avoid spending tokens on a ~270-entry table every single turn.

**Deterministic tools (all computed server-side in Python, never left to the LLM to work out from
raw results — see the "Core design principle" note at the top):**
- **`aggregate_cases`** — filter cases by practice area / custom field values / exact case
  stage(s) (`case_stages` to KEEP, `exclude_case_stages` to drop — both exact-match, not
  substring) / date ranges (`opened_after`/`opened_before`, `closed_after`/`closed_before`,
  `updated_after`/`updated_before` — all computed client-side, since MyCase has no server-side
  filter for opened_date/closed_date at all), group-by-and-count (builtin field or custom field).
  Returns one flattened row per surviving case with EVERY field (including each custom field
  broken into its own named column, not a nested blob), PLUS `client_name` (resolved from the
  case's clients) and `assigned_attorney` (resolved from the `lead_lawyer` staff member — one
  extra `get_staff()` lookup, done once per call) — the case object itself only ever has bare
  ids for both, never names. `PROCESSING AGENT` gets both a cleaned column (whitespace-collapsed,
  blank/null → `"(unassigned)"`, passed through a curated alias map — see `_AGENT_ALIAS_MAP`) and
  a `PROCESSING AGENT (original)` column with the untouched raw value. Plus that row's
  `group_name`/`case_count`. Use for ANY "count/group/breakdown of cases by X", "cases where
  stage is X", or date-range case report request.
- **`search_cases`** — find case(s) by case_number, internal id, or name. MyCase has no
  server-side filter for any of these. Resolution order: (1) exact case_number/id match wins over
  everything (a case's display *name* often embeds a padded number too, e.g. "01597-Smith", which
  used to cause an unrelated case to match and pollute the result — fixed); (2) the whole query as
  one literal substring against case_number OR name; (3) if that finds nothing, multi-word
  matching — filler words (case, matter, for, the, a, an, of, and, in, on, re) are stripped, and a
  case matches if EVERY remaining significant word appears somewhere in case_number + name +
  practice_area + that case's **CASE TYPE custom field value** (a small, curated allowlist of
  field names — see the follow-up below for why it's narrow, not every custom field), in any
  order — so a natural-language description ("ASYLUM Case for MOISE PIERRE") finds a case whose
  literal NAME never contains the word "Asylum" at all, because that classification lives only in
  a custom field.
- **`find_cases_with_documents`** — "give me N cases that have documents": scans every document
  firm-wide (each document's `case` field is `{"id": N}` per MyCase's real schema), tallies real
  document counts per case, returns the N with the most. Replaces an earlier broken approach that
  just sampled the first few cases from `get_cases` and hoped some had documents.
- **`get_case_folder_tree`** — a case's ENTIRE folder structure (root + every subfolder,
  recursively, to `max_depth`) with each folder's documents, in one call. For the document LIST
  itself (not structure), `get_case_documents` is simpler and already complete — this is only for
  "what does the folder structure look like" requests.
- **`get_invoices_by_date`** — exact-day or range filter on `created_at`/`updated_at`/
  `invoice_date`/`due_date`. `get_invoices` itself only supports `updated_after` (a floor on
  created-OR-updated time — see "MyCase's ONE date filter" below) — this walks every page and
  filters precisely in Python for ANY "invoices created/due/dated on|before|after X" request.
- **`get_case_invoices`** — all invoices for ONE case, given `case_id` OR a loose `case_query`
  (resolved exactly like `search_cases` — numeric → case_number then id; text → substring on
  case_number/name). `get_invoices` has no server-side case filter, so this resolves the case
  AND filters invoices in one call — critically, if `case_query` matches ZERO cases it returns
  immediately (`matched_case: null`, empty `items`) WITHOUT ever touching `/invoices` (see
  "No match, no fallback scan" below); if it matches MORE than one, `candidates` lists them
  instead of guessing.

For local Ollama models, the tool list is capped at 16 (keyword-priority matched against the
user's message, mirrors the Podio agent's same pattern) — cloud models get the full 53.

### No match, no fallback scan — a real "wasted 1,000-row scan" incident

Reported: "get all the invoices related to the ASYLUM Case for MOISE PIERRE" — `search_cases`
correctly found no case matching that description (it was a loose natural-language description,
not the case's actual name/number — a legitimate no-match, not a search bug). The model's reply
correctly said so and even offered a good recommendation ("search by client name, or give me the
exact case id/number") — but it ALSO, on its own initiative, called `get_invoices` and scanned up
to 1,000 invoices firm-wide "just in case", reporting an irrelevant "I found 1,000 invoices in the
system" aside. There was no case to filter by, so that scan was guaranteed to produce nothing
useful — pure wasted tokens, and confusing to read.

**Fix:** `get_case_invoices` (above) does case resolution and invoice filtering as ONE
deterministic call — when the case doesn't resolve, it returns immediately with an empty
`items[]` and never calls `/invoices` at all, so there is no raw invoice batch left lying around
for the model to "helpfully" fall back to scanning. The system prompt also explicitly forbids
calling `get_invoices`/`get_cases` as a fallback after a failed case lookup — a "not found" ends
with the plain statement + recommendation (which the model was already doing right), never a
scan of an unrelated dataset.

**Also fixed while here:** `search_cases`'s query resolution only ever checked a numeric query
against `case_number` — never the case's own internal numeric `id`. A purely numeric query is now
checked against `case_number` first, then `id`, before falling back to a substring match — so
"find case 41349079" (an internal id, not a case_number) now resolves correctly instead of
silently matching nothing.

**Follow-up — the SAME exact query still failed after the above fix; root-caused live against the
real MyCase account (not a mock).** Re-running "get all invoices related to the ASYLUM Case for
MOISE PIERRE" — and later "get all the details for the case ASYLUM Case for MOISE PIERRE" — still
returned "No case found", even with the multi-word fallback in place. `get_case_invoices` correctly
stopped the wasted invoice scan (that part of the fix held), but the case itself still didn't
resolve. Rather than guess again, this was debugged with live, read-only calls against the actual
connected MyCase account (`search_cases`, `get_case`, `get_custom_fields`) run directly from a
one-off script, bypassing the running dev server to rule out a stale-reload question too.

**Real root cause found:** the client's actual cases are `01639-Moise Pierre
MOISE PIERRE-IMMIGRATION` (case_number 1639) and `01640-Moise Pierre MOISE PIERRE-TPS` (case_number
1640) — both closed, and **neither name contains the word "Asylum" at all**. The first one's
practice_area is generically "Immigration"; its actual case type — "Asylum" — lives ONLY in a
custom field literally named **CASE TYPE**, confirmed via a direct `get_case()` call:
`custom_field id 1130203, value "Asylum"`. So "asylum" was never a match candidate in ANY field
`search_cases` was checking (case_number, name) — no amount of tuning the word-matching logic
could ever have found it, since the word genuinely isn't present in either searched field.

**First attempt was too broad and produced a real false positive.** Extending the multi-word
haystack to include ALL of a case's custom field values did make the target case match — but ALSO
matched an unrelated case, "Asylum For Richard Pierre-Saint" (case 37322645), because ITS
`PROCESSING AGENT` custom field value happened to be `"Jean-Baptiste Saint-Cyr (Moise)"` — a staff
member's parenthetical nickname that coincidentally contains "moise". With "asylum" (from that
case's own real CASE TYPE), "pierre" (from its own name), and "moise" (from the unrelated staff
nickname) all present, the AND-match fired on an entirely wrong case for entirely wrong reasons.

**Final fix:** `_case_search_haystack()` now reads only `case_number` + `name` + `practice_area` +
custom field values whose field NAME is in a small curated allowlist —
`_CASE_TYPE_CUSTOM_FIELD_NAMES = {"case type", "matter type", "practice type"}` — deliberately
excluding personnel/free-text fields (PROCESSING AGENT, Case Manager, QC - REVIEWER, deadlines,
etc.) that are exactly where this kind of coincidental collision comes from. Re-verified LIVE
against the real account: the exact reported query now returns exactly ONE match (the real Moise
Pierre asylum case, 1639) with no false positive; a parallel "TPS case for Moise Pierre" query
correctly returns only the OTHER Moise Pierre case (1640, TPS) and not the asylum one. Also
regression-tested with mocks (no custom fields present): the plain multi-word name match, an
all-filler-word query, and the single-word substring path are all unchanged.

**Takeaway for future search/matching work on this codebase:** a firm's free-text custom fields
(especially ones holding staff/agent names) are a real source of coincidental substring collisions
once you widen a search's field surface — broadening should target NAMED, curated fields relevant
to what's being searched, not "every field on the record," even when the immediate fix looks like
it works on the one case you're testing.

### MyCase's ONE date filter — and the systemic gap it leaves (found Session, invoices)

Confirmed by reading every "Get X" endpoint in MyCase's own docs: **`filter[updated_after]` is
the ONLY server-side date filter that exists anywhere in this API** — every single list endpoint
(cases, invoices, expenses, time entries, events, tasks, documents, clients, leads, companies,
calls, ...) supports it and NOTHING else. It is a floor ("created or updated after this
date/time"), not an exact-date match, and it has no relationship at all to a resource's own
date-ish fields (`invoice_date`, `due_date`, `opened_date`, `closed_date`, event start/end time,
...).

**Real bug this caused:** "Get all the invoices that were created on 20 July 2026" was answered
by calling `get_invoices(updated_after=<that day>)` — which correctly returns every invoice
*touched* (created OR updated) since then, 20 rows — but nothing then filtered that batch down to
invoices actually *created* that day. The model's own prose partially caught it ("16 of these
were created earlier but updated today"), yet the chat's result table — built directly from the
raw, unfiltered tool result, not the model's text (see "Viewing results" below) — still showed
all 20 rows regardless, 16 of them wrong for the question actually asked.

**Fix:** `get_invoices_by_date` (above) does the fetch-broad-then-filter-exact pattern already
proven by `aggregate_cases` — the model is instructed (system prompt) to use it for any
date-specific invoice question instead of `get_invoices` + eyeballing.

**Same gap exists for every other resource's own date fields** (case `opened_date`/`closed_date`,
expense/time-entry dates, event start/end times, task `due_date`, document `created_at`, etc.) —
none of them have a server-side exact/range filter either, only the same `updated_after` floor.
Only invoices got a dedicated deterministic tool so far (the one that was actually reported
wrong); the same fix pattern (`_walk_all_pages` + `_date_matches` + `_fetch_filtered_by_date`,
all in `mycase_rest.py`) is reusable for the others in a few lines each if a similar wrong-data
report comes in for one of them.

---

## Chat history

`mycase_chat_sessions` table (JSONB `messages` column, same shape as the Podio agent's sessions).
Scoped per real user (`owner_key = "user:<user_id>"` from the JWT) or per team for the
`DEV_TOKENS` fallback. CRUD via `/agent/mycase/sessions[/{id}]` — list/get/upsert/delete, same
REST pattern as `/agent/podio/sessions`.

---

## Anti-hallucination measures

- **Text-tool-call recovery:** if a weak model writes a tool call as plain text/JSON instead of
  using structured function-calling (including the self-describing `{"name":..., "parameters":
  {...}}` envelope shape), it's recovered and executed rather than silently dropped or leaked to
  the user as raw text.
- **Garbage-reply / non-ASCII detection:** degenerate output (repeated-character runs, excessive
  bracket noise, non-ASCII flooding) is discarded and the model re-prompted rather than shown to
  the user.
- **"Stated a number without ever calling the matching tool" guard.** The original assumption was
  "read-only means no hallucination guard is needed" — proven wrong: a model can just as easily
  fabricate a plausible READ answer ("Found 1,023 leads", "Found 12 leads with the first name
  Marie") without ever successfully calling `get_leads` at all, and nothing else catches that if
  there's no real fetched data to compare against. `_unverified_resource_claims()` scans the reply
  for `<number> <resource word>` patterns (leads, clients, cases, companies, invoices,
  documents, ...) and checks whether that resource was ACTUALLY fetched this turn (real items or a
  real `item_count`); if not, the reply is rejected and the model is forced to call the real tool
  and answer from its actual result instead of restating an invented number. Applied both mid-loop
  (can retry) and at the final closing-summary fallback (falls back to an honest "Completed: X.
  Failed: Y." instead of accepting a still-unverified closing reply).
  - **Follow-up — a qualifier word between the number and the resource word slipped past the
    regex entirely.** Reported: "Show me only active staff" / "Show me only inactive staff" each
    got a confident reply ("Found 46 active staff members" / "Found 25 inactive staff members")
    with NO table — meaning `get_staff` was never actually called; the numbers were invented. Root
    cause: `_RESOURCE_CLAIM_RE` required the resource word IMMEDIATELY after the number
    (`\d+\s+staff`), so "46 active staff" didn't match at all (the word "active" sat in between) —
    the guard never even saw a claim to verify. Confirmed live: `"Found 46 staff members"` matched
    the old regex, `"Found 46 active staff members"` did not. **Fixed** by allowing up to 2 filler
    words between the number and the resource word (`\d+\s+(?:\w+\s+){0,2}staff`), so
    "46 active staff", "25 inactive staff members", "12 unassigned tasks", etc. are now all
    correctly caught and forced through a real tool call. Verified against the exact reported
    phrases plus a set of regression cases (plain "46 staff members", "10 open cases", "3 overdue
    invoices") — all still correctly detected.
- **The old "claimed to show records but didn't" text-completeness guard was REMOVED.** It used to
  deterministically append a Markdown table to the reply whenever the model's text didn't contain
  literal record ids — a real safeguard back when the model was expected to hand-type data into
  its reply. Once the design changed to "the model gives a one-line acknowledgment and the UI
  renders the real table independently of the text" (see below), that check started firing on
  *every single correctly-behaving short reply* (since a compliant reply never contains ids by
  design), producing a redundant near-duplicate "already shown as a table below" sentence under
  every answer. It's fully obsolete now: the frontend's `primaryResultGroups` sources data
  straight from the raw tool result, completely independent of what the model's text says, so
  there is nothing left for a text-based completeness check to usefully verify.
- **A genuinely empty assistant turn (no content, no tool_calls) now always gets a `"(no
  response)"` placeholder before being appended to conversation history.** Left empty, that
  message could be silently re-sent to the LLM provider on a later loop iteration (e.g. after a
  bad-reply retry) — Mistral's API rejects an assistant message with neither content nor
  tool_calls outright (`"Assistant message must have either content or tool_calls, but not
  none"`), which surfaced as a raw 400 error reaching the user. Fixed in both `mycase_agent.py`
  and (same vulnerable pattern, same fix) `podio_agent.py`.

---

## Pagination correctness

A "how many clients do we have" question once returned three different numbers across three
layers (a hallucinated 12,327, then a hallucinated 1,432, versus the real 8,448) — not a
pagination bug so much as a **truncation** bug, since fixed at the root:

- **`_get_list()` (the shared helper behind nearly every list endpoint) now returns
  `item_count`/`next_page_token` BEFORE `items` in the dict.** Every tool result gets
  JSON-serialized and cut off at `_MAX_TOOL_OUTPUT_CHARS` (6000) before reaching the model —
  `items` can be up to 1000 full records, so with it listed first, truncation routinely sliced
  straight through (or entirely past) the pagination metadata, leaving the model with no real
  total or continuation token to read, so it fabricated a plausible-sounding number instead. This
  is a systemic, one-line fix: `_get_list()` is shared by essentially every plain list endpoint
  (cases, clients, companies, leads, documents, invoices, expenses, time entries, tasks, events,
  notes, calls, custom fields, case stages, case roles, practice areas, locations, referral
  sources, people groups, ...), so it fixes the whole class of endpoints at once, not just one.
- **Defensive second layer:** whenever a tool result IS truncated, the real `item_count`/
  `next_page_token` are now also spelled out in plain English immediately before the truncated
  blob, independent of JSON key order — a backstop in case any future tool gets the ordering
  wrong again.
- **"How many X" vs "list/show all X" are now different code paths.** `item_count` is accurate
  from page 1 alone (it's MyCase's own reported total across every page, not just the current
  one) — a pure count question only ever needs ONE cheap call (`page_size=1` is enough) and
  should report `item_count` verbatim. A real listing/export request needs the actual rows, so it
  must keep paging with `next_page_token` until it's exhausted. These were conflated before,
  which encouraged both the wrong behavior for counts (over-fetching) and for listings
  (stopping after page 1 while still reporting the full total as if it had been retrieved).
- **`_incomplete_pagination_notes()`** deterministically compares what was actually
  fetched against the real `item_count` and appends an honest disclosure — e.g. *"Note: only
  1000 of 1235 total clients were fetched (pagination stopped early) — the table/CSV below for
  clients is INCOMPLETE."* — whenever a listing request under-fetches, regardless of what the
  model's own text claims. Deliberately suppressed for pure count questions (`count_only`),
  where fetching only 1 row is correct behavior, not something to warn about.

---

## Immigration case reporting requirement — gap analysis and what was built

A business requirement asked for an "active immigration cases" report with a specific field list
(Case ID, Case Name, Client Name, Practice Area, Case Type, Case Status, Case Stage, Processing
Agent, Assigned Attorney, Date Opened, Last Updated, Closed Date, Days in Current Stage, Case
Owner), Case Stage / Processing Agent normalization, and filtering by practice area/case
type/stage/agent/dates/status. Investigated live against the real account (not assumed) — findings
and what was actually built:

| Requirement | Status |
|---|---|
| Retrieve all active immigration cases | ✅ `aggregate_cases(practice_area="Immigration", status="open")` |
| Case ID, Case Name, Practice Area, Case Status, Date Opened, Last Updated, Closed Date | ✅ already returned as-is |
| Case Type | ✅ real custom field `CASE TYPE`, already broken out into its own column |
| Client Name | ✅ **added** — `client_name`, resolved from the case's `clients` via `field[client]` expansion (no extra API calls) |
| Assigned Attorney | ✅ **added** — `assigned_attorney`, resolved from the `staff` member flagged `lead_lawyer=true` via one `get_staff()` lookup (the case object only ever has a bare staff id) |
| Processing Agent (raw) | ✅ real custom field `PROCESSING AGENT` |
| Case Stage | ✅ — see normalization below |
| Days in Current Stage | ❌ **Not available from MyCase's API at all** — see below. Decision: left out. |
| Case Owner | ❌ **No such field exists** in this account's 46 custom fields (checked every one). Decision: left out rather than guess a substitute. |
| Filtering: Practice Area, Case Type, Case Stage, Processing Agent, Status | ✅ already supported |
| Filtering: Date Opened, Last Updated, Closed Date | ✅ **added** — `opened_after`/`opened_before`, `closed_after`/`closed_before`, `updated_after`/`updated_before` |

**Case Stage normalization — the "N days" example didn't match reality.** The spec's example raw
values (`"...ASSIGNED TO PARALEGAL 16 days"`, etc.) suggested `case_stage` embeds a live day-count
suffix that needs stripping. Live-checked: it does not. Real `case_stage` values in this account are
already clean fixed strings (e.g. `"IMMIGRATION- PROCESSING (ASSIGNED TO PARALEGAL)"`), confirmed
by scanning real open cases AND the firm's 34 configured stage names (`get_case_stages()`) — none
contain a day count. The user then supplied a screenshot showing where the day count actually comes
from: MyCase's own web UI has a **"Case Timeline by Stage"** widget (`Days Open: 1195`, broken into
segments like `"IMMIGRATION- PROCESSING (ASSIGNED TO ... 176 Days"`) — a separate, MyCase-internal
computed display, not the `case_stage` API field, which shows the plain stage name with no day count
directly underneath it in the same screenshot. So in this account, Clean Case Stage = Original Case
Stage (no stripping needed) — a defensive regex strip is still worth keeping as a safety net in case
a suffix ever appears in different data, but it isn't the primary gap.

**Days in Current Stage — genuinely unavailable via the API, confirmed by reading every documented
endpoint.** There is no case-history, timeline, or audit-log endpoint anywhere in MyCase's public
API — the timeline widget in the screenshot is computed by MyCase internally and never exposed
externally. Two options were considered and declined for now (both recorded here for when this is
revisited):
1. **Approximate via `updated_at`** — available immediately, no new infrastructure, but inaccurate:
   `updated_at` changes on ANY case edit (a note, a document, a field), not just a stage change.
2. **Build our own tracking** — a scheduled job (Celery Beat, already used elsewhere in this app)
   periodically snapshotting each case's `case_stage`, detecting transitions, and computing real
   elapsed days from OUR recorded transition date going forward. Accurate, but only from the day
   tracking starts — historical stage-entry dates before that can never be recovered from MyCase.

Decision made: leave this field out entirely rather than ship either an inaccurate proxy or invest
in new infrastructure without a clear ask for it. Revisit if/when the accurate (option 2) approach
is wanted.

**Case Owner — no such field exists.** Checked the complete list of this account's 46 custom
fields; none is named "Case Owner" or anything synonymous. The closest real concept is a `Case
Manager` custom field (confirmed present and populated, e.g. `"Lucnise Regis"`), or the case's
`originating_lawyer` staff flag (structurally available, same mechanism as `lead_lawyer`/Assigned
Attorney). Decision made: leave it out rather than silently substitute one of these guesses.

**Processing Agent normalization — implemented via `_normalize_agent_name()` + `_AGENT_ALIAS_MAP`
in `mycase_rest.py`.** Blank/None → `"(unassigned)"`; otherwise whitespace is collapsed and the
value is trimmed, then checked against a small curated alias map for known duplicate spellings.
Deliberately NOT automatic fuzzy-matching — a live investigation this session (see the `search_cases`
custom-field follow-up above) found a real case where a staff member's PARENTHETICAL NICKNAME in an
unrelated custom field coincidentally matched another person's first name; the same risk applies to
agent-name fuzzy-matching (two different real people with similar names could get silently merged).
The alias map is empty by default and is the extensibility point — add confirmed duplicate spellings
to it as they're found in real data, never guess them upfront. The raw value is always preserved
alongside the cleaned one (`"PROCESSING AGENT (original)"`).

**Verified:** unit-tested (mocked) client_name/assigned_attorney resolution, agent
clean/original/unassigned handling, and all three date-range filter pairs — all correct. Re-verified
live against the real account: a real Immigration case correctly returned `client_name: "IBRA SEYE,
MARIAMA CHORR"`, `assigned_attorney: "LANA JOSEPH"`, `CASE TYPE: "Asylum"`, and both PROCESSING AGENT
columns populated. 53 tools still register correctly.

---

## Known limitations / not done

- **Write operations** (create/update/delete — roughly the other half of MyCase's API surface,
  ~50 more endpoints) are not implemented. This agent can look up data but cannot change anything
  in MyCase.
- **No live end-to-end OAuth round-trip has been verified from this environment** (requires a
  real MyCase login in a browser) — the endpoint URLs and request/response shapes are confirmed
  correct against MyCase's own docs and live 401 testing, but the full "click Connect → log in →
  land back connected" path should be exercised by the user.
- **A manually-pasted Access Token alone still doesn't auto-refresh** — if you skip Connect and
  paste only an access token, it silently stops working after 24h. **Fixed to the extent
  possible without OAuth login:** Settings → MyCase now also has a Refresh Token field — paste
  both (MyCase issues them together) and `_get_valid_token()` will proactively refresh using it
  (previously, this fell into a dead code path: expiry unknown + a refresh token present used to
  incorrectly short-circuit to "just reuse the pasted token forever" without ever attempting a
  refresh — fixed so an unknown-expiry-but-refresh-token-available case now refreshes once to
  establish a real expiry, then behaves normally). There's still no proactive UI
  warning/countdown before expiry.
- **`/llm/models` is slow (~5s)** — it queries all 7 LLM providers sequentially rather than in
  parallel. Not MyCase-specific (shared with the Podio agent's model dropdown), but noted here
  since it was observed while debugging MyCase Agent model selection. Not yet fixed.

---

## Viewing results: table + CSV export

**There is no tool-call/step-card UI at all.** Tool calls (names, arguments, individual
success/error) are a purely internal implementation detail now — the chat only ever shows the
model's reply text and, when relevant, the real result data. This was a deliberate redesign: a
raw "N tool calls" panel with expandable args/JSON was confusing to a non-technical user and
actively duplicated what the clean result table already shows. The one exception: if a tool call
failed, that failure is still visible — but through the model's own plain-English explanation
(required by the system prompt), not a technical error panel.

**Result rendering (`primaryResultGroups` in `mycase-agent/page.tsx`):** every non-reference tool
result that carries real record data becomes a table — a proper `items[]` array as-is, OR a
single-object result (`get_case`, `get_client`, ...) wrapped as a one-row table, so "get case X"
gets a real table just as much as "list all cases" does (an empty `items: []` — e.g. a search
that legitimately matched nothing — correctly renders nothing, not a fake row built from the
search's own metadata; that was a real bug, now fixed). Results are merged by **canonical
resource**, not raw tool name — `get_case`/`get_cases`/`search_cases`/`get_client_cases`/
`find_cases_with_documents` all fold into one "Cases" table (deduped by row id), so a targeted
`search_cases` hit followed by a fuller `get_case` fetch of that same record becomes ONE complete
row, not two near-duplicate tables. There is no user-message-wording gate anymore either (an
earlier "only show a table if the user said 'show'/'list'" heuristic was removed — it incorrectly
suppressed legitimate single-record answers like "get case X and include the client's email").
Reference/config tool results (case stages, case roles, practice areas, locations, referral
sources, people groups, custom field definitions) are deliberately excluded from ever becoming an
auto-table — they're small config lists, not case data, and the model is instructed to just print
their actual values in text instead.

Columns are the union of every key across all rows in that resource (so a mixed-shape result set
still shows every column), and a **Download CSV** button next to each table exports that exact
data client-side (`frontend/lib/recordTable.ts`), no backend involved. Nested objects (e.g. a
case's expanded `clients` array) flatten to a human label — name first, then email appended in
angle brackets when present (`Patricio Gonzalez <patricio@example.com>`) — rather than raw JSON,
so an expanded client's email is never silently lost just because it's shown inside the Cases
table's `clients` column instead of its own separate table.

**Sparse fieldsets — the one real gotcha:** `field[client]` on `get_cases`/`get_case` supports a
real list of sub-fields (id, first_name, last_name, email, phone numbers, etc.) — this is the
PREFERRED way to get a case's client name/email in one call (`field_client=
"id,first_name,last_name,email"`), rather than a separate `get_client(id)` call (which works too,
but produces a second, separate table). `field[custom_field]` supports **only** `id,field_type` —
no `name`, no `value`. This is easy to guess wrong (the model tried `id,name,value` and got a 400)
because `value` sounds like it should be there — it's already included by default on every
`custom_field_values` entry, no expansion needed. To resolve a custom field's *name* (e.g. "Case
Type"), call `get_custom_fields()` once (walking every page — the default page_size is 25, and a
firm with more custom fields than that will silently miss some on an unpaginated call) and match
its `id` against `custom_field_values[].custom_field.id` on each record. `aggregate_cases` does
this resolution automatically and breaks each custom field into its own named column.

**Downloads render as a real button, not a link the user must click/copy.** `download_document`/
`download_document_version` results are excluded from the record-table logic (a raw `download_url`
used to be able to leak into a one-row table as a giant unreadable URL column) and instead render
as a dedicated `DownloadCard` — filename (parsed from the URL's own
`response-content-disposition`, falling back to `Document <id>`), the real expiry
(`expires_in` — 1 minute for a document's current version, ~1 hour for a specific version, sourced
from the tool result itself, not the model's text), and a styled `<a>` "Download" button
(`target="_blank"`, opens the presigned URL directly — no backend proxy, bytes never transit the
LLM). The system prompt tells the model NOT to paste the raw URL as a markdown link, but that
alone wasn't reliable — a real transcript showed the model pasting `[Download Document](https://
s3.amazonaws.com/...)` into its reply anyway. Backstopped deterministically in
`mycase_agent.py`'s `_strip_download_link_urls()`: every successful `download_document`/
`download_document_version` URL from the turn is stripped out of the final reply text (markdown
link → just its label; a bare occurrence → removed outright) before the reply is returned, matched
on an exact string OR the same scheme+host+path with a different query string (covers the model
subtly retyping/mangling the long presigned URL). So even when the model ignores the prompt
instruction, the raw URL never reaches the chat — only the button (built straight from the real
tool result) can trigger the download.

**"Show/list all documents for case X" → `get_case_documents(case_id)`, not folder-walking.** Per
MyCase's own docs this endpoint returns EVERY document for the case, complete, in one call,
regardless of which folder/subfolder it's filed in. The model previously defaulted to manually
walking `get_case_folder → get_folder_subfolders → get_folder_documents` instead (likely because
the old tool description said "directly associated," which read as "root folder only, might miss
subfolders" even though that's not true) — that path is more complex, error-prone, and produced
false "this case has no documents" answers. Folder-walking (or now `get_case_folder_tree`, which
does the whole recursive walk in one call) is reserved for genuine folder-STRUCTURE requests, not
for listing documents.

---

## Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| "MyCase connected" badge shows, but every call 401s | The stored access token is expired. If it was pasted manually (no refresh token), this is expected after 24h — get a fresh token or switch to Connect MyCase. Decode the JWT's `exp` claim (base64, no signature verification needed) to confirm before assuming it's a code bug. |
| Access token "disappeared" after a restart | A backend restart never touches the database — if a token that was definitely saved is gone, check `integration_settings.updated_at`/`updated_by` for that key; an `updated_by` of NULL/blank means it was cleared programmatically (e.g. a test script), not via the Settings UI. Client ID/Secret/Redirect URI are separate rows and unaffected by an access-token wipe. |
| "Connect MyCase" redirects to a MyCase login page asking for username/password | Expected — this is the OAuth Authorization Code grant working correctly. Verify the URL bar shows `auth.mycase.com` before entering credentials, then log in with a real MyCase account. |
| Connect flow fails right after login with a Forbidden-style error | The authorizing MyCase user likely lacks the "Manage your firm's preferences, billing, and payment options" permission (required per MyCase's own docs to authorize external apps). |
| `GET /integrations/mycase/connect` returns `{"success": false, ...}` | No `mycase_client_id` configured yet — add it in Settings → MyCase first. |
| Reply is literally `"All connection attempts failed"`, especially on a long/complex multi-tool-call query | Not a MyCase-specific issue — a transient network blip during one of the underlying LLM provider calls (`httpx.ConnectError`'s exact message). Root-caused and fixed in `model_gateway.py` (Session 26): all 4 provider chat paths now retry transient transport-level failures up to 3 times (1s/2s/4s backoff) before giving up. Longer/more complex queries make many more LLM round-trips, so they were disproportionately likely to hit this before the fix — nothing wrong with the query, token, or MyCase connection itself. |
| A count/total in the reply doesn't match the actual table/CSV, or doesn't match a repeated question | Was a truncation bug (pagination metadata getting cut off, causing the model to fabricate a number) — see "Pagination correctness" above. Fixed; if it recurs, check whether a NEW tool built a raw dict with `items` listed before its own metadata keys, since that's the exact pattern that caused it. |
| "Get invoices/cases/etc created|due|dated on X" returns a table with extra rows from unrelated dates, even when the reply text sounds right | MyCase's only server-side date filter is `updated_after` — see "MyCase's ONE date filter" above. For invoices, fixed via `get_invoices_by_date`. If the same shape shows up for another resource's own date field (case opened/closed date, expense/time-entry date, event time, task due_date, document created_at), it needs the same dedicated fetch-then-filter tool — `get_invoices_by_date` in `mycase_rest.py` is the template to copy. |
| A whole-firm question ("how many clients", "list all leads") looks suspiciously specific/wrong, or a table just doesn't appear at all | Was a read-hallucination gap — the model stating a confident number with no real tool call behind it. See "Anti-hallucination measures" → the `_unverified_resource_claims` guard. If it still happens, check the logs for `mycase_agent_bad_reply_discarded` (`reason=unverified_claim`) to see whether the guard fired and the model just kept refusing to call the real tool across all `_MAX_STEPS` retries. |
| The reply says "see the table above" | Should not happen anymore — the reply always renders BELOW the result table in the chat layout, and the system prompt was corrected throughout to say "below". If this resurfaces, grep `mycase_agent.py`'s `_SYSTEM_PROMPT` and any generated pointer text for a stray "above". |
| `Mistral API 400: "Assistant message must have either content or tool_calls, but not none"` | A genuinely empty model turn got appended to conversation history and later re-sent. Fixed — see "Anti-hallucination measures" above (the `"(no response)"` placeholder fix, applied to both this agent and the Podio agent). |
| `search_cases` for an exact case number returns extra, unrelated cases | Was matching the query as a substring against BOTH case_number and name — a case's display *name* often embeds a padded number too (e.g. name "01597-Smith" for an unrelated case), which could coincidentally contain the searched digits. Fixed: an exact case_number match now always wins over any substring hit. |
| "Get me N cases that have documents" returns cases that don't actually have any | Was answered by sampling the first few cases from `get_cases` and hoping some had documents — wrong by construction. Use `find_cases_with_documents(limit=N)` instead (see "The 51 tools" above), which scans real documents and ranks cases by actual document_count. |
| Raw pydantic validation dump reaches the user (e.g. `"1 validation error for search_casesArguments\nquery\n  Field required..."`) | `mycase_client.py`'s `call_tool()` now catches this class of error (a `ToolError` wrapping a pydantic `ValidationError`, raised when the model calls a tool with a missing/invalid required argument) and reformats it into a plain sentence, e.g. `"Invalid arguments for search_cases: 'query' is required but was not provided."` If a NEW raw dump shape reaches the user, it's likely a different exception type than `ToolError`/`ValidationError` — extend the catch in `mycase_client.py`. |
| Clicking a "download document" link shows an XML page: `<Error><Code>InvalidRequest</Code><Message>Request specific response headers cannot be used for anonymous GET requests.</Message>...</Error>` | The link EXPIRED — not a bug in the link itself. Per MyCase's own docs, `download_document`'s signed URL is valid only **~1 minute** (`download_document_version` is ~1 hour); once expired, MyCase's S3-backed storage returns this specific (confusingly-worded) error instead of a plain "expired" message. Was previously made worse by the model hallucinating a longer, wrong expiry (e.g. "expires in 10 minutes") when relaying the link to the user, despite the correct duration already being in the tool description — models don't reliably surface a fact from a schema description read once amid 50+ tools. Fixed in `mycase_rest.py`'s `_download()`: the real `expires_in` and an explanation of this exact error are now returned INSIDE the tool result itself (fresh in context at reply time, not just the description), and the system prompt instructs the model to quote that value verbatim and tell the user to click immediately. If it still expires before the user can click, just ask the agent to generate a fresh link. |
