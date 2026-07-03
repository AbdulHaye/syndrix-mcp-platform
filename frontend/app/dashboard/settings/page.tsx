"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Topbar from "@/components/Topbar";
import { fetchSettings, saveSettings } from "@/lib/api";
import { useToast } from "@/lib/toast";
import type { IntegrationSetting } from "@/types";

interface ServiceGroup {
  id: string;
  label: string;
  icon: string;
  color: string;
  description: string;
  fields: {
    key: string;
    label: string;
    placeholder: string;
    secret?: boolean;
    inputType?: "text" | "number";
  }[];
}

// Core integrations — always visible, never opt-in
const GROUPS: ServiceGroup[] = [
  {
    id: "podio",
    label: "Podio (MCP)",
    icon: "bi-kanban-fill",
    color: "#10b981",
    description:
      "Connects to Podio's hosted MCP server via OAuth. Enter the OAuth Client ID + Secret, " +
      "Save, then click Connect on the Podio Agent page to log in. " +
      "Register this redirect URI on your OAuth client: http://localhost:8000/integrations/podio/callback",
    fields: [
      { key: "podio_mcp_client_id",     label: "OAuth Client ID",     placeholder: "your-podio-mcp-client-id" },
      { key: "podio_mcp_client_secret", label: "OAuth Client Secret", placeholder: "your-podio-mcp-client-secret", secret: true },
    ],
  },
  {
    id: "ghl",
    label: "GoHighLevel",
    icon: "bi-lightning-fill",
    color: "#f59e0b",
    description: "API key authentication for GoHighLevel CRM (v1).",
    fields: [
      { key: "ghl_api_key",     label: "API Key",     placeholder: "your-ghl-api-key", secret: true },
      { key: "ghl_location_id", label: "Location ID", placeholder: "your-location-id" },
    ],
  },
  {
    id: "slack",
    label: "Slack",
    icon: "bi-slack",
    color: "#6366f1",
    description: "Bot token for posting messages and reading channel history.",
    fields: [
      { key: "slack_bot_token",      label: "Bot Token",      placeholder: "xoxb-...", secret: true },
      { key: "slack_signing_secret", label: "Signing Secret", placeholder: "your-signing-secret", secret: true },
    ],
  },
  {
    id: "github",
    label: "GitHub",
    icon: "bi-github",
    color: "#334155",
    description: "Personal access token for repo search, issue creation, and PR listing.",
    fields: [
      { key: "github_token", label: "Personal Access Token", placeholder: "ghp_...", secret: true },
      { key: "github_org",   label: "Username / Org",        placeholder: "AbdulHaye" },
    ],
  },
  {
    id: "smtp",
    label: "Email (SMTP)",
    icon: "bi-envelope-fill",
    color: "#8b5cf6",
    description: "SMTP credentials for sending email via the email adapter.",
    fields: [
      { key: "smtp_host",     label: "SMTP Host",    placeholder: "smtp.gmail.com" },
      { key: "smtp_port",     label: "SMTP Port",    placeholder: "587", inputType: "number" },
      { key: "smtp_user",     label: "Username",     placeholder: "you@example.com" },
      { key: "smtp_password", label: "Password",     placeholder: "your password", secret: true },
      { key: "smtp_from",     label: "From Address", placeholder: "no-reply@example.com" },
    ],
  },
];

