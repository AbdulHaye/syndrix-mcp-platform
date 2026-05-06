"use client";

import { useEffect, useState } from "react";
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

const GROUPS: ServiceGroup[] = [
  {
    id: "podio",
    label: "Podio CRM",
    icon: "bi-kanban-fill",
    color: "#10b981",
    description: "OAuth client-credentials for Podio CRM contacts and notes.",
    fields: [
      { key: "podio_client_id",     label: "Client ID",     placeholder: "your-podio-client-id" },
      { key: "podio_client_secret", label: "Client Secret", placeholder: "your-podio-client-secret", secret: true },
      { key: "podio_app_id",        label: "App ID",        placeholder: "123456" },
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

function ServiceCard({
  group,
  allSettings,
  onSaved,
}: {
  group: ServiceGroup;
  allSettings: Record<string, IntegrationSetting>;
  onSaved: () => void;
}) {
  const { toast } = useToast();
  // Only track what the user has typed — not yet saved
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);

  // Source of truth: user's pending edit if present, otherwise the decrypted DB value
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
      setOverrides({});   // clear pending edits; allSettings will refresh via onSaved
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
  const [settingsMap, setSettingsMap] = useState<Record<string, IntegrationSetting>>({});
  const [loading, setLoading] = useState(true);

  async function loadSettings() {
    try {
      const res = await fetchSettings();   // values arrive already decrypted
      const map: Record<string, IntegrationSetting> = {};
      for (const s of res.settings) map[s.key] = s;
      setSettingsMap(map);
    } catch (err: unknown) {
      toast("error", (err as Error).message ?? "Failed to load settings");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadSettings(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
