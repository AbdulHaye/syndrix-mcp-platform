"use client";

import { useState } from "react";
import Topbar from "@/components/Topbar";
import ToolCard from "@/components/ToolCard";
import { useToast } from "@/lib/toast";

type ActiveTool = "daily" | "health";

const TOOLS: { key: ActiveTool; icon: string; iconBg: string; color: string; bg: string; title: string; desc: string }[] = [
  { key: "daily",  icon: "bi-clipboard-data", iconBg: "#ffc107", color: "#f59e0b", bg: "rgba(245,158,11,0.1)", title: "Daily Report",        desc: "Team activity summary for today across all functions." },
  { key: "health", icon: "bi-heart-pulse",     iconBg: "#dc3545", color: "#dc3545", bg: "rgba(220,53,69,0.1)", title: "Client Health Score",  desc: "Composite health score for a client account." },
];

export default function MgmtPage() {
  const toast = useToast();
  const [active, setActive] = useState<ActiveTool | null>(null);
  const [result, setResult] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [clientId, setClientId] = useState("");

  function openTool(tool: ActiveTool) {
    if (active !== tool) { setResult(null); setLoading(false); }
    setActive(tool);
  }

  function goBack() { setActive(null); setResult(null); setLoading(false); }

  async function callTool(tool: string, args: Record<string, unknown>) {
    setLoading(true);
    setResult(null);
    try {
      const { invokeTool } = await import("@/lib/api");
      const res = await invokeTool({ tool, args });
      setResult(res);
      if ((res as { success?: boolean }).success) toast.success("Tool completed successfully");
      else toast.error((res as { error?: string }).error ?? "Tool returned an error");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Tool call failed");
    } finally {
      setLoading(false);
    }
  }

  const activeTool = TOOLS.find((t) => t.key === active);
  const dailyResult = result as Record<string, unknown> | null;

  /* ── Card grid view ── */
  if (!active) {
    return (
      <>
        <Topbar title="Management" subtitle="Reports · Analytics · Client Health" />
        <div className="page-body fade-in">
          <div className="row g-3">
            {TOOLS.map((t) => (
              <div key={t.key} className="col-sm-6 col-lg-4">
                <ToolCard icon={t.icon} iconBg={t.iconBg} title={t.title} description={t.desc} onClick={() => openTool(t.key)} />
              </div>
            ))}
          </div>
        </div>
      </>
    );
  }

  /* ── Split panel view ── */
  return (
    <>
      <Topbar title="Management" subtitle="Reports · Analytics · Client Health" />
      <div className="page-body fade-in">
        <div className="tool-split">

          {/* Left nav */}
          <nav className="tool-nav">
            <button className="tool-nav-back" onClick={goBack}>
              <i className="bi bi-arrow-left" />
              All Tools
            </button>
            {TOOLS.map((t) => (
              <button key={t.key} className={`tool-nav-item${active === t.key ? " active" : ""}`} onClick={() => openTool(t.key)}>
                <div className="tool-nav-icon" style={{ background: active === t.key ? "rgba(99,102,241,0.12)" : t.bg }}>
                  <i className={`bi ${t.icon}`} style={{ color: active === t.key ? "var(--primary)" : t.color }} />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "0.845rem", fontWeight: 600, color: active === t.key ? "var(--primary)" : "var(--text)", lineHeight: 1.2 }}>{t.title}</div>
                  <div style={{ fontSize: "0.71rem", color: "var(--text-muted)", marginTop: "0.15rem", lineHeight: 1.3 }}>{t.desc}</div>
                </div>
                {active === t.key && <i className="bi bi-chevron-right" style={{ fontSize: "0.65rem", color: "var(--primary)", flexShrink: 0 }} />}
              </button>
            ))}
          </nav>

          {/* Right detail */}
          <div className="tool-detail">
            <div className="tool-detail-header">
              <div className="tool-detail-header-icon" style={{ background: activeTool!.bg }}>
                <i className={`bi ${activeTool!.icon}`} style={{ color: activeTool!.color }} />
              </div>
              <div>
                <div className="tool-detail-title">{activeTool!.title}</div>
                <div className="tool-detail-subtitle">{activeTool!.desc}</div>
              </div>
            </div>

            <div className="tool-detail-body">
              {active === "daily" && (
                <>
                  <p className="text-muted small mb-3">Generates today&apos;s activity report across BD, Dev, and Management.</p>
                  <button className="btn btn-primary mb-3" style={{ background: "var(--primary)", border: "none" }} onClick={() => callTool("report.team.daily", {})} disabled={loading}>
                    {loading ? <><span className="spinner-border spinner-border-sm me-2" />Loading…</> : <><i className="bi bi-play-fill me-2" />Generate Report</>}
                  </button>
                  {dailyResult && (
                    <div className="row g-3">
                      <div className="col-12">
                        <div className="d-flex align-items-center gap-2 mb-2">
                          <span className="badge bg-secondary">{String(dailyResult.report_date)}</span>
                          <span className="text-muted small">Generated at {String(dailyResult.generated_at)}</span>
                        </div>
                      </div>
                      {Object.entries((dailyResult.teams as Record<string, Record<string, number>>) ?? {}).map(([team, metrics]) => (
                        <div key={team} className="col-md-4">
                          <div className="content-card p-3">
                            <div className="fw-semibold text-capitalize mb-2 small">{team.replace("_", " ")}</div>
                            {Object.entries(metrics).map(([k, v]) => (
                              <div key={k} className="d-flex justify-content-between small border-bottom py-1">
                                <span className="text-muted text-capitalize">{k.replace(/_/g, " ")}</span>
                                <span className="fw-bold">{String(v)}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                      {((dailyResult.highlights as string[]) ?? []).length > 0 && (
                        <div className="col-12">
                          <div className="alert alert-info small">
                            {(dailyResult.highlights as string[]).map((h, i) => (
                              <div key={i}><i className="bi bi-info-circle me-2" />{h}</div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}

              {active === "health" && (
                <>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Client ID</label>
                    <input className="form-control" value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="e.g. client-001" />
                  </div>
                  <button className="btn btn-primary mb-3" style={{ background: "var(--primary)", border: "none" }} onClick={() => callTool("client.health.score", { client_id: clientId })} disabled={loading || !clientId}>
                    {loading ? <><span className="spinner-border spinner-border-sm me-2" />Calculating…</> : <><i className="bi bi-play-fill me-2" />Get Score</>}
                  </button>
                  {result && <pre className="result-panel">{JSON.stringify(result, null, 2)}</pre>}
                </>
              )}
            </div>
          </div>

        </div>
      </div>
    </>
  );
}
