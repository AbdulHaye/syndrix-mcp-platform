"use client";

import { useEffect, useState, useCallback } from "react";
import Topbar from "@/components/Topbar";
import { fetchPromptList, runPrompt } from "@/lib/api";
import { getAuth } from "@/lib/auth";
import type { PromptTemplate, PromptRunResult } from "@/types";

const CATEGORY_META: Record<string, { label: string; color: string; bg: string; icon: string }> = {
  bd:     { label: "Business Development", color: "#10b981", bg: "rgba(16,185,129,0.1)",  icon: "bi-people-fill" },
  dev:    { label: "Software Dev",         color: "#6366f1", bg: "rgba(99,102,241,0.1)",  icon: "bi-code-slash" },
  shared: { label: "Shared",               color: "#f59e0b", bg: "rgba(245,158,11,0.1)",  icon: "bi-lightning-charge-fill" },
};

function TemplateCard({
  t,
  selected,
  onSelect,
}: {
  t: PromptTemplate;
  selected: boolean;
  onSelect: () => void;
}) {
  const meta = CATEGORY_META[t.category] ?? CATEGORY_META.shared;
  return (
    <div
      onClick={onSelect}
      style={{
        background: selected ? "rgba(99,102,241,0.08)" : "white",
        border: `1.5px solid ${selected ? "#6366f1" : "#e2e8f0"}`,
        borderRadius: 12,
        padding: "0.9rem 1rem",
        cursor: "pointer",
        transition: "all 0.15s",
      }}
    >
      <div className="d-flex align-items-center gap-2 mb-1">
        <div
          style={{
            width: 28,
            height: 28,
            borderRadius: 7,
            background: meta.bg,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <i className={`bi ${meta.icon}`} style={{ color: meta.color, fontSize: "0.75rem" }} />
        </div>
        <span
          style={{
            fontSize: "0.8rem",
            fontWeight: 600,
            color: selected ? "#6366f1" : "#1e293b",
          }}
        >
          {t.title}
        </span>
      </div>
      <p style={{ fontSize: "0.73rem", color: "#64748b", margin: 0, lineHeight: 1.4 }}>
        {t.description}
      </p>
      {t.variables.length > 0 && (
        <div className="d-flex flex-wrap gap-1 mt-2">
          {t.variables.map((v) => (
            <span
              key={v}
              style={{
                fontSize: "0.65rem",
                background: "#f1f5f9",
                color: "#475569",
                borderRadius: 4,
                padding: "1px 6px",
                fontFamily: "monospace",
              }}
            >
              {v}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function OutputCard({ result, loading }: { result: PromptRunResult | null; loading: boolean }) {
  const [copied, setCopied] = useState(false);

  function copy() {
    if (result?.output) {
      navigator.clipboard.writeText(result.output).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      });
    }
  }

  if (loading) {
    return (
      <div className="content-card">
        <div className="content-card-header">
          <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Generating…</span>
        </div>
        <div className="content-card-body">
          <div className="d-flex align-items-center gap-2 text-muted py-4">
            <span className="spinner-border spinner-border-sm" style={{ color: "#6366f1" }} />
            <span style={{ fontSize: "0.85rem" }}>Running prompt through Ollama…</span>
          </div>
        </div>
      </div>
    );
  }

  if (!result) return null;

  if (!result.success) {
    return (
      <div className="content-card">
        <div className="content-card-header">
          <span style={{ fontWeight: 600, fontSize: "0.9rem", color: "#ef4444" }}>Error</span>
        </div>
        <div className="content-card-body">
          <div className="alert alert-danger small mb-0">{result.error}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="content-card">
      <div className="content-card-header">
        <div className="d-flex align-items-center gap-2">
          <i className="bi bi-stars" style={{ color: "#6366f1", fontSize: "1rem" }} />
          <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>{result.title ?? "Output"}</span>
          {result.model && (
            <span
              style={{
                fontSize: "0.68rem",
                background: "rgba(99,102,241,0.1)",
                color: "#6366f1",
                borderRadius: 20,
                padding: "2px 8px",
                fontWeight: 600,
              }}
            >
              {result.model}
            </span>
          )}
        </div>
        <button
          className="btn btn-sm"
          style={{
            background: "rgba(99,102,241,0.08)",
            color: "#6366f1",
            border: "none",
            borderRadius: 8,
            fontSize: "0.75rem",
            fontWeight: 500,
          }}
          onClick={copy}
        >
          <i className={`bi ${copied ? "bi-check-lg" : "bi-clipboard"} me-1`} />
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>
      <div className="content-card-body">
        <pre
          style={{
            whiteSpace: "pre-wrap",
            fontFamily: "inherit",
            fontSize: "0.85rem",
            lineHeight: 1.65,
            margin: 0,
            color: "#1e293b",
          }}
        >
          {result.output}
        </pre>
      </div>
    </div>
  );
}

export default function PromptsPage() {
  const auth = getAuth();
  const role = auth?.role ?? "dev";

  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [selected, setSelected] = useState<PromptTemplate | null>(null);
  const [vars, setVars] = useState<Record<string, string>>({});
  const [result, setResult] = useState<PromptRunResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingTemplates, setLoadingTemplates] = useState(true);
  const [error, setError] = useState("");
  const [activeCategory, setActiveCategory] = useState<string>("all");
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const loadTemplates = useCallback(async () => {
    setLoadingTemplates(true);
    try {
      const res = await fetchPromptList(role);
      setTemplates(res.templates ?? []);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load prompts");
    } finally {
      setLoadingTemplates(false);
    }
  }, [role]);

  useEffect(() => { loadTemplates(); }, [loadTemplates]);

  // Variables hidden behind the Advanced toggle, keyed by template key
  const ADVANCED_VARS: Record<string, string[]> = {
    "dev.bug_triage": ["steps", "expected", "actual", "environment"],
  };

  function selectTemplate(t: PromptTemplate) {
    setSelected(t);
    setVars(Object.fromEntries(t.variables.map((v) => [v, ""])));
    setResult(null);
    setAdvancedOpen(false);
  }

  async function handleRun() {
    if (!selected) return;
    const advanced = ADVANCED_VARS[selected.key] ?? [];
    const required = selected.variables.filter((v) => !advanced.includes(v));
    const missing = required.filter((v) => !vars[v]?.trim());
    if (missing.length > 0) {
      setError(`Please fill in: ${missing.join(", ")}`);
      return;
    }
    setError("");
    setLoading(true);
    try {
      const res = await runPrompt(selected.key, vars, role);
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Prompt failed");
    } finally {
      setLoading(false);
    }
  }

  const categories = ["all", ...Array.from(new Set(templates.map((t) => t.category)))];
  const filtered = activeCategory === "all"
    ? templates
    : templates.filter((t) => t.category === activeCategory);

  return (
    <>
      <Topbar title="Prompt Packs" subtitle="AI prompt templates for every team" />

      <div className="page-body fade-in">
        {/* Header */}
        <div className="page-header mb-3">
          <div>
            <div className="page-header-title">Prompt Packs</div>
            <div className="page-header-subtitle">
              Select a template, fill in the variables, and get AI-powered output.
            </div>
          </div>
        </div>

        {/* Category filter pills */}
        <div className="d-flex flex-wrap gap-2 mb-3">
          {categories.map((cat) => {
            const meta = CATEGORY_META[cat] ?? { label: "All", color: "#64748b", bg: "#f1f5f9", icon: "" };
            const isActive = activeCategory === cat;
            return (
              <button
                key={cat}
                onClick={() => setActiveCategory(cat)}
                style={{
                  border: `1.5px solid ${isActive ? (meta.color) : "#e2e8f0"}`,
                  borderRadius: 20,
                  padding: "0.3rem 0.85rem",
                  fontSize: "0.78rem",
                  fontWeight: isActive ? 600 : 400,
                  background: isActive ? meta.bg : "white",
                  color: isActive ? meta.color : "#64748b",
                  cursor: "pointer",
                  transition: "all 0.15s",
                }}
              >
                {cat === "all" ? "All Prompts" : (meta.label || cat)}
              </button>
            );
          })}
        </div>

        <div className="row g-3">
          {/* Template list */}
          <div className="col-lg-4">
            <div className="content-card" style={{ height: "100%" }}>
              <div className="content-card-header">
                <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Templates</span>
                <span
                  style={{
                    fontSize: "0.7rem",
                    background: "rgba(99,102,241,0.1)",
                    color: "#6366f1",
                    borderRadius: 20,
                    padding: "2px 8px",
                    fontWeight: 600,
                  }}
                >
                  {filtered.length}
                </span>
              </div>
              <div className="content-card-body" style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
                {loadingTemplates && (
                  <div className="text-center text-muted py-4" style={{ fontSize: "0.85rem" }}>
                    <span className="spinner-border spinner-border-sm me-2" />Loading…
                  </div>
                )}
                {!loadingTemplates && filtered.length === 0 && (
                  <div className="empty-state py-4">
                    <i className="bi bi-lightning-charge" />
                    <div style={{ fontSize: "0.85rem" }}>No templates for this role</div>
                  </div>
                )}
                {!loadingTemplates && filtered.map((t) => (
                  <TemplateCard
                    key={t.key}
                    t={t}
                    selected={selected?.key === t.key}
                    onSelect={() => selectTemplate(t)}
                  />
                ))}
              </div>
            </div>
          </div>

          {/* Input form + output */}
          <div className="col-lg-8">
            {!selected ? (
              <div className="content-card" style={{ minHeight: 300 }}>
                <div className="content-card-body d-flex align-items-center justify-content-center" style={{ minHeight: 250 }}>
                  <div className="empty-state">
                    <i className="bi bi-arrow-left-circle" />
                    <div style={{ fontSize: "0.875rem" }}>Select a template to get started</div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="d-flex flex-column gap-3">
                {/* Variable input form */}
                <div className="content-card">
                  <div className="content-card-header">
                    <div className="d-flex align-items-center gap-2">
                      <i
                        className={`bi ${CATEGORY_META[selected.category]?.icon ?? "bi-stars"}`}
                        style={{ color: CATEGORY_META[selected.category]?.color ?? "#6366f1" }}
                      />
                      <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>{selected.title}</span>
                    </div>
                    <button
                      className="btn btn-sm"
                      style={{
                        background: "var(--primary)",
                        color: "white",
                        border: "none",
                        borderRadius: 8,
                        fontSize: "0.78rem",
                        fontWeight: 500,
                        padding: "0.35rem 1rem",
                      }}
                      onClick={handleRun}
                      disabled={loading}
                    >
                      {loading ? (
                        <><span className="spinner-border spinner-border-sm me-1" />Running…</>
                      ) : (
                        <><i className="bi bi-play-fill me-1" />Run</>
                      )}
                    </button>
                  </div>
                  <div className="content-card-body">
                    <p style={{ fontSize: "0.8rem", color: "#64748b", marginBottom: "1rem" }}>
                      {selected.description}
                    </p>

                    {error && (
                      <div
                        className="alert mb-3"
                        style={{
                          background: "rgba(239,68,68,0.08)",
                          border: "1px solid rgba(239,68,68,0.2)",
                          color: "#ef4444",
                          borderRadius: 8,
                          fontSize: "0.8rem",
                          padding: "0.6rem 0.9rem",
                        }}
                      >
                        {error}
                      </div>
                    )}

                    {selected.variables.length === 0 ? (
                      <p style={{ fontSize: "0.8rem", color: "#94a3b8" }}>
                        This template has no required variables. Click Run to execute.
                      </p>
                    ) : (() => {
                      const advanced = ADVANCED_VARS[selected.key] ?? [];
                      const required = selected.variables.filter((v) => !advanced.includes(v));
                      const LONG = ["requirements", "context", "raw_notes", "description",
                        "info", "diff_summary", "bug_description", "text", "contact_data"];

                      function renderField(v: string, isOptional = false) {
                        const isLong = LONG.includes(v);
                        return (
                          <div key={v} className={isLong ? "col-12" : "col-sm-6"}>
                            <label style={{ fontSize: "0.78rem", fontWeight: 600, color: "#475569", marginBottom: 4, display: "block" }}>
                              {v.replace(/_/g, " ")}
                              {isOptional
                                ? <span style={{ color: "#94a3b8", marginLeft: 4, fontWeight: 400 }}>(optional)</span>
                                : <span style={{ color: "#ef4444", marginLeft: 2 }}>*</span>}
                            </label>
                            {isLong ? (
                              <textarea
                                className="form-control"
                                rows={3}
                                value={vars[v] ?? ""}
                                onChange={(e) => setVars((p) => ({ ...p, [v]: e.target.value }))}
                                placeholder={`Enter ${v.replace(/_/g, " ")}…`}
                                style={{ fontSize: "0.82rem", borderRadius: 8, resize: "vertical" }}
                              />
                            ) : (
                              <input
                                type="text"
                                className="form-control"
                                value={vars[v] ?? ""}
                                onChange={(e) => setVars((p) => ({ ...p, [v]: e.target.value }))}
                                placeholder={`Enter ${v.replace(/_/g, " ")}…`}
                                style={{ fontSize: "0.82rem", borderRadius: 8 }}
                              />
                            )}
                          </div>
                        );
                      }

                      return (
                        <>
                          <div className="row g-3">
                            {required.map((v) => renderField(v, false))}
                          </div>

                          {advanced.length > 0 && (
                            <div style={{ marginTop: "1rem" }}>
                              <button
                                type="button"
                                onClick={() => setAdvancedOpen((o) => !o)}
                                style={{
                                  background: "none",
                                  border: "none",
                                  padding: 0,
                                  fontSize: "0.78rem",
                                  color: "#6366f1",
                                  fontWeight: 600,
                                  cursor: "pointer",
                                  display: "flex",
                                  alignItems: "center",
                                  gap: 4,
                                }}
                              >
                                <i className={`bi bi-chevron-${advancedOpen ? "up" : "down"}`} style={{ fontSize: "0.7rem" }} />
                                {advancedOpen ? "Hide advanced" : "Show advanced"} (optional)
                              </button>

                              {advancedOpen && (
                                <div className="row g-3 mt-1">
                                  {advanced.map((v) => renderField(v, true))}
                                </div>
                              )}
                            </div>
                          )}
                        </>
                      );
                    })()}
                  </div>
                </div>

                {/* Output */}
                <OutputCard result={result} loading={loading} />
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