// LLM providers are opt-in (persisted to localStorage so they survive refreshes)
const LLM_PROVIDERS: ServiceGroup[] = [
  {
    id: "openai",
    label: "OpenAI (GPT)",
    icon: "bi-robot",
    color: "#10a37f",
    description:
      "API key for OpenAI GPT models. Once set, GPT models appear in the model selector on " +
      "the Podio Agent page. Use a tool-capable model (e.g. gpt-4o) for the agent.",
    fields: [
      { key: "openai_api_key", label: "API Key", placeholder: "sk-...", secret: true },
    ],
  },
  {
    id: "anthropic",
    label: "Anthropic (Claude)",
    icon: "bi-stars",
    color: "#d97757",
    description:
      "API key for Anthropic Claude models. Once set, Claude models appear in the model " +
      "selector on the Podio Agent page. Claude models are strong tool-callers.",
    fields: [
      { key: "anthropic_api_key", label: "API Key", placeholder: "sk-ant-...", secret: true },
    ],
  },
  {
    id: "google",
    label: "Google AI Studio (Gemini)",
    icon: "bi-google",
    color: "#4285f4",
    description:
      "API key for Google AI Studio (Gemini). Once set, Gemini models appear in the model " +
      "selector on the Podio Agent page.",
    fields: [
      { key: "google_api_key", label: "API Key", placeholder: "AIza...", secret: true },
    ],
  },
  {
    id: "groq",
    label: "Groq",
    icon: "bi-lightning-charge-fill",
    color: "#f55036",
    description:
      "API key for Groq's fast cloud LLMs (Llama 3.x, etc.). Use a tool-capable model for the agent.",
    fields: [
      { key: "groq_api_key", label: "API Key", placeholder: "gsk_...", secret: true },
    ],
  },
  {
    id: "mistral",
    label: "Mistral AI",
    icon: "bi-wind",
    color: "#fa520f",
    description:
      "API key for Mistral AI's cloud LLMs (Mistral Large, etc.). Use a tool-capable model for the agent.",
    fields: [
      { key: "mistral_api_key", label: "API Key", placeholder: "your-mistral-api-key", secret: true },
    ],
  },
];

const LLM_STORAGE_KEY = "syndrix_added_llms";

