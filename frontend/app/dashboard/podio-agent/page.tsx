"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import Topbar from "@/components/Topbar";
import {
  runPodioAgent,
  getPodioStatus,
  startPodioConnect,
  getPodioTools,
  disconnectPodio,
  listPodioOrganizations,
  listPodioSpaces,
  setPodioWorkspace,
  listLlmModels,
  setLlmModel,
  getPodioFilesStatus,
  startPodioFilesConnect,
  uploadPodioFile,
  downloadPodioFile,
  listPodioChatSessions,
  getPodioChatSession,
  savePodioChatSession,
  deletePodioChatSession,
} from "@/lib/api";
import { getAuth } from "@/lib/auth";
import { useToast } from "@/lib/toast";
import Markdown from "@/components/Markdown";
import JsonTree from "@/components/JsonTree";
import type { PodioChatMessage, PodioAgentStep, PodioChatSessionSummary } from "@/types";

const TOOL_META: Record<string, { label: string; icon: string; color: string }> = {
  // Podio MCP tool names
  get_items:                  { label: "Get Items",          icon: "bi-list-ul",        color: "#6366f1" },
  get_item:                   { label: "Get Item",           icon: "bi-file-earmark",   color: "#6366f1" },
  get_app:                    { label: "Get App",            icon: "bi-grid-3x3-gap",   color: "#0ea5e9" },
  get_app_summary:            { label: "App Summary",        icon: "bi-grid-3x3-gap",   color: "#0ea5e9" },
  get_apps_in_space:          { label: "List Apps",          icon: "bi-columns-gap",    color: "#0ea5e9" },
  get_organizations:          { label: "Organizations",      icon: "bi-building",       color: "#8b5cf6" },
  get_spaces_in_organization: { label: "Workspaces",         icon: "bi-diagram-3",      color: "#8b5cf6" },
  search_globally:            { label: "Search",             icon: "bi-search",         color: "#6366f1" },
  get_item_comments:          { label: "Item Comments",      icon: "bi-chat-left-text", color: "#64748b" },
  get_tasks:                  { label: "Get Tasks",          icon: "bi-check2-square",  color: "#10b981" },
  get_notifications:          { label: "Notifications",      icon: "bi-bell",           color: "#64748b" },
  get_space_members:          { label: "Workspace Members",  icon: "bi-people",         color: "#64748b" },
  get_org_members:            { label: "Org Members",        icon: "bi-people",         color: "#64748b" },
  create_item:                { label: "Create Item",        icon: "bi-plus-square",    color: "#f59e0b" },
  update_item:                { label: "Update Item",        icon: "bi-pencil-square",  color: "#f59e0b" },
  add_comment:                { label: "Add Comment",        icon: "bi-chat-left-dots", color: "#f59e0b" },
  create_task:                { label: "Create Task",        icon: "bi-plus-circle",    color: "#f59e0b" },
  update_task:                { label: "Update Task",        icon: "bi-pencil",         color: "#f59e0b" },
  complete_task:              { label: "Complete Task",      icon: "bi-check2-circle",  color: "#10b981" },
  // custom REST file tools
  attach_file_to_item:        { label: "Attach File",        icon: "bi-paperclip",      color: "#0ea5e9" },
  attach_file:                { label: "Attach File",        icon: "bi-paperclip",      color: "#0ea5e9" },
  set_item_image:             { label: "Set Item Image",     icon: "bi-image",          color: "#0ea5e9" },
  get_item_files:             { label: "List Files",         icon: "bi-folder2-open",   color: "#6366f1" },
  // calendar (native + externally added / linked-account calendars)
  get_calendar:               { label: "Calendar",           icon: "bi-calendar3",      color: "#6366f1" },
  get_space_calendar:         { label: "Workspace Calendar", icon: "bi-calendar-week",  color: "#6366f1" },
  get_app_calendar:           { label: "App Calendar",       icon: "bi-calendar-event", color: "#6366f1" },
  list_linked_accounts:       { label: "Added Calendars",    icon: "bi-calendar-plus",  color: "#8b5cf6" },
  get_linked_account_calendar:{ label: "External Calendar",  icon: "bi-calendar-heart", color: "#8b5cf6" },
  download_file:              { label: "Download File",      icon: "bi-cloud-download", color: "#10b981" },
  delete_file:                { label: "Delete File",        icon: "bi-trash",          color: "#ef4444" },
  delete_item:                { label: "Delete Item",        icon: "bi-trash3",         color: "#ef4444" },
  // legacy REST tool names
  search_leads:               { label: "Search Leads",       icon: "bi-search",         color: "#6366f1" },
  get_contact:                { label: "Get Contact",        icon: "bi-person-vcard",   color: "#10b981" },
  create_note:                { label: "Create Note",        icon: "bi-pencil-square",  color: "#f59e0b" },
};

