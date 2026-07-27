# The MyCase Agent — How It Works

*A guide for firm administrators and users*

---

## 1. What Is the MyCase Agent?

The MyCase Agent is a chat assistant built into Syndrix that lets your team ask questions about your firm's own MyCase data in plain English — no filters, no report builders, no exporting spreadsheets by hand.

Instead of clicking through MyCase's screens to find "which cases have no assigned attorney" or "which invoices are still unpaid," you simply type the question. The assistant looks up the real answer directly from your firm's MyCase account and shows it to you as a table, with a one-click CSV export.

**It is read-only.** The MyCase Agent can look up, search, filter, and summarize your firm's cases, clients, companies, invoices, documents, notes, tasks, and billing records. It **cannot** create, edit, or delete anything in MyCase. If you ask it to change something, it will tell you plainly that it can't — it will never pretend to.

Access is limited to your firm's Business Development and Admin team members.

---

## 2. How a Question Gets Answered

Every question you ask goes through the same series of steps:

1. **You type a question** in the chat — e.g., *"How many open cases do we have with no Lead Attorney assigned?"*
2. **The system checks who's asking.** Only signed-in users with the right role can reach the MyCase Agent at all, and it confirms your firm's MyCase connection is active before doing anything else.
3. **The AI decides what to look up.** It reads your question and chooses the appropriate lookup — e.g., "search cases," "get invoices," "get case documents" — based only on what real capabilities are available. It does not decide the *answer*, only which real data to go fetch.
4. **The real lookup runs against your live MyCase account.** This is a direct, live query — never a guess, never something recalled from the AI's general training. If your question involves counting, filtering, or grouping across many records (e.g., "how many," "top 10," "grouped by X"), that counting is done by exact program logic, not by the AI reading through a list and estimating — this is the single most important design decision behind the whole system, described further in Section 4.
5. **The AI writes a short plain-English summary.** Something like *"Found 23 cases — see the table below."*
6. **The data table is built directly from the real results** — independent of whatever the summary text says. What you see in the table is never "the AI's version of the data"; it's the actual data, rendered as-is.
7. **Before you ever see the reply, it passes through a series of automated accuracy checks** (Section 4). If a check finds a problem, the system either fixes it automatically or has the AI try again — you only see the final, checked answer.

The whole exchange — your question, which lookups ran, and the final answer — is logged for quality monitoring (Section 6).

---

## 3. Security Measures

| Measure | What it means for you |
|---|---|
| **Role-based access** | Only users with Business Development or Admin permissions can open the MyCase Agent at all — enforced on the server, not just hidden in the interface. |
| **Authenticated sessions** | Every request is tied to a signed-in user session. There's no anonymous or shared access. |
| **Secure MyCase connection (OAuth)** | The assistant never sees or stores your MyCase username or password. It uses a scoped access token, generated through MyCase's own official login flow, which you can revoke at any time from Settings. |
| **Read-only by design** | Because the MyCase Agent has no ability to create, edit, or delete records, there is no risk of it accidentally changing or damaging your MyCase data — the worst it can do is answer a question incorrectly, and Section 4 covers how that risk is minimized. |
| **Your firm's data only** | The assistant only ever queries the MyCase account your firm connected. It has no access to any other firm's data. |
| **Protected against manipulated content** | Case notes, documents, and other free-text fields are sometimes written by clients or third parties. The assistant is explicitly built to treat *all* content coming from your MyCase records as information to report on — never as an instruction to follow — so a note containing text that looks like a command (accidentally or deliberately) cannot redirect the assistant's behavior. |
| **Stays on topic** | The assistant will only answer questions about your firm's MyCase data. It's built to decline general-knowledge or unrelated questions rather than answer them using outside knowledge, keeping every interaction relevant and auditable. |
| **Token expiry** | The MyCase connection token expires periodically and must be refreshed, limiting how long any single access credential remains valid. |

---

## 4. Measures Against AI "Hallucination"

AI assistants are well known for occasionally stating something confidently and incorrectly — this is usually called "hallucination." Here is what actually happens, using one real example, to show exactly where each safeguard steps in.

> **Example question:** *"How many open cases do we have?"*

**What a plain, unprotected AI chatbot would do:** skim through some of the case records it can see, and guess — *"looks like around 200"*. If your firm actually has 2,847 open cases, that guess is confidently wrong, and nothing would tell you so.

**What the MyCase Agent does instead, step by step:**

