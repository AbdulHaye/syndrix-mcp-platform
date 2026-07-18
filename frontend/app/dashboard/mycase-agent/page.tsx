"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Topbar from "@/components/Topbar";
import {
  runMyCaseAgent,
  getMyCaseStatus,
  startMyCaseConnect,
  disconnectMyCase,
  listLlmModels,
  setLlmModel,
  listMyCaseChatSessions,
  getMyCaseChatSession,
  saveMyCaseChatSession,
  deleteMyCaseChatSession,
} from "@/lib/api";
import { getAuth } from "@/lib/auth";
import { useToast } from "@/lib/toast";
import Markdown from "@/components/Markdown";
import { deriveColumns, flattenValue, itemsToCsv, downloadCsv, type RecordRow } from "@/lib/recordTable";
import type { MyCaseChatMessage, MyCaseAgentStep, MyCaseChatSessionSummary } from "@/types";

/** Plain resource-noun heading for a results table — deliberately NOT a tool/action
 * name (no "Get"/"Search"/"Aggregate" verb): the user should see "Cases", not the
 * mechanics of which internal call produced them. */
function resourceLabel(tool: string): string {
  const stripped = tool.replace(/^(get|search|aggregate)_/, "");
  return stripped.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

const EXAMPLES = [
  "Show me all open cases",
  "List my clients",
  "What are my upcoming calendar events?",
];

function RecordsTable({ toolName, items }: { toolName: string; items: RecordRow[] }) {
  const columns = deriveColumns(items);

  function onDownload() {
    const csv = itemsToCsv(items, columns);
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    downloadCsv(`${toolName}-${stamp}.csv`, csv);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
      <div className="d-flex align-items-center justify-content-between">
        <span style={{ fontSize: "0.72rem", color: "#64748b", fontWeight: 600 }}>
          {items.length} record{items.length !== 1 ? "s" : ""} · {columns.length} column{columns.length !== 1 ? "s" : ""}
        </span>
        <button
          onClick={onDownload}
          style={{
            display: "inline-flex", alignItems: "center", gap: 4,
            padding: "0.25rem 0.6rem", background: "#0b6fcc", color: "#fff",
            borderRadius: 6, fontSize: "0.7rem", fontWeight: 600,
            border: "none", cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0,
          }}
        >
          <i className="bi bi-download" />
          Download CSV
        </button>
      </div>
      <div style={{ maxHeight: 340, overflow: "auto", border: "1px solid #e2e8f0", borderRadius: 8, minWidth: 0 }}>
        <table style={{ borderCollapse: "collapse", fontSize: "0.66rem", width: "100%" }}>
          <thead style={{ position: "sticky", top: 0, background: "#f1f5f9", zIndex: 1 }}>
            <tr>
              {columns.map((col) => (
                <th
                  key={col}
                  style={{
                    textAlign: "left", padding: "0.4rem 0.6rem", fontWeight: 700,
                    color: "#334155", whiteSpace: "nowrap", borderBottom: "1px solid #e2e8f0",
                  }}
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((item, i) => (
              <tr key={i} style={{ borderBottom: "1px solid #f1f5f9" }}>
                {columns.map((col) => (
                  <td
                    key={col}
                    style={{
                      padding: "0.35rem 0.6rem", color: "#1e293b",
                      whiteSpace: "nowrap", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis",
                    }}
                    title={flattenValue(item[col])}
                  >
                    {flattenValue(item[col])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Tools that only exist to resolve names -> ids/labels for another call (case
// stages, custom fields, practice areas, etc.) — never what the user actually asked
// to see, so they're excluded from the primary result table(s) shown under a reply
// and only appear if the "show all tool calls" debug view is opened.
const _REFERENCE_TOOLS = new Set([
  "get_case_stages", "get_case_roles", "get_practice_areas", "get_locations",
  "get_referral_sources", "get_people_groups", "get_custom_fields",
  "get_custom_field_list_options",
]);

function stepResult(step: MyCaseAgentStep) {
  return step.result as
    | { success?: boolean; error?: string; items?: unknown[]; item_count?: number; [key: string]: unknown }
    | undefined;
}

function stepFailed(step: MyCaseAgentStep): boolean {
  const r = stepResult(step);
  return r?.success === false || !!r?.error;
}

/** Every non-reference tool result that carries real record data becomes table
 * rows — a proper array (get_cases/search_cases/...) as-is, OR a single-object
 * result (get_case/get_client/...) wrapped as a one-row table, so "get case X" is
 * shown just as much as "list all cases" is. Only success/error/pagination-meta
 * keys are stripped; a result with nothing left after that (e.g. just a download
 * URL) is correctly left to the model's own prose instead of a fake table. */
function stepItems(step: MyCaseAgentStep): RecordRow[] | null {
  if (stepFailed(step)) return null;
  const r = stepResult(step);
  if (!r) return null;
  if (Array.isArray(r.items)) {
    // A genuine list/search/report result (get_X, search_X, aggregate_X) — even
    // when it legitimately found zero matches, do NOT fall through to the
    // single-object wrapper below: that would render the search's OWN metadata
    // (query, cases_scanned, match_count, ...) as if it were a fake "1 result" row
    // instead of correctly showing nothing.
    if (r.items.length > 0 && r.items.every((it) => it !== null && typeof it === "object")) {
      return r.items as RecordRow[];
    }
    return null;
  }
  const { success: _success, error: _error, item_count: _itemCount, next_page_token: _npt, ...rest } =
    r as Record<string, unknown>;
  return Object.keys(rest).length >= 2 ? [rest as RecordRow] : null;
}

function rowId(item: RecordRow): unknown {
  return item.id !== undefined && item.id !== null ? item.id : (item as { case_id?: unknown }).case_id;
}

// Several tools return the same underlying resource under different names
// (get_case / get_cases / search_cases / get_client_cases all return CASE rows) —
// keying results by this canonical resource instead of the raw tool name merges
// them into one table (deduped by row id) instead of several near-duplicate ones,
// e.g. a targeted search_cases match plus a fuller get_case fetch of that same case.
const _RESOURCE_ALIASES: Record<string, string> = {
  get_case: "cases", get_cases: "cases", search_cases: "cases", get_client_cases: "cases",
  find_cases_with_documents: "cases",
  get_client: "clients", get_clients: "clients",
  get_company: "companies", get_companies: "companies",
  get_lead: "leads", get_leads: "leads",
  get_document: "documents", get_documents: "documents", get_case_documents: "documents",
  get_folder_documents: "documents",
  get_case_folder: "folders", get_case_folder_tree: "folders", get_folder_subfolders: "folders",
  get_invoice: "invoices", get_invoices: "invoices",
  get_expense: "expenses", get_expenses: "expenses",
  get_time_entry: "time_entries", get_time_entries: "time_entries",
  get_note: "notes", get_case_notes: "notes", get_client_notes: "notes",
};

function resourceKey(tool: string): string {
  return _RESOURCE_ALIASES[tool] ?? tool;
}

/** Group this turn's successful, non-reference tool calls by resource, merging
 * (deduped by row id) any paginated/aliased calls to the same resource into one
 * complete set — so "list all cases" across 4 pages, or search_cases + get_case for
 * the same record, becomes ONE table + ONE CSV button instead of several
 * easy-to-confuse partial ones. Order follows first appearance. Deliberately does
 * NOT break a case's own nested clients/staff/custom-fields out into separate
 * tables — that fragmented one lookup into 5 boxes; those stay as flattened cell
 * values within the one row instead (see flattenValue in lib/recordTable). */
function primaryResultGroups(steps: MyCaseAgentStep[]): { tool: string; items: RecordRow[] }[] {
  const order: string[] = [];
  const byKey = new Map<string, Map<unknown, RecordRow>>();

  for (const step of steps) {
    if (_REFERENCE_TOOLS.has(step.tool)) continue;
    const items = stepItems(step);
    if (!items) continue;
    const key = resourceKey(step.tool);
    if (!byKey.has(key)) {
      byKey.set(key, new Map());
      order.push(key);
    }
    const bucket = byKey.get(key)!;
    for (const item of items) {
      const id = rowId(item);
      bucket.set(id !== undefined && id !== null ? id : Symbol(), item);
    }
  }
  return order.map((tool) => ({ tool, items: Array.from(byKey.get(tool)!.values()) }));
}

// No per-tool-call UI at all — tool calls are purely a backend implementation
// detail. Only the model's reply text and the actual result data (below) are ever
// shown; failures are reported through the model's own prose (CORE RULES already
// require it to state failures plainly), not a separate technical error panel.
function MessageBubble({ msg }: { msg: MyCaseChatMessage }) {
  const isUser = msg.role === "user";
  const steps = msg.steps ?? [];
  const resultGroups = isUser ? [] : primaryResultGroups(steps);

  return (
    <div className={`d-flex ${isUser ? "justify-content-end" : "justify-content-start"}`} style={{ minWidth: 0 }}>
      <div style={{ maxWidth: "85%", display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
        <div
          style={{
            background: isUser ? "var(--primary)" : "white",
            color: isUser ? "white" : "#1e293b",
            border: isUser ? "none" : "1px solid #e2e8f0",
            borderRadius: 14,
            borderTopRightRadius: isUser ? 4 : 14,
            borderTopLeftRadius: isUser ? 14 : 4,
            padding: "0.6rem 0.9rem",
            fontSize: "0.85rem",
            lineHeight: 1.55,
            whiteSpace: isUser ? "pre-wrap" : "normal",
            overflowWrap: "anywhere",
          }}
        >
          {isUser ? msg.content || "" : msg.content ? <Markdown content={msg.content} /> : msg.error ? "Something went wrong." : ""}
        </div>

        {/* The actual result data — one table + one Download CSV button per
            resource (paginated calls to the same resource are merged). Reference/
            lookup calls (case stages, custom fields, etc.) are excluded so there's
            never more than one obvious download per dataset. */}
        {resultGroups.map((g) => (
          <div key={g.tool} style={{ border: "1px solid #e2e8f0", borderRadius: 10, background: "white", padding: "0.6rem 0.7rem", minWidth: 0 }}>
            <div style={{ fontSize: "0.66rem", fontWeight: 700, color: "#334155", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.03em" }}>
              {resourceLabel(g.tool)}
            </div>
            <RecordsTable toolName={g.tool} items={g.items} />
          </div>
        ))}
      </div>
    </div>
  );
}

function MyCaseConnection({ onConnectedChange }: { onConnectedChange?: (c: boolean) => void }) {
  const toast = useToast();
  const [connected, setConnected] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await getMyCaseStatus();
      setConnected(s.connected);
      onConnectedChange?.(s.connected);
    } catch {
      setConnected(false);
      onConnectedChange?.(false);
    }
  }, [onConnectedChange]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Re-check connection status whenever this tab regains focus — covers the case
  // where the user completed (or abandoned) the MyCase login in the new tab we
  // opened and then switched back here, without needing a manual page reload.
  useEffect(() => {
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [refresh]);

  async function connect() {
    setBusy(true);
    // Open the tab SYNCHRONOUSLY (before the await below) so browsers still treat
    // it as a direct result of the user's click — opening it only after the async
    // startMyCaseConnect() call resolves gets silently popup-blocked in most
    // browsers, since the async gap breaks the "user gesture" association.
    const popup = window.open("", "_blank");
    if (popup) popup.opener = null; // sever back-reference; we still hold our own handle
    try {
      const res = await startMyCaseConnect();
      if (res.success && res.authorize_url) {
        if (popup) {
          popup.location.href = res.authorize_url;
        } else {
          toast.error("Popup blocked — allow popups for this site, or we'll continue in this tab.");
          window.location.href = res.authorize_url;
        }
      } else {
        popup?.close();
        toast.error(res.error ?? "Could not start MyCase connection");
      }
    } catch (e: unknown) {
      popup?.close();
      toast.error(e instanceof Error ? e.message : "Connect failed");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await disconnectMyCase();
      await refresh();
      toast.info("Disconnected from MyCase");
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Disconnect failed");
    } finally {
      setBusy(false);
    }
  }

  if (connected === null) {
    return <span style={{ fontSize: "0.78rem", color: "#94a3b8" }}>Checking MyCase…</span>;
  }

  if (connected) {
    return (
      <div className="d-flex align-items-center gap-2">
        <span
          style={{ fontSize: "0.72rem", fontWeight: 600, color: "#166534", background: "#dcfce7", borderRadius: 20, padding: "3px 10px" }}
        >
          <i className="bi bi-check-circle-fill me-1" />
          MyCase connected
        </span>
        <button
          className="btn btn-sm"
          style={{ background: "white", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: "0.75rem", color: "#64748b" }}
          onClick={disconnect}
          disabled={busy}
        >
          Disconnect
        </button>
      </div>
    );
  }

  return (
    <button
      className="btn btn-sm"
      style={{ background: "#0b6fcc", color: "white", border: "none", borderRadius: 8, fontSize: "0.8rem", fontWeight: 600, whiteSpace: "nowrap" }}
      onClick={connect}
      disabled={busy}
      title="Requires a Client ID/Secret registered with MyCase support and configured in Settings → MyCase"
    >
      {busy ? (
        <><span className="spinner-border spinner-border-sm me-1" />Connecting…</>
      ) : (
        <><i className="bi bi-plug-fill me-1" />Connect MyCase</>
      )}
    </button>
  );
}

function ModelSelector() {
  const toast = useToast();
  const [ollama, setOllama] = useState<string[]>([]);
  const [google, setGoogle] = useState<string[]>([]);
  const [groq, setGroq] = useState<string[]>([]);
  const [mistral, setMistral] = useState<string[]>([]);
  const [openai, setOpenai] = useState<string[]>([]);
  const [anthropic, setAnthropic] = useState<string[]>([]);
  const [zai, setZai] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const res = await listLlmModels("mycase");
        setOllama(res.ollama);
        setGoogle(res.google);
        setGroq(res.groq ?? []);
        setMistral(res.mistral ?? []);
        setOpenai(res.openai ?? []);
        setAnthropic(res.anthropic ?? []);
        setZai(res.zai ?? []);
        setSelected(res.selected);
      } catch {
        /* ignore */
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function onChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const model = e.target.value;
    setSelected(model);
    try {
      await setLlmModel(model, "mycase");
      toast.success(`Model: ${model.split(":")[1] ?? model}`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to set model");
    }
  }

  const label = (m: string) => m.split(":")[1] ?? m;

  return (
    <div className="d-flex align-items-center gap-1" style={{ flex: "0 0 auto" }} title="LLM model the agent uses">
      <i className="bi bi-cpu" style={{ color: "#64748b", fontSize: "0.8rem" }} />
      <select
        className="form-select form-select-sm"
        value={selected}
        onChange={onChange}
        disabled={loading}
        style={{ fontSize: "0.78rem", borderRadius: 8, width: 190, flex: "0 0 auto" }}
      >
        {loading && <option>Loading models…</option>}
        {!loading && selected && ![...ollama, ...google, ...groq, ...mistral, ...openai, ...anthropic, ...zai].includes(selected) && (
          <option value={selected}>{label(selected)}</option>
        )}
        {ollama.length > 0 && (
          <optgroup label="Ollama (local)">
            {ollama.map((m) => <option key={m} value={m}>{label(m)}</option>)}
          </optgroup>
        )}
        {google.length > 0 && (
          <optgroup label="Google Gemini">
            {google.map((m) => <option key={m} value={m}>{label(m)}</option>)}
          </optgroup>
        )}
        {groq.length > 0 && (
          <optgroup label="Groq (cloud)">
            {groq.map((m) => <option key={m} value={m}>{label(m)}</option>)}
          </optgroup>
        )}
        {mistral.length > 0 && (
          <optgroup label="Mistral AI">
            {mistral.map((m) => <option key={m} value={m}>{label(m)}</option>)}
          </optgroup>
        )}
        {openai.length > 0 && (
          <optgroup label="OpenAI (GPT)">
            {openai.map((m) => <option key={m} value={m}>{label(m)}</option>)}
          </optgroup>
        )}
        {anthropic.length > 0 && (
          <optgroup label="Anthropic (Claude)">
            {anthropic.map((m) => <option key={m} value={m}>{label(m)}</option>)}
          </optgroup>
        )}
        {zai.length > 0 && (
          <optgroup label="Z.ai (GLM)">
            {zai.map((m) => <option key={m} value={m}>{label(m)}</option>)}
          </optgroup>
        )}
        {!loading && ollama.length === 0 && google.length === 0 && groq.length === 0 &&
          mistral.length === 0 && openai.length === 0 && anthropic.length === 0 && zai.length === 0 && (
          <option value="">No models found</option>
        )}
      </select>
    </div>
  );
}

function makeSessionId(): string {
  try {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  } catch {}
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function deriveTitle(msgs: MyCaseChatMessage[]): string {
  const firstUser = msgs.find((m) => m.role === "user");
  const line = (firstUser?.content ?? "").trim().split("\n")[0];
  if (!line) return "New chat";
  return line.length > 40 ? `${line.slice(0, 40)}…` : line;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.round(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.round(hr / 24)}d ago`;
}

function HistoryMenu({
  sessions, currentId, onSelect, onDelete,
}: {
  sessions: MyCaseChatSessionSummary[];
  currentId: string;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const sorted = sessions
    .filter((s) => s.message_count > 0)
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  return (
    <div style={{ position: "relative" }}>
      <button
        className="btn btn-sm"
        onClick={() => setOpen((o) => !o)}
        title="Chat history"
        style={{ background: "white", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: "0.78rem", color: "#475569", whiteSpace: "nowrap" }}
      >
        <i className="bi bi-clock-history me-1" />
        History
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 10 }} />
          <div
            className="card shadow"
            style={{ position: "absolute", right: 0, top: "calc(100% + 4px)", zIndex: 20, width: 280, maxHeight: 380, overflowY: "auto", padding: 4, borderRadius: 10 }}
          >
            {sorted.length === 0 && (
              <div className="text-muted text-center p-3" style={{ fontSize: "0.8rem" }}>No saved chats</div>
            )}
            {sorted.map((s) => (
              <div
                key={s.id}
                className="d-flex align-items-center gap-2 px-2 py-2"
                style={{ borderRadius: 8, cursor: "pointer", background: s.id === currentId ? "rgba(11,111,204,0.1)" : "transparent" }}
                onClick={() => { onSelect(s.id); setOpen(false); }}
              >
                <i className="bi bi-chat-left-text" style={{ color: "#94a3b8", fontSize: "0.8rem", flexShrink: 0 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "0.8rem", fontWeight: 600, color: "#1e293b", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {s.title || "New chat"}
                  </div>
                  <div style={{ fontSize: "0.68rem", color: "#94a3b8" }}>
                    {s.message_count} msg · {relativeTime(s.updated_at)}
                  </div>
                </div>
                <button
                  className="btn btn-sm p-1"
                  title="Delete chat"
                  onClick={(e) => { e.stopPropagation(); onDelete(s.id); }}
                  style={{ border: "none", background: "transparent", color: "#cbd5e1", lineHeight: 1 }}
                >
                  <i className="bi bi-trash" style={{ fontSize: "0.75rem" }} />
                </button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default function MyCaseAgentPage() {
  const auth = getAuth();
  const role = auth?.role ?? "dev";
  const allowed = role === "bd" || role === "admin";
  const toast = useToast();

  const [messages, setMessages] = useState<MyCaseChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [sessions, setSessions] = useState<MyCaseChatSessionSummary[]>([]);
  const [currentId, setCurrentId] = useState("");
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => setMounted(true), []);

  // Show a toast when we return from the MyCase OAuth flow, then clean the URL.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const mycase = params.get("mycase");
    if (mycase === "connected") {
      toast.success("Connected to MyCase");
    } else if (mycase === "error") {
      toast.error(`MyCase connection failed${params.get("reason") ? `: ${params.get("reason")}` : ""}`);
    }
    if (mycase) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, [toast]);

  useEffect(() => {
    (async () => {
      try {
        const existing = await listMyCaseChatSessions();
        setSessions(existing);
      } catch {
        setSessions([]);
      }
      setCurrentId(makeSessionId());
      setMessages([]);
      setHistoryLoaded(true);
    })();
  }, []);

  useEffect(() => {
    if (!historyLoaded || !currentId || messages.length === 0) return;
    saveMyCaseChatSession(currentId, deriveTitle(messages), messages)
      .then((summary) => {
        setSessions((prev) => {
          const idx = prev.findIndex((s) => s.id === currentId);
          if (idx === -1) return [summary, ...prev];
          const copy = [...prev];
          copy[idx] = summary;
          return copy;
        });
      })
      .catch(() => {});
  }, [messages, currentId, historyLoaded]);

  function newChat() {
    setCurrentId(makeSessionId());
    setMessages([]);
    setInput("");
  }

  async function loadSession(id: string) {
    setCurrentId(id);
    try {
      const detail = await getMyCaseChatSession(id);
      setMessages(detail.messages ?? []);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to load chat");
      setMessages([]);
    }
  }

  async function deleteSession(id: string) {
    try {
      await deleteMyCaseChatSession(id);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to delete chat");
      return;
    }
    const next = sessions.filter((s) => s.id !== id);
    setSessions(next);
    if (id === currentId) {
      if (next.length) {
        const latest = [...next].sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())[0];
        await loadSession(latest.id);
      } else {
        newChat();
      }
    }
  }

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [input]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    const userMsg: MyCaseChatMessage = { role: "user", content: trimmed };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await runMyCaseAgent(trimmed, history, controller.signal);
      if (res.success) {
        setMessages((prev) => [...prev, { role: "assistant", content: res.reply, steps: res.steps }]);
      } else {
        toast.error(res.error ?? "Agent failed");
        setMessages((prev) => [...prev, { role: "assistant", content: res.error ?? "The agent failed to respond.", error: true }]);
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") {
        // User clicked Stop — this is not a failure, don't show an error bubble.
        // The backend keeps running to completion regardless (we've just stopped
        // waiting on it), so nothing further is appended for this turn.
        toast.info("Stopped");
      } else {
        const msg = err instanceof Error ? err.message : "Request failed";
        toast.error(msg);
        setMessages((prev) => [...prev, { role: "assistant", content: msg, error: true }]);
      }
    } finally {
      abortRef.current = null;
      setLoading(false);
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  return (
    <>
      <Topbar title="MyCase Agent" subtitle="Chat with an AI that can look up your MyCase practice management data" />

      <div className="page-body fade-in">
        <div className="page-header mb-3">
          <div>
            <div className="page-header-title">
              <i className="bi bi-briefcase-fill me-2" style={{ color: "#0b6fcc" }} />
              MyCase Agent
            </div>
            <div className="page-header-subtitle">
              Ask in plain language — the agent searches cases, clients, documents, billing, and more from MyCase. Read-only for now.
            </div>
          </div>
        </div>

        <div className="d-flex align-items-center mb-3" style={{ gap: 8, rowGap: 8, flexWrap: "wrap" }}>
          <ModelSelector />
          <div className="d-flex align-items-center ms-auto" style={{ gap: 8, rowGap: 8, flexWrap: "wrap" }}>
            <MyCaseConnection />
            <HistoryMenu sessions={sessions} currentId={currentId} onSelect={loadSession} onDelete={deleteSession} />
            <button
              className="btn btn-sm"
              style={{ background: "white", border: "1px solid #e2e8f0", borderRadius: 8, fontSize: "0.78rem", color: "#475569", whiteSpace: "nowrap" }}
              onClick={newChat}
              title="Start a new chat"
            >
              <i className="bi bi-plus-lg me-1" />
              New chat
            </button>
          </div>
        </div>

        {mounted && !allowed && (
          <div
            className="alert mb-3"
            style={{ background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.25)", color: "#b45309", borderRadius: 10, fontSize: "0.8rem" }}
          >
            <i className="bi bi-exclamation-triangle me-1" />
            The MyCase Agent is available to BD and Admin roles. You can explore the UI, but requests will be rejected by the server.
          </div>
        )}

        <div className="content-card" style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 250px)", minHeight: 420 }}>
          <div
            ref={scrollRef}
            className="content-card-body"
            style={{ flex: 1, overflowY: "auto", overflowX: "hidden", display: "flex", flexDirection: "column", gap: "1rem" }}
          >
            {messages.length === 0 && !loading && (
              <div className="d-flex flex-column align-items-center justify-content-center text-center" style={{ flex: 1, gap: 14 }}>
                <div
                  style={{
                    width: 56, height: 56, borderRadius: 16, background: "rgba(11,111,204,0.1)",
                    display: "flex", alignItems: "center", justifyContent: "center",
                  }}
                >
                  <i className="bi bi-briefcase-fill" style={{ fontSize: "1.6rem", color: "#0b6fcc" }} />
                </div>
                <div>
                  <div style={{ fontWeight: 600, color: "#1e293b" }}>Start a conversation</div>
                  <div style={{ fontSize: "0.82rem", color: "#94a3b8" }}>Try one of these to get going:</div>
                </div>
                <div className="d-flex flex-column gap-2" style={{ width: "100%", maxWidth: 460 }}>
                  {EXAMPLES.map((ex) => (
                    <button
                      key={ex}
                      onClick={() => send(ex)}
                      style={{
                        background: "white", border: "1px solid #e2e8f0", borderRadius: 10,
                        padding: "0.6rem 0.9rem", fontSize: "0.82rem", color: "#475569", textAlign: "left", cursor: "pointer",
                      }}
                    >
                      <i className="bi bi-arrow-return-right me-2" style={{ color: "#94a3b8" }} />
                      {ex}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m, i) => <MessageBubble key={i} msg={m} />)}

            {loading && (
              <div className="d-flex justify-content-start">
                <div
                  style={{
                    background: "white", border: "1px solid #e2e8f0", borderRadius: 14, borderTopLeftRadius: 4,
                    padding: "0.6rem 0.9rem", fontSize: "0.82rem", color: "#64748b", display: "flex", alignItems: "center", gap: 8,
                  }}
                >
                  <span className="spinner-border spinner-border-sm" style={{ color: "#0b6fcc" }} />
                  Thinking &amp; querying MyCase…
                  <button
                    onClick={stop}
                    style={{
                      marginLeft: 4, display: "inline-flex", alignItems: "center", gap: 4,
                      padding: "0.15rem 0.5rem", background: "#fef2f2", color: "#ef4444",
                      borderRadius: 20, fontSize: "0.7rem", fontWeight: 600,
                      border: "1px solid #fecaca", cursor: "pointer", whiteSpace: "nowrap",
                    }}
                  >
                    <i className="bi bi-stop-fill" />
                    Stop
                  </button>
                </div>
              </div>
            )}
          </div>

          <div style={{ borderTop: "1px solid #e2e8f0", padding: "0.75rem" }}>
            <div className="d-flex gap-2 align-items-end">
              <textarea
                ref={textareaRef}
                className="form-control"
                rows={1}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Ask the MyCase Agent about cases, clients, documents, billing…"
                style={{ fontSize: "0.85rem", borderRadius: 10, resize: "none", maxHeight: 180, overflowY: "auto" }}
                disabled={loading}
              />
              {loading ? (
                <button
                  className="btn"
                  style={{ background: "#ef4444", color: "white", border: "none", borderRadius: 10, fontSize: "0.85rem", padding: "0.5rem 1rem", flexShrink: 0 }}
                  onClick={stop}
                  title="Stop generating"
                >
                  <i className="bi bi-stop-fill" />
                </button>
              ) : (
                <button
                  className="btn"
                  style={{ background: "#0b6fcc", color: "white", border: "none", borderRadius: 10, fontSize: "0.85rem", padding: "0.5rem 1rem", flexShrink: 0 }}
                  onClick={() => send(input)}
                  disabled={!input.trim()}
                >
                  <i className="bi bi-send-fill" />
                </button>
              )}
            </div>
            <div style={{ fontSize: "0.68rem", color: "#94a3b8", marginTop: 6, paddingLeft: 2 }}>
              Read-only — this agent can look up MyCase data but cannot create, update, or delete records. Enter to send, Shift+Enter for a new line.
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