function fmtArg(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

const EXAMPLES = [
  "Search Podio for leads about 'acme'",
  "Get the contact with item ID 123456",
  "Add a note to item 123456 saying 'Called, left voicemail'",
];

function StepCard({ step }: { step: PodioAgentStep }) {
  const [open, setOpen] = useState(false);
  const meta = TOOL_META[step.tool] ?? { label: step.tool, icon: "bi-gear", color: "#64748b" };
  const argEntries = Object.entries(step.args ?? {});
  const r = step.result as
    | {
        isError?: boolean; success?: boolean; error?: string; content?: string; data?: unknown;
        // download_file extras
        file_id?: number; filename?: string; mimetype?: string; size?: number;
        download_url?: string; note?: string; truncated?: boolean;
        // export_app_xlsx extras
        app_id?: number;
      }
    | undefined;
  const failed = r?.isError === true || r?.success === false || !!r?.error;
  const message = (r?.error || r?.content || "").trim();
  const data = r?.data;

  const argSummary = argEntries.map(([k, v]) => `${k}=${fmtArg(v)}`).join(", ");
  const argSummaryShort = argSummary.length > 70 ? argSummary.slice(0, 70) + "…" : argSummary;

  return (
    <div
      style={{
        border: "1px solid #e2e8f0",
        borderRadius: 10,
        background: "#f8fafc",
        overflow: "hidden",
      }}
    >
      <div
        onClick={() => setOpen((o) => !o)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "0.5rem 0.7rem",
          cursor: "pointer",
        }}
      >
        <div
          style={{
            width: 24,
            height: 24,
            borderRadius: 6,
            background: `${meta.color}1a`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <i className={`bi ${meta.icon}`} style={{ color: meta.color, fontSize: "0.72rem" }} />
        </div>
        <span style={{ fontSize: "0.76rem", fontWeight: 600, color: "#1e293b" }}>
          {meta.label}
        </span>
        {argSummaryShort && (
          <span
            style={{
              fontSize: "0.7rem",
              color: "#94a3b8",
              fontFamily: "monospace",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={argSummary}
          >
            {argSummaryShort}
          </span>
        )}
        <span
          style={{
            marginLeft: "auto",
            fontSize: "0.65rem",
            fontWeight: 600,
            color: failed ? "#ef4444" : "#10b981",
          }}
        >
          {failed ? "error" : "ok"}
        </span>
        <i
          className={`bi bi-chevron-${open ? "up" : "down"}`}
          style={{ fontSize: "0.65rem", color: "#94a3b8" }}
        />
      </div>
      {open && (
        <div
          style={{
            borderTop: "1px solid #e2e8f0",
            padding: "0.6rem 0.7rem",
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          {argEntries.length > 0 && (
            <div>
              <SectionLabel>Arguments</SectionLabel>
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                {argEntries.map(([k, v]) => (
                  <div key={k} style={{ fontSize: "0.72rem", fontFamily: "monospace", lineHeight: 1.5 }}>
                    <span style={{ color: "#6d28d9", fontWeight: 600 }}>{k}</span>
                    <span style={{ color: "#94a3b8" }}> = </span>
                    {v !== null && typeof v === "object" ? (
                      <JsonTree data={v} />
                    ) : (
                      <span style={{ color: "#334155", wordBreak: "break-word" }}>{fmtArg(v)}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div>
            <SectionLabel>Result</SectionLabel>
            {failed ? (
              <div
                style={{
                  background: "#fef2f2",
                  border: "1px solid #fecaca",
                  color: "#b91c1c",
                  borderRadius: 8,
                  padding: "0.45rem 0.6rem",
                  fontSize: "0.74rem",
                  lineHeight: 1.5,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                <i className="bi bi-exclamation-triangle-fill me-2" />
                {message || "The tool returned an error."}
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {/* export_app_xlsx tool: show download button (auth via API client) */}
                {step.tool === "export_app_xlsx" && r?.success && r?.app_id != null && (
                  <div
                    style={{
                      background: "#f0fdf4",
                      border: "1px solid #bbf7d0",
                      borderRadius: 8,
                      padding: "0.5rem 0.7rem",
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                    }}
                  >
                    <i className="bi bi-file-earmark-spreadsheet" style={{ color: "#10b981", fontSize: "1.1rem" }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: "0.78rem", fontWeight: 600, color: "#065f46" }}>
                        Excel export ready
                      </div>
                      <div style={{ fontSize: "0.68rem", color: "#6b7280" }}>
                        App ID {r.app_id} · .xlsx
                      </div>
                    </div>
                    <button
                      onClick={async () => {
                        const { downloadPodioExport } = await import("@/lib/api");
                        downloadPodioExport(r!.app_id!).catch(console.error);
                      }}
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 4,
                        padding: "0.3rem 0.7rem", background: "#10b981", color: "#fff",
                        borderRadius: 6, fontSize: "0.72rem", fontWeight: 600,
                        border: "none", cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0,
                      }}
                    >
                      <i className="bi bi-download" />
                      Download
                    </button>
                  </div>
                )}
                {/* download_file tool: show file info + download button */}
                {step.tool === "download_file" && r?.file_id != null && (
                  <div
                    style={{
                      background: "#f0fdf4",
                      border: "1px solid #bbf7d0",
                      borderRadius: 8,
                      padding: "0.5rem 0.7rem",
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                    }}
                  >
                    <i className="bi bi-file-earmark-arrow-down" style={{ color: "#10b981", fontSize: "1.1rem" }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: "0.78rem", fontWeight: 600, color: "#065f46", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {r.filename ?? `file_${r.file_id}`}
                      </div>
                      {r.size != null && (
                        <div style={{ fontSize: "0.68rem", color: "#6b7280" }}>
                          {r.mimetype ?? "file"} · {r.size < 1024 ? `${r.size} B` : r.size < 1024 * 1024 ? `${(r.size / 1024).toFixed(1)} KB` : `${(r.size / (1024 * 1024)).toFixed(1)} MB`}
                          {r.truncated && " · preview truncated"}
                        </div>
                      )}
                    </div>
                    <a
                      href={`http://localhost:8000/integrations/podio-files/download/${r.file_id}`}
                      download={r.filename ?? undefined}
                      onClick={async (e) => {
                        e.preventDefault();
                        const { downloadPodioFile } = await import("@/lib/api");
                        downloadPodioFile(r!.file_id!, r!.filename ?? undefined).catch(console.error);
                      }}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 4,
                        padding: "0.3rem 0.7rem",
                        background: "#10b981",
                        color: "#fff",
                        borderRadius: 6,
                        fontSize: "0.72rem",
                        fontWeight: 600,
                        textDecoration: "none",
                        whiteSpace: "nowrap",
                        flexShrink: 0,
                      }}
                    >
                      <i className="bi bi-download" />
                      Download
                    </a>
                  </div>
                )}
                {/* text content returned inline */}
                {step.tool === "download_file" && r?.content && (
                  <div>
                    <div style={{ fontSize: "0.62rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em", color: "#94a3b8", marginBottom: 4 }}>File Content</div>
                    <pre
                      style={{
                        background: "#1e293b",
                        color: "#e2e8f0",
                        borderRadius: 8,
                        padding: "0.6rem 0.8rem",
                        fontSize: "0.72rem",
                        lineHeight: 1.6,
                        overflowX: "auto",
                        maxHeight: 280,
                        overflowY: "auto",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        margin: 0,
                      }}
                    >{r.content}</pre>
                  </div>
                )}
                {message && step.tool !== "download_file" && (
                  <div
                    style={{
                      fontSize: "0.74rem",
                      lineHeight: 1.5,
                      color: "#334155",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                    }}
                  >
                    {message}
                  </div>
                )}
                {data !== undefined && data !== null ? (
                  <div style={{ maxHeight: 300, overflow: "auto" }}>
                    <JsonTree data={data} />
                  </div>
                ) : (
                  step.tool !== "download_file" && !message && <span style={{ fontSize: "0.74rem", color: "#94a3b8" }}>No data returned.</span>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        fontSize: "0.62rem",
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.04em",
        color: "#94a3b8",
        marginBottom: 4,
      }}
    >
      {children}
    </div>
  );
}

function MessageBubble({ msg }: { msg: PodioChatMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={`d-flex ${isUser ? "justify-content-end" : "justify-content-start"}`}>
      <div style={{ maxWidth: "85%", display: "flex", flexDirection: "column", gap: 8 }}>
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
          {isUser ? (
            msg.content || ""
          ) : msg.content ? (
            <Markdown content={msg.content} />
          ) : (
            msg.error ? "Something went wrong." : ""
          )}
        </div>
        {msg.steps && msg.steps.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={{ fontSize: "0.68rem", color: "#94a3b8", fontWeight: 600, paddingLeft: 2 }}>
              <i className="bi bi-tools me-1" />
              {msg.steps.length} Podio action{msg.steps.length !== 1 ? "s" : ""}
            </span>
            {msg.steps.map((s, i) => (
              <StepCard key={i} step={s} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PodioFilesButton({ connected }: { connected: boolean }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  async function onConnect() {
    setBusy(true);
    try {
      const res = await startPodioFilesConnect();
      if (res.success && res.authorize_url) {
        window.location.href = res.authorize_url;
      } else {
        toast.error(res.error ?? "Could not start Podio Files connection");
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Connection failed");
    } finally {
      setBusy(false);
    }
  }

  if (connected) {
    return (
      <span
        className="d-flex align-items-center gap-1"
        title="Podio file upload is connected"
        style={{ fontSize: "0.74rem", color: "#10b981", fontWeight: 600 }}
      >
        <i className="bi bi-paperclip" /> Files
        <i className="bi bi-check-circle-fill" />
      </span>
    );
  }
  return (
    <button
      className="btn btn-sm"
      onClick={onConnect}
      disabled={busy}
      title="Connect Podio file upload (REST API)"
      style={{
        background: "white",
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        fontSize: "0.74rem",
        padding: "0.3rem 0.6rem",
        color: "#475569",
      }}
    >
      <i className="bi bi-paperclip me-1" />
      {busy ? "Connecting…" : "Connect Files"}
    </button>
  );
}

function PodioConnection({ onConnectedChange }: { onConnectedChange?: (c: boolean) => void }) {
  const toast = useToast();
  const [connected, setConnected] = useState<boolean | null>(null);
  const [toolCount, setToolCount] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await getPodioStatus();
      setConnected(s.connected);
      onConnectedChange?.(s.connected);
      if (s.connected) {
        const t = await getPodioTools();
        if (t.success) setToolCount(t.count ?? null);
      }
    } catch {
      setConnected(false);
      onConnectedChange?.(false);
    }
  }, [onConnectedChange]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function connect() {
    setBusy(true);
    try {
      const res = await startPodioConnect();
      if (res.success && res.authorize_url) {
        // Hand off to Podio's OAuth login; we'll return to ?podio=connected.
        window.location.href = res.authorize_url;
      } else {
        toast.error(res.error ?? "Could not start Podio connection");
        setBusy(false);
      }
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Connect failed");
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await disconnectPodio();
      setToolCount(null);
      await refresh();
      toast.info("Disconnected from Podio");
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Disconnect failed");
    } finally {
      setBusy(false);
    }
  }

  if (connected === null) {
    return <span style={{ fontSize: "0.78rem", color: "#94a3b8" }}>Checking Podio…</span>;
  }

  if (connected) {
    return (
      <div className="d-flex align-items-center gap-2">
        <span
          style={{
            fontSize: "0.72rem", fontWeight: 600, color: "#166534",
            background: "#dcfce7", borderRadius: 20, padding: "3px 10px",
          }}
        >
          <i className="bi bi-check-circle-fill me-1" />
          Podio connected{toolCount != null ? ` · ${toolCount} tools` : ""}
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
      style={{ background: "#10b981", color: "white", border: "none", borderRadius: 8, fontSize: "0.8rem", fontWeight: 600, whiteSpace: "nowrap" }}
      onClick={connect}
      disabled={busy}
    >
      {busy ? (
        <><span className="spinner-border spinner-border-sm me-1" />Connecting…</>
      ) : (
        <><i className="bi bi-plug-fill me-1" />Connect Podio</>
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
        const res = await listLlmModels();
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
      await setLlmModel(model);
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
        {/* Ensure the current selection is always shown even if discovery missed it */}
        {!loading && selected && ![...ollama, ...google, ...groq, ...mistral, ...openai, ...anthropic, ...zai].includes(selected) && (
          <option value={selected}>{label(selected)}</option>
        )}
        {ollama.length > 0 && (
          <optgroup label="Ollama (local)">
            {ollama.map((m) => (
              <option key={m} value={m}>{label(m)}</option>
            ))}
          </optgroup>
        )}
        {google.length > 0 && (
          <optgroup label="Google Gemini">
            {google.map((m) => (
              <option key={m} value={m}>{label(m)}</option>
            ))}
          </optgroup>
        )}
        {groq.length > 0 && (
          <optgroup label="Groq (cloud)">
            {groq.map((m) => (
              <option key={m} value={m}>{label(m)}</option>
            ))}
          </optgroup>
        )}
        {mistral.length > 0 && (
          <optgroup label="Mistral AI">
            {mistral.map((m) => (
              <option key={m} value={m}>{label(m)}</option>
            ))}
          </optgroup>
        )}
        {openai.length > 0 && (
          <optgroup label="OpenAI (GPT)">
            {openai.map((m) => (
              <option key={m} value={m}>{label(m)}</option>
            ))}
          </optgroup>
        )}
        {anthropic.length > 0 && (
          <optgroup label="Anthropic (Claude)">
            {anthropic.map((m) => (
              <option key={m} value={m}>{label(m)}</option>
            ))}
          </optgroup>
        )}
        {zai.length > 0 && (
          <optgroup label="Z.ai (GLM)">
            {zai.map((m) => (
              <option key={m} value={m}>{label(m)}</option>
            ))}
          </optgroup>
        )}
        {!loading && ollama.length === 0 && google.length === 0 && groq.length === 0 &&
          mistral.length === 0 && openai.length === 0 && anthropic.length === 0 &&
          zai.length === 0 && (
          <option value="">No models found</option>
        )}
      </select>
    </div>
  );
}

// Default workspace applied automatically the first time (when nothing is selected yet).
const DEFAULT_ORG_NAME = "360SynergyTech";
const DEFAULT_SPACE_NAME = "Test Work";
const normalizeName = (s: string) => (s || "").toLowerCase().replace(/[^a-z0-9]/g, "");

function WorkspacePicker({ enabled }: { enabled: boolean }) {
  const toast = useToast();
  const [orgs, setOrgs] = useState<{ org_id: number; name: string; spaces_count?: number }[]>([]);
  const [spaces, setSpaces] = useState<{ space_id: number; name: string }[]>([]);
  const [orgId, setOrgId] = useState<number | "">("");
  const [spaceId, setSpaceId] = useState<number | "">("");
  const [loadingOrgs, setLoadingOrgs] = useState(false);
  const [loadingSpaces, setLoadingSpaces] = useState(false);
  const [activeName, setActiveName] = useState<string | null>(null);

  // Auto-select 360SynergyTech → Test Work and persist it, only when no workspace
  // has been chosen yet (respects an existing selection).
  async function applyDefaults(orgList: { org_id: number; name: string }[]) {
    const org = orgList.find((o) => normalizeName(o.name) === normalizeName(DEFAULT_ORG_NAME));
    if (!org) return;
    setOrgId(org.org_id);
    setLoadingSpaces(true);
    try {
      const res = await listPodioSpaces(org.org_id);
      const spaceList = res.success ? res.spaces : [];
      setSpaces(spaceList);
      const sp = spaceList.find((s) => normalizeName(s.name) === normalizeName(DEFAULT_SPACE_NAME));
      if (sp) {
        setSpaceId(sp.space_id);
        await setPodioWorkspace(sp.space_id, sp.name, org.name);
        setActiveName(sp.name);
      }
    } catch {
      /* ignore — defaults are best-effort */
    } finally {
      setLoadingSpaces(false);
    }
  }

  useEffect(() => {
    if (!enabled) return;
    setLoadingOrgs(true);
    (async () => {
      try {
        const o = await listPodioOrganizations();
        const orgList = o.success ? o.organizations : [];
        setOrgs(orgList);
        await applyDefaults(orgList);
      } catch {
        /* ignore — header stays usable */
      } finally {
        setLoadingOrgs(false);
      }
    })();
  }, [enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  async function onOrg(e: React.ChangeEvent<HTMLSelectElement>) {
    const id = Number(e.target.value);
    setOrgId(id || "");
    setSpaces([]);
    setSpaceId("");
    if (!id) return;
    setLoadingSpaces(true);
    try {
      const res = await listPodioSpaces(id);
      if (res.success) setSpaces(res.spaces);
      else toast.error(res.error ?? "Failed to load workspaces");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to load workspaces");
    } finally {
      setLoadingSpaces(false);
    }
  }

  async function onSpace(e: React.ChangeEvent<HTMLSelectElement>) {
    const id = Number(e.target.value);
    setSpaceId(id || "");
    if (!id) return;
    const sp = spaces.find((s) => s.space_id === id);
    const org = orgs.find((o) => o.org_id === orgId);
    try {
      await setPodioWorkspace(id, sp?.name ?? null, org?.name ?? null);
      setActiveName(sp?.name ?? `Space ${id}`);
      toast.success(`Active workspace: ${sp?.name ?? id}`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to set workspace");
    }
  }

  const selectStyle: React.CSSProperties = { fontSize: "0.78rem", borderRadius: 8, width: 150, flex: "0 0 auto" };

  return (
    <div className="d-flex align-items-center gap-2" style={{ flex: "0 0 auto" }}>
      <select
        className="form-select form-select-sm"
        value={orgId}
        onChange={onOrg}
        disabled={!enabled || loadingOrgs}
        style={selectStyle}
        title="Organization"
      >
        <option value="">{!enabled ? "Connect Podio…" : loadingOrgs ? "Loading orgs…" : "Organization…"}</option>
        {orgs.map((o) => (
          <option key={o.org_id} value={o.org_id}>{o.name}</option>
        ))}
      </select>
      <select
        className="form-select form-select-sm"
        value={spaceId}
        onChange={onSpace}
        disabled={!enabled || !orgId || loadingSpaces}
        style={selectStyle}
        title="Workspace"
      >
        <option value="">{loadingSpaces ? "Loading…" : orgId ? "Workspace…" : "Workspace"}</option>
        {spaces.map((s) => (
          <option key={s.space_id} value={s.space_id}>{s.name}</option>
        ))}
      </select>
      <span
        style={{
          fontSize: "0.72rem", fontWeight: 600, color: "#3730a3",
          background: "rgba(99,102,241,0.1)", borderRadius: 20, padding: "3px 10px",
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          maxWidth: 150, flex: "0 0 auto",
          visibility: activeName ? "visible" : "hidden",
        }}
        title="Workspace the agent acts on"
      >
        <i className="bi bi-diagram-3-fill me-1" />
        {activeName ?? "—"}
      </span>
    </div>
  );
}

// ── Chat session history (persisted server-side, in the podio_chat_sessions table) ──
// Sessions are DB-backed so history survives across browsers/devices. The frontend
// only ever holds the lightweight PodioChatSessionSummary list (id/title/message_count/
// timestamps) for the History dropdown; full messages are fetched on demand when a
// session is opened, and written back via an upsert (PUT) whenever the active chat's
// messages change.

function makeSessionId(): string {
  try {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  } catch {}
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function deriveTitle(msgs: PodioChatMessage[]): string {
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
  sessions,
  currentId,
  onSelect,
  onDelete,
}: {
  sessions: PodioChatSessionSummary[];
  currentId: string;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  // Only show chats that actually have messages, newest-created first.
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
            style={{
              position: "absolute", right: 0, top: "calc(100% + 4px)", zIndex: 20,
              width: 280, maxHeight: 380, overflowY: "auto", padding: 4, borderRadius: 10,
            }}
          >
            {sorted.length === 0 && (
              <div className="text-muted text-center p-3" style={{ fontSize: "0.8rem" }}>No saved chats</div>
            )}
            {sorted.map((s) => (
              <div
                key={s.id}
                className="d-flex align-items-center gap-2 px-2 py-2"
                style={{
                  borderRadius: 8, cursor: "pointer",
                  background: s.id === currentId ? "rgba(99,102,241,0.1)" : "transparent",
                }}
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

export default function PodioAgentPage() {
  const auth = getAuth();
  const role = auth?.role ?? "dev";
  const allowed = role === "bd" || role === "admin";
  const toast = useToast();

  const [messages, setMessages] = useState<PodioChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [attachment, setAttachment] = useState<{ name: string; file: File; text: string | null } | null>(null);
  const [loading, setLoading] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [connected, setConnected] = useState(false);
  const [filesConnected, setFilesConnected] = useState(false);
  const [sessions, setSessions] = useState<PodioChatSessionSummary[]>([]);
  const [currentId, setCurrentId] = useState("");
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // getAuth() reads localStorage, which is empty during SSR — defer any
  // role-dependent UI until after mount to avoid a hydration mismatch.
  useEffect(() => setMounted(true), []);

  // Load the session list from the database after mount, then always start a
  // fresh (unsaved) chat — previous chats remain reachable via History. The new
  // chat isn't written to the DB until it has at least one message.
  useEffect(() => {
    (async () => {
      try {
        const existing = await listPodioChatSessions();
        setSessions(existing);
      } catch {
        setSessions([]);
      }
      setCurrentId(makeSessionId());
      setMessages([]);
      setHistoryLoaded(true);
    })();
  }, []);

  // Persist the live conversation to the database whenever it changes (upsert by
  // currentId — creates the session row on the first message, updates it after).
  useEffect(() => {
    if (!historyLoaded || !currentId || messages.length === 0) return;
    savePodioChatSession(currentId, deriveTitle(messages), messages)
      .then((summary) => {
        setSessions((prev) => {
          const idx = prev.findIndex((s) => s.id === currentId);
          if (idx === -1) return [summary, ...prev];
          const copy = [...prev];
          copy[idx] = summary;
          return copy;
        });
      })
      .catch(() => {
        // Best-effort — keep the conversation in local state even if the save fails
        // (e.g. transient network issue); it will retry on the next message.
      });
  }, [messages, currentId, historyLoaded]);

  function newChat() {
    setCurrentId(makeSessionId());
    setMessages([]);
    setInput("");
    setAttachment(null);
  }

  async function loadSession(id: string) {
    setCurrentId(id);
    try {
      const detail = await getPodioChatSession(id);
      setMessages(detail.messages ?? []);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to load chat");
      setMessages([]);
    }
  }

  async function deleteSession(id: string) {
    try {
      await deletePodioChatSession(id);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to delete chat");
      return;
    }
    const next = sessions.filter((s) => s.id !== id);
    setSessions(next);
    if (id === currentId) {
      if (next.length) {
        const latest = [...next].sort(
          (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
        )[0];
        await loadSession(latest.id);
      } else {
        newChat();
      }
    }
  }

  // Show a toast when we return from either Podio OAuth flow, then clean the URL.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const podio = params.get("podio");
    const podioFiles = params.get("podio_files");
    if (podio === "connected") {
      toast.success("Connected to Podio");
    } else if (podio === "error") {
      toast.error(`Podio connection failed${params.get("reason") ? `: ${params.get("reason")}` : ""}`);
    }
    if (podioFiles === "connected") {
      toast.success("Connected to Podio Files");
      setFilesConnected(true);
    } else if (podioFiles === "error") {
      toast.error(`Podio Files connection failed${params.get("reason") ? `: ${params.get("reason")}` : ""}`);
    }
    if (podio || podioFiles) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, [toast]);

  // Track whether the Podio REST (file upload) connection is live.
  useEffect(() => {
    getPodioFilesStatus().then((s) => setFilesConnected(s.connected)).catch(() => {});
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  // Auto-grow the textarea with its content, up to a max height.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [input]);

  async function send(text: string) {
    const trimmed = text.trim();
    if ((!trimmed && !attachment) || loading) return;

    let messageToSend = trimmed;
    let displayContent = trimmed;

    if (attachment) {
      if (filesConnected) {
        // Upload bytes to Podio first (the LLM can't carry binary). Then hand the
        // agent the file_id so it can attach it to a record on request.
        setLoading(true);
        try {
          const up = await uploadPodioFile(attachment.file);
          if (!up.success || up.file_id == null) {
            toast.error(up.error ?? "Podio upload failed");
            setLoading(false);
            return;
          }
          messageToSend =
            `${trimmed}\n\n[A file "${attachment.name}" was uploaded to Podio ` +
            `(file_id=${up.file_id}). To attach it to a contact/record, resolve the ` +
            `record's item_id and call attach_file_to_item with this file_id.]`.trim();
          displayContent = trimmed ? `${trimmed}\n\n📎 ${attachment.name}` : `📎 ${attachment.name}`;
        } catch (err: unknown) {
          toast.error(err instanceof Error ? err.message : "Podio upload failed");
          setLoading(false);
          return;
        }
      } else if (attachment.text != null) {
        // Not connected to Podio Files — fold text content into the message as before.
        messageToSend = `${trimmed}\n\n--- Attached file: ${attachment.name} ---\n${attachment.text}`.trim();
        displayContent = trimmed ? `${trimmed}\n\n📎 ${attachment.name}` : `📎 ${attachment.name}`;
      }
    }

    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    const userMsg: PodioChatMessage = { role: "user", content: displayContent };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setAttachment(null);
    setLoading(true);

    try {
      const res = await runPodioAgent(messageToSend, history);
      if (res.success) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: res.reply, steps: res.steps },
        ]);
        // Auto-trigger browser download for any successful download_file tool step.
        for (const step of res.steps ?? []) {
          if (step.tool === "download_file") {
            const r = step.result as { success?: boolean; file_id?: number; filename?: string } | undefined;
            if (r?.success && r.file_id != null) {
              downloadPodioFile(r.file_id, r.filename ?? undefined).catch(() => {});
            }
          }
        }
      } else {
        toast.error(res.error ?? "Agent failed");
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: res.error ?? "The agent failed to respond.", error: true },
        ]);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Request failed";
      toast.error(msg);
      setMessages((prev) => [...prev, { role: "assistant", content: msg, error: true }]);
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  const TEXT_EXT = [".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".xml", ".html"];
  const MAX_FILE_BYTES = 25 * 1024 * 1024; // 25 MB (Podio upload cap)

  async function onFilePicked(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow picking the same file again
    if (!file) return;
    if (file.size > MAX_FILE_BYTES) {
      toast.error("File too large (max 25 MB).");
      return;
    }
    const lower = file.name.toLowerCase();
    const isText = TEXT_EXT.some((ext) => lower.endsWith(ext)) || file.type.startsWith("text/");
    // Binary files can only be sent when connected to Podio Files (uploaded there).
    if (!isText && !filesConnected) {
      toast.error("Connect Podio Files (top right) to upload non-text files.");
      return;
    }
    try {
      const text = isText ? await file.text() : null;
      setAttachment({ name: file.name, file, text });
      toast.success(`Attached ${file.name}`);
    } catch {
      toast.error("Could not read the file.");
    }
  }

  return (
    <>
      <Topbar title="Podio Agent" subtitle="Chat with an AI that can act on your Podio CRM" />

      <div className="page-body fade-in">
        <div className="page-header mb-3">
          <div>
            <div className="page-header-title">
              <i className="bi bi-robot me-2" style={{ color: "var(--primary)" }} />
              Podio Agent
            </div>
            <div className="page-header-subtitle">
              Ask in plain language — the agent searches leads, fetches contacts, and creates
              notes in Podio using the local LLM.
            </div>
          </div>
        </div>

        {/* Controls toolbar — model/workspace anchored left (fixed), connection
            actions on the right; wraps responsively without reflowing the left side. */}
        <div
          className="d-flex align-items-center mb-3"
          style={{ gap: 8, rowGap: 8, flexWrap: "wrap" }}
        >
          <ModelSelector />
          <WorkspacePicker enabled={connected} />
          <div
            className="d-flex align-items-center ms-auto"
            style={{ gap: 8, rowGap: 8, flexWrap: "wrap" }}
          >
            <PodioConnection onConnectedChange={setConnected} />
            <PodioFilesButton connected={filesConnected} />
            <HistoryMenu
              sessions={sessions}
              currentId={currentId}
              onSelect={loadSession}
              onDelete={deleteSession}
            />
            <button
              className="btn btn-sm"
              style={{
                background: "white",
                border: "1px solid #e2e8f0",
                borderRadius: 8,
                fontSize: "0.78rem",
                color: "#475569",
                whiteSpace: "nowrap",
              }}
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
            style={{
              background: "rgba(245,158,11,0.08)",
              border: "1px solid rgba(245,158,11,0.25)",
              color: "#b45309",
              borderRadius: 10,
              fontSize: "0.8rem",
            }}
          >
            <i className="bi bi-exclamation-triangle me-1" />
            The Podio Agent is available to BD and Admin roles. You can explore the UI, but
            requests will be rejected by the server.
          </div>
        )}

        <div
          className="content-card"
          style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 250px)", minHeight: 420 }}
        >
          {/* Conversation */}
          <div
            ref={scrollRef}
            className="content-card-body"
            style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "1rem" }}
          >
            {messages.length === 0 && !loading && (
              <div className="d-flex flex-column align-items-center justify-content-center text-center" style={{ flex: 1, gap: 14 }}>
                <div
                  style={{
                    width: 56,
                    height: 56,
                    borderRadius: 16,
                    background: "rgba(99,102,241,0.1)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <i className="bi bi-robot" style={{ fontSize: "1.6rem", color: "var(--primary)" }} />
                </div>
                <div>
                  <div style={{ fontWeight: 600, color: "#1e293b" }}>Start a conversation</div>
                  <div style={{ fontSize: "0.82rem", color: "#94a3b8" }}>
                    Try one of these to get going:
                  </div>
                </div>
                <div className="d-flex flex-column gap-2" style={{ width: "100%", maxWidth: 460 }}>
                  {EXAMPLES.map((ex) => (
                    <button
                      key={ex}
                      onClick={() => send(ex)}
                      style={{
                        background: "white",
                        border: "1px solid #e2e8f0",
                        borderRadius: 10,
                        padding: "0.6rem 0.9rem",
                        fontSize: "0.82rem",
                        color: "#475569",
                        textAlign: "left",
                        cursor: "pointer",
                      }}
                    >
                      <i className="bi bi-arrow-return-right me-2" style={{ color: "#94a3b8" }} />
                      {ex}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m, i) => (
              <MessageBubble key={i} msg={m} />
            ))}

            {loading && (
              <div className="d-flex justify-content-start">
                <div
                  style={{
                    background: "white",
                    border: "1px solid #e2e8f0",
                    borderRadius: 14,
                    borderTopLeftRadius: 4,
                    padding: "0.6rem 0.9rem",
                    fontSize: "0.82rem",
                    color: "#64748b",
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <span className="spinner-border spinner-border-sm" style={{ color: "var(--primary)" }} />
                  Thinking &amp; running Podio tools…
                </div>
              </div>
            )}
          </div>

          {/* Input */}
          <div style={{ borderTop: "1px solid #e2e8f0", padding: "0.75rem" }}>
            {attachment && (
              <div style={{ marginBottom: 8 }}>
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    background: "rgba(99,102,241,0.1)",
                    color: "#4338ca",
                    borderRadius: 8,
                    padding: "3px 6px 3px 10px",
                    fontSize: "0.74rem",
                    maxWidth: "100%",
                  }}
                >
                  <i className="bi bi-paperclip" />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {attachment.name}
                  </span>
                  <button
                    type="button"
                    onClick={() => setAttachment(null)}
                    title="Remove attachment"
                    style={{
                      border: "none",
                      background: "transparent",
                      color: "#4338ca",
                      cursor: "pointer",
                      lineHeight: 1,
                      padding: "0 2px",
                    }}
                  >
                    <i className="bi bi-x-lg" style={{ fontSize: "0.7rem" }} />
                  </button>
                </span>
              </div>
            )}
            <div className="d-flex gap-2 align-items-end">
              <input
                type="file"
                ref={fileInputRef}
                onChange={onFilePicked}
                accept=".txt,.md,.csv,.json,.log,.yaml,.yml,.xml,.html,text/*"
                style={{ display: "none" }}
              />
              <button
                type="button"
                className="btn"
                title="Attach a text file"
                onClick={() => fileInputRef.current?.click()}
                disabled={loading}
                style={{
                  background: "white",
                  color: "#475569",
                  border: "1px solid #e2e8f0",
                  borderRadius: 10,
                  fontSize: "0.95rem",
                  padding: "0.4rem 0.7rem",
                  flexShrink: 0,
                }}
              >
                <i className="bi bi-paperclip" />
              </button>
              <textarea
                ref={textareaRef}
                className="form-control"
                rows={1}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Ask the Podio Agent to search, fetch, or note something…"
                style={{
                  fontSize: "0.85rem",
                  borderRadius: 10,
                  resize: "none",
                  maxHeight: 180,
                  overflowY: "auto",
                }}
                disabled={loading}
              />
              <button
                className="btn"
                style={{
                  background: "var(--primary)",
                  color: "white",
                  border: "none",
                  borderRadius: 10,
                  fontSize: "0.85rem",
                  padding: "0.5rem 1rem",
                  flexShrink: 0,
                }}
                onClick={() => send(input)}
                disabled={loading || (!input.trim() && !attachment)}
              >
                <i className="bi bi-send-fill" />
              </button>
            </div>
            <div style={{ fontSize: "0.68rem", color: connected ? "#94a3b8" : "#d97706", marginTop: 6, paddingLeft: 2 }}>
              {connected
                ? "Connected to Podio via MCP. Enter to send, Shift+Enter for a new line. Attach a text file with the clip."
                : "Not connected — click “Connect Podio” (top right) to authenticate before running actions."}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