1. **The counting is never done by the AI.** The AI's only job is to recognize "this is a counting question" and trigger the real lookup. The actual count — every single matching record, not a sample — is computed by exact program logic against your live MyCase data. The number that comes back, say 2,847, is always the real number, not an estimate. 
2. **The table and the written answer come from the same real result.** The table listing all 2,847 cases isn't the AI's recollection or summary of the data — it's built directly from that same real count. The AI writes a short sentence ("Found 2,847 cases"), but it doesn't get to invent the table separately.
3. **An automatic check compares the sentence to the table.** Immediately before you see the answer, the system checks: does "2,847" in the written sentence actually match the number of rows in the table? If the AI's wording is ever off — even by a formatting slip — this is caught and either corrected automatically or sent back to the AI to redo, so you never see a sentence that disagrees with its own table.
4. **A few more automatic checks run alongside that one:** Did the AI actually make a real lookup before stating a fact (versus just answering from memory)? Does the written answer contradict its own results (e.g., saying "no invoices" when invoices were actually found)? Was a failed lookup ever reported as if it had succeeded? All three are checked on every single reply, not just on request.
5. **Optionally, a second, independent AI double-checks the finished answer** against the real data one more time before it reaches you — an extra layer on top of the automatic checks above, available if your firm wants it turned on.
6. **Known tricky questions are kept on file and re-tested automatically.** Any time the system is updated, a set of real past questions — including ones that once caused a mistake during development — gets re-run to confirm the old mistake hasn't quietly come back.

**The short version:** the AI is only ever responsible for understanding your question and writing a short sentence about a result it did not compute itself. All counting, filtering, and matching is done by exact code against your real data, and an automatic check confirms the AI's sentence and the real data agree before anything reaches your screen.

---

## 5. What It Can and Can't Do — At a Glance

**It can:**
- Answer questions about cases, clients, companies, invoices, documents, notes, tasks, calendar events, and billing/time-entry data.
- Filter, group, count, and rank across your firm's entire dataset accurately, no matter how large.
- Export any result as a CSV with one click.
- Explain what a billing code (UTBMS/LEDES) means.

**It cannot (by design):**
- Create, edit, or delete anything in MyCase.
- Answer questions unrelated to your firm's MyCase data.
- See or access another firm's MyCase account.
- Report on a small number of fields that MyCase itself does not expose through its system (the assistant will say so plainly rather than guess).

---

## 6. Monitoring and Continuous Improvement

Every conversation is logged to a private, access-controlled monitoring dashboard that records which lookups ran, how long each step took, which AI model answered, and whether the automatic accuracy checks (Section 4) passed or caught something. This isn't visible to end users during normal use — it exists so the team supporting the MyCase Agent can spot a problem quickly, understand exactly what happened, and confirm a fix actually works before it's relied on again.

---

## 7. Data Privacy — What Happens to Your Data

Using the same example — *"How many open cases do we have?"* — here is exactly where your data goes, in order:

1. **Your typed question** goes to the AI model your firm has chosen in Settings (for example Anthropic's Claude, Google's Gemini, or OpenAI's GPT — whichever provider your firm picked).
2. **The AI requests a real lookup**, so our system fetches the matching records — case names, client names, dollar amounts, whatever the question needs — directly from **your firm's own live MyCase account**.
3. **That fetched MyCase data is then also sent to the same AI provider**, so it can write an accurate summary of it. This step is unavoidable for *any* AI assistant, in any product — the AI can only describe data it's actually been shown.
4. **The AI provider processes your question and data on their own servers** and sends back the written answer. From that point on, what happens to that data is governed by that AI provider's own privacy policy — not something this application controls.
5. **We keep a copy of the chat conversation** so you can scroll back through your own history, but we do not build any separate, permanent copy of your MyCase database anywhere else.
6. **The MyCase Agent never writes back to MyCase** — it only ever reads. There is no path, at any step above, for it to alter your source records.

**The one fact worth remembering:** whichever AI provider your firm selects *does* see the real case and client data relevant to each question, the same way it would if a staff member pasted that data into that provider's own chat tool directly. If your firm has data-handling requirements around client information, that's the point in the flow to evaluate against your chosen AI provider's own privacy/data-retention terms — we're happy to walk through which provider fits your requirements best.

---

## 8. Summary

The MyCase Agent is designed so the parts most likely to go wrong with a typical AI assistant — miscounting, guessing, or drifting off-topic — are handled by exact, testable program logic instead of being left to the AI's judgment, with the AI's role limited to understanding your question, choosing the right real lookup, and writing a short, checked summary of real results. Combined with role-based access, a read-only connection to MyCase, and multiple layers of automatic verification, it's built to be a reliable, low-risk way for your team to get real answers from your firm's own data.
