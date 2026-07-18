# MyCase Agent

A second, independent chat agent in Syndrix — alongside the Podio Agent — that lets BD/Admin
users query MyCase (legal practice management software) in plain language. Built Sessions
23–25, heavily hardened in a long reliability-focused session afterward (search/aggregation
tools, pagination correctness, hallucination guards, UI redesign — all summarized below); see
`CLAUDE.md` for the full narrative session log. This file is the standalone reference for the
feature itself.

**Scope: read-only.** 46 real GET/download endpoints plus 5 deterministic tools built on top of
them (`aggregate_cases`, `search_cases`, `find_cases_with_documents`, `get_case_folder_tree`,
`lookup_utbms_code`) — **51 tools total**. Create/update/delete operations are not implemented.

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

## The 51 tools

46 real MyCase GET/download endpoints + 1 static reference lookup + 4 deterministic
search/aggregation/traversal tools, grouped by resource. Full per-endpoint schemas (params,
response fields) are documented as docstrings in `app/mcp_servers/mycase.py`; this is just the
inventory:

**Cases:** `get_cases`, `get_case`, `get_client_cases`, `get_case_folder`, `get_case_folder_tree`, `get_case_documents`, `get_case_notes`
**Case config:** `get_case_stages`, `get_case_roles`
**Clients (people):** `get_clients`, `get_client`, `get_client_notes`, `get_client_message_threads`
**Companies:** `get_companies`, `get_company`
**Leads:** `get_leads`, `get_lead`
**Custom fields:** `get_custom_fields`, `get_custom_field`, `get_custom_field_list_options`
**Documents:** `get_documents`, `get_document`, `get_document_versions_all`, `get_document_versions`, `download_document`, `download_document_version`
**Folders:** `get_folder_documents`, `get_folder_subfolders`
**Calendar:** `get_events`
**Billing:** `get_expenses`, `get_expense`, `get_invoices`, `get_invoice_payments`, `get_time_entries`, `get_time_entry`
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
  substring), group-by-and-count (builtin field or custom field). Returns one flattened row per
  surviving case with EVERY field (including each custom field broken into its own named column,
  not a nested blob), plus that row's `group_name`/`case_count`. Use for ANY "count/group/
  breakdown of cases by X" or "cases where stage is X" request.
- **`search_cases`** — find case(s) by case_number or name. MyCase has no server-side filter for
  either. Prioritizes an EXACT case_number match over a substring hit (a case's display *name*
  often embeds a padded number too, e.g. "01597-Smith", which used to cause an unrelated case to
  match and pollute the result — fixed).
- **`find_cases_with_documents`** — "give me N cases that have documents": scans every document
  firm-wide (each document's `case` field is `{"id": N}` per MyCase's real schema), tallies real
  document counts per case, returns the N with the most. Replaces an earlier broken approach that
  just sampled the first few cases from `get_cases` and hoped some had documents.
- **`get_case_folder_tree`** — a case's ENTIRE folder structure (root + every subfolder,
  recursively, to `max_depth`) with each folder's documents, in one call. For the document LIST
  itself (not structure), `get_case_documents` is simpler and already complete — this is only for
  "what does the folder structure look like" requests.

For local Ollama models, the tool list is capped at 16 (keyword-priority matched against the
user's message, mirrors the Podio agent's same pattern) — cloud models get the full 51.

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
| A whole-firm question ("how many clients", "list all leads") looks suspiciously specific/wrong, or a table just doesn't appear at all | Was a read-hallucination gap — the model stating a confident number with no real tool call behind it. See "Anti-hallucination measures" → the `_unverified_resource_claims` guard. If it still happens, check the logs for `mycase_agent_bad_reply_discarded` (`reason=unverified_claim`) to see whether the guard fired and the model just kept refusing to call the real tool across all `_MAX_STEPS` retries. |
| The reply says "see the table above" | Should not happen anymore — the reply always renders BELOW the result table in the chat layout, and the system prompt was corrected throughout to say "below". If this resurfaces, grep `mycase_agent.py`'s `_SYSTEM_PROMPT` and any generated pointer text for a stray "above". |
| `Mistral API 400: "Assistant message must have either content or tool_calls, but not none"` | A genuinely empty model turn got appended to conversation history and later re-sent. Fixed — see "Anti-hallucination measures" above (the `"(no response)"` placeholder fix, applied to both this agent and the Podio agent). |
| `search_cases` for an exact case number returns extra, unrelated cases | Was matching the query as a substring against BOTH case_number and name — a case's display *name* often embeds a padded number too (e.g. name "01597-Smith" for an unrelated case), which could coincidentally contain the searched digits. Fixed: an exact case_number match now always wins over any substring hit. |
| "Get me N cases that have documents" returns cases that don't actually have any | Was answered by sampling the first few cases from `get_cases` and hoping some had documents — wrong by construction. Use `find_cases_with_documents(limit=N)` instead (see "The 51 tools" above), which scans real documents and ranks cases by actual document_count. |
| Raw pydantic validation dump reaches the user (e.g. `"1 validation error for search_casesArguments\nquery\n  Field required..."`) | `mycase_client.py`'s `call_tool()` now catches this class of error (a `ToolError` wrapping a pydantic `ValidationError`, raised when the model calls a tool with a missing/invalid required argument) and reformats it into a plain sentence, e.g. `"Invalid arguments for search_cases: 'query' is required but was not provided."` If a NEW raw dump shape reaches the user, it's likely a different exception type than `ToolError`/`ValidationError` — extend the catch in `mycase_client.py`. |