function loadSavedLLMs(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(LLM_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function saveLLMsToStorage(ids: string[]) {
  if (typeof window !== "undefined") {
    localStorage.setItem(LLM_STORAGE_KEY, JSON.stringify(ids));
  }
}

function AddMenu({
  available,
  onAdd,
  label,
  emptyLabel,
}: {
  available: ServiceGroup[];
  onAdd: (id: string) => void;
  label: string;
  emptyLabel: string;
}) {
  const [open, setOpen] = useState(false);
  if (available.length === 0) {
    return (
      <span className="text-muted" style={{ fontSize: "0.8rem" }}>
        {emptyLabel}
      </span>
    );
  }
  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        className="btn btn-primary btn-sm"
        onClick={() => setOpen((o) => !o)}
      >
        <i className="bi bi-plus-lg me-1" />
        {label}
      </button>
      {open && (
        <>
          <div
            onClick={() => setOpen(false)}
            style={{ position: "fixed", inset: 0, zIndex: 10 }}
          />
          <div
            className="card shadow"
            style={{
              position: "absolute", right: 0, top: "calc(100% + 4px)",
              zIndex: 20, minWidth: 240, padding: 6, borderRadius: 10,
            }}
          >
            {available.map((p) => (
              <button
                key={p.id}
                type="button"
                className="btn btn-light btn-sm d-flex align-items-center gap-2 w-100 text-start"
                style={{ border: "none" }}
                onClick={() => { onAdd(p.id); setOpen(false); }}
              >
                <span
                  style={{
                    width: 24, height: 24, borderRadius: 6, background: p.color,
                    display: "inline-flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
                  }}
                >
                  <i className={`bi ${p.icon}`} style={{ color: "white", fontSize: "0.8rem" }} />
                </span>
                <span style={{ fontSize: "0.85rem" }}>{p.label}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function ServiceCard({
  group,
  allSettings,
  onSaved,
  onRemove,
}: {
  group: ServiceGroup;
  allSettings: Record<string, IntegrationSetting>;
  onSaved: () => void;
  onRemove?: () => void;
}) {
  const { toast } = useToast();
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);

  function getValue(key: string): string {
    return key in overrides ? overrides[key] : (allSettings[key]?.value ?? "");
  }

  function handleChange(key: string, val: string) {
    setOverrides((prev) => ({ ...prev, [key]: val }));
  }

  async function handleSave() {
    setSaving(true);
    try {
      const payload: Record<string, string | null> = {};
      for (const f of group.fields) {
        const v = getValue(f.key);
        payload[f.key] = v === "" ? null : v;
      }
      const res = await saveSettings(payload);
      setOverrides({});
      onSaved();
      toast("success", `${group.label} saved (${res.saved.length} keys)`);
    } catch (err: unknown) {
      toast("error", (err as Error).message ?? "Save failed");
    } finally {
      setSaving(false);
    }
  }

  const primarySecretKey = group.fields.find((f) => f.secret)?.key;
  const isConfigured = primarySecretKey
    ? (allSettings[primarySecretKey]?.is_set ?? false)
    : group.fields.some((f) => allSettings[f.key]?.is_set);

  return (
    <div className="card shadow-sm mb-4">
      <div className="card-header d-flex align-items-center gap-3 py-3">
        <div
          style={{
            width: 38, height: 38, borderRadius: 10,
            background: group.color,
            display: "flex", alignItems: "center", justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <i className={`bi ${group.icon}`} style={{ color: "white", fontSize: "1rem" }} />
        </div>
        <div style={{ flex: 1 }}>
          <div className="fw-semibold" style={{ fontSize: "0.95rem" }}>{group.label}</div>
          <div className="text-muted" style={{ fontSize: "0.78rem" }}>{group.description}</div>
        </div>
        <span
          className="badge"
          style={{
            background: isConfigured ? "#dcfce7" : "#fef9c3",
            color: isConfigured ? "#166534" : "#854d0e",
            fontSize: "0.7rem", fontWeight: 500,
            padding: "4px 10px", borderRadius: 6,
          }}
        >
          <i className={`bi ${isConfigured ? "bi-check-circle-fill" : "bi-exclamation-circle-fill"} me-1`} />
          {isConfigured ? "Configured" : "Not configured"}
        </span>
        {onRemove && (
          <button
            type="button"
            className="btn btn-sm"
            onClick={onRemove}
            title={`Remove ${group.label}`}
            style={{ border: "1px solid #fecaca", color: "#b91c1c", background: "white", flexShrink: 0 }}
          >
            <i className="bi bi-trash" />
          </button>
        )}
      </div>

      <div className="card-body pt-3">
        <div className="row g-3">
          {group.fields.map((field) => {
            const isSet = allSettings[field.key]?.is_set ?? false;
            const show = revealed[field.key] ?? false;
            const inputType = field.secret && !show ? "password" : field.inputType ?? "text";
            const currentValue = getValue(field.key);

            return (
              <div className="col-md-6" key={field.key}>
                <label
                  className="form-label fw-medium d-flex align-items-center gap-2"
                  style={{ fontSize: "0.82rem" }}
                >
                  {field.label}
                  {isSet && (
                    <span style={{
                      background: "#dbeafe", color: "#1d4ed8",
                      fontSize: "0.65rem", fontWeight: 600,
                      padding: "1px 6px", borderRadius: 4,
                    }}>
                      SET
                    </span>
                  )}
                </label>
                <div className="input-group input-group-sm">
                  <input
                    type={inputType}
                    className="form-control"
                    placeholder={field.placeholder}
                    value={currentValue}
                    onChange={(e) => handleChange(field.key, e.target.value)}
                    style={{
                      fontSize: "0.85rem",
                      fontFamily: field.secret ? "monospace" : undefined,
                    }}
                    autoComplete="off"
                    spellCheck={false}
                  />
                  {field.secret && (
                    <button
                      type="button"
                      className="btn btn-outline-secondary"
                      tabIndex={-1}
                      onClick={() =>
                        setRevealed((prev) => ({ ...prev, [field.key]: !prev[field.key] }))
                      }
                      title={show ? "Hide" : "Reveal"}
                    >
                      <i className={`bi ${show ? "bi-eye-slash" : "bi-eye"}`} />
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <div className="d-flex justify-content-end mt-4">
          <button
            className="btn btn-primary btn-sm px-4"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? (
              <>
                <span className="spinner-border spinner-border-sm me-2" role="status" />
                Saving…
              </>
            ) : (
              <>
                <i className="bi bi-floppy-fill me-2" />
                Save {group.label}
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const { toast } = useToast();
  const router = useRouter();
  const [settingsMap, setSettingsMap] = useState<Record<string, IntegrationSetting>>({});
  const [loading, setLoading] = useState(true);
  // LLM providers are opt-in, persisted to localStorage so they survive refreshes
  const [addedLLMs, setAddedLLMs] = useState<string[]>([]);

  // Sync addedLLMs to localStorage whenever it changes
  useEffect(() => { saveLLMsToStorage(addedLLMs); }, [addedLLMs]);

  async function loadSettings() {
    try {
      const res = await fetchSettings();
      const map: Record<string, IntegrationSetting> = {};
      for (const s of res.settings) map[s.key] = s;
      setSettingsMap(map);
    } catch (err: unknown) {
      const msg = (err as Error).message ?? "";
      if (msg.startsWith("401")) {
        toast("error", "Your session has expired. Redirecting to login…");
        setTimeout(() => router.push("/login"), 1500);
        return;
      }
      toast("error", msg || "Failed to load settings");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    // Restore LLM provider choices from localStorage first so they show immediately
    setAddedLLMs(loadSavedLLMs());
    loadSettings();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-show any LLM provider that already has a saved key (in case localStorage was cleared)
  useEffect(() => {
    const configuredLLMs = LLM_PROVIDERS
      .filter((p) => p.fields.some((f) => settingsMap[f.key]?.is_set))
      .map((p) => p.id);
    if (configuredLLMs.length > 0) {
      setAddedLLMs((prev) => Array.from(new Set([...prev, ...configuredLLMs])));
    }
  }, [settingsMap]);

  const activeLLMs = LLM_PROVIDERS.filter((p) => addedLLMs.includes(p.id));
  const availableLLMs = LLM_PROVIDERS.filter((p) => !addedLLMs.includes(p.id));

  async function removeLLMCard(group: ServiceGroup) {
    const hasKey = group.fields.some((f) => settingsMap[f.key]?.is_set);
    if (hasKey && !window.confirm(`Remove ${group.label}? This clears its saved credentials.`)) return;
    try {
      if (hasKey) {
        const payload: Record<string, string | null> = {};
        for (const f of group.fields) payload[f.key] = null;
        await saveSettings(payload);
      }
      setAddedLLMs((a) => a.filter((id) => id !== group.id));
      await loadSettings();
      toast("success", `${group.label} removed`);
    } catch (err: unknown) {
      toast("error", (err as Error).message ?? "Remove failed");
    }
  }

  return (
    <>
      <Topbar title="Settings" subtitle="Configure API credentials for each integration" />
      <div className="page-body">
        {loading ? (
          <div className="text-center py-5">
            <div className="spinner-border text-primary" role="status" />
          </div>
        ) : (
          <>
            <div
              className="d-flex align-items-center gap-3 mb-4 p-3"
              style={{ background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 10 }}
            >
              <i className="bi bi-shield-lock-fill fs-5" style={{ color: "#16a34a", flexShrink: 0 }} />
              <span style={{ fontSize: "0.84rem", color: "#166534" }}>
                <strong>Your credentials are encrypted and stored securely.</strong>{" "}
                Only your team can access them — never exposed in logs or source code.
              </span>
            </div>

            {/* ── AI / LLM providers (opt-in, persisted to localStorage) ── */}
            <div className="d-flex align-items-center justify-content-between mb-3">
              <div>
                <h6 className="mb-0 fw-semibold">
                  <i className="bi bi-cpu me-2" style={{ color: "var(--primary)" }} />
                  AI / LLM Providers
                </h6>
                <div className="text-muted" style={{ fontSize: "0.78rem" }}>
                  Add the LLM APIs you want available in the Podio Agent model selector.
                </div>
              </div>
              <AddMenu
                available={availableLLMs}
                onAdd={(id) => setAddedLLMs((a) => Array.from(new Set([...a, id])))}
                label="Add LLM API"
                emptyLabel="All LLM providers added"
              />
            </div>

            {activeLLMs.length === 0 ? (
              <div
                className="text-center text-muted mb-4 p-4"
                style={{ border: "1px dashed #cbd5e1", borderRadius: 10, fontSize: "0.85rem" }}
              >
                No LLM providers added yet. Click <strong>Add LLM API</strong> to add one
                (OpenAI GPT, Anthropic Claude, Gemini, Groq, or Mistral).
              </div>
            ) : (
              activeLLMs.map((group) => (
                <ServiceCard
                  key={group.id}
                  group={group}
                  allSettings={settingsMap}
                  onSaved={loadSettings}
                  onRemove={() => removeLLMCard(group)}
                />
              ))
            )}

            {/* ── Core integrations (always visible) ── */}
            <div className="mb-3 mt-4">
              <h6 className="mb-0 fw-semibold">
                <i className="bi bi-plug-fill me-2" style={{ color: "var(--primary)" }} />
                Integrations
              </h6>
              <div className="text-muted" style={{ fontSize: "0.78rem" }}>
                External services used by the MCP tools — Podio, CRM, Slack, GitHub, and email.
              </div>
            </div>

            {GROUPS.map((group) => (
              <ServiceCard
                key={group.id}
                group={group}
                allSettings={settingsMap}
                onSaved={loadSettings}
              />
            ))}
          </>
        )}
      </div>
    </>
  );
}
