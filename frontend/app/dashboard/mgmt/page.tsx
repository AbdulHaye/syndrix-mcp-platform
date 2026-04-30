"use client";

import { useState } from "react";
import Topbar from "@/components/Topbar";
import ToolCard from "@/components/ToolCard";
import EmptyState from "@/components/EmptyState";

type ActiveTool = "daily" | "health" | null;

export default function MgmtPage() {
  const [active, setActive] = useState<ActiveTool>(null);
  const [result, setResult] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [clientId, setClientId] = useState("");

  function reset() { setResult(null); setError(""); setLoading(false); }
  function openTool(tool: ActiveTool) { reset(); setActive(tool); }

  async function callTool(tool: string, args: Record<string, unknown>) {
    setLoading(true); setError(""); setResult(null);
    try {
      const { invokeTool } = await import("@/lib/api");
      setResult(await invokeTool({ tool, args }));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Tool call failed");
    } finally { setLoading(false); }
  }

  const dailyResult = result as Record<string, unknown> | null;

  return (
    <>
      <Topbar title="Management" subtitle="Reports · Analytics · Client Health" />
      <div className="page-body">
        <div className="row g-3 mb-4">
          <div className="col-sm-6 col-lg-4">
            <ToolCard icon="bi-clipboard-data" iconBg="#ffc107" title="Daily Report" description="Team activity summary for today across all functions." onClick={() => openTool("daily")} badge={active === "daily" ? "active" : undefined} badgeColor="primary" />
          </div>
          <div className="col-sm-6 col-lg-4">
            <ToolCard icon="bi-heart-pulse" iconBg="#dc3545" title="Client Health Score" description="Composite health score for a client account." onClick={() => openTool("health")} badge={active === "health" ? "active" : undefined} badgeColor="primary" />
          </div>
        </div>

        {active === "daily" && (
          <div className="bg-white rounded-3 border p-4">
            <div className="d-flex align-items-center justify-content-between mb-3">
              <h6 className="fw-bold mb-0 d-flex align-items-center gap-2"><i className="bi bi-clipboard-data text-warning" />Daily Report</h6>
              <button className="btn btn-sm btn-outline-secondary" onClick={() => setActive(null)}><i className="bi bi-x" /></button>
            </div>
            <p className="text-muted small">Generates today&apos;s activity report across BD, Dev, and Management.</p>
            <button className="btn btn-warning mb-3" onClick={() => callTool("report.team.daily", {})} disabled={loading}>
              {loading ? <><span className="spinner-border spinner-border-sm me-2" />Loading…</> : <><i className="bi bi-play-fill me-2" />Generate Report</>}
            </button>

            {error && <div className="alert alert-danger small">{error}</div>}

            {dailyResult && (
              <div className="row g-3">
                <div className="col-12">
                  <div className="d-flex align-items-center gap-2 mb-3">
                    <span className="badge bg-secondary">{String(dailyResult.report_date)}</span>
                    <span className="text-muted small">Generated at {String(dailyResult.generated_at)}</span>
                  </div>
                </div>
                {Object.entries((dailyResult.teams as Record<string, Record<string, number>>) ?? {}).map(([team, metrics]) => (
                  <div key={team} className="col-md-4">
                    <div className="stat-card">
                      <div className="fw-semibold text-capitalize mb-2">{team.replace("_", " ")}</div>
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
                      {(dailyResult.highlights as string[]).map((h, i) => <div key={i}><i className="bi bi-info-circle me-2" />{h}</div>)}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {active === "health" && (
          <div className="bg-white rounded-3 border p-4">
            <div className="d-flex align-items-center justify-content-between mb-3">
              <h6 className="fw-bold mb-0 d-flex align-items-center gap-2"><i className="bi bi-heart-pulse text-danger" />Client Health Score</h6>
              <button className="btn btn-sm btn-outline-secondary" onClick={() => setActive(null)}><i className="bi bi-x" /></button>
            </div>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Client ID</label>
              <input className="form-control" value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="e.g. client-001" />
            </div>
            <button className="btn btn-danger mb-3" onClick={() => callTool("client.health.score", { client_id: clientId })} disabled={loading || !clientId}>
              {loading ? <><span className="spinner-border spinner-border-sm me-2" />Calculating…</> : <><i className="bi bi-play-fill me-2" />Get Score</>}
            </button>
            {error && <div className="alert alert-danger small">{error}</div>}
            {result && <pre className="result-panel">{JSON.stringify(result, null, 2)}</pre>}
          </div>
        )}

        {!active && (
          <div className="bg-white rounded-3 border">
            <EmptyState icon="bi-cursor" title="Select a tool above" />
          </div>
        )}
      </div>
    </>
  );
}
