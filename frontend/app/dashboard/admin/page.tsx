"use client";

import { useEffect, useState } from "react";
import Topbar from "@/components/Topbar";
import { fetchAdminTools, fetchAuditLog, fetchMetrics } from "@/lib/api";
import type { AuditEntry } from "@/types";

type Tab = "tools" | "audit" | "metrics";

export default function AdminPage() {
  const [tab, setTab] = useState<Tab>("tools");
  const [tools, setTools] = useState<{ name: string; description: string }[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [metrics, setMetrics] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function loadTab(t: Tab) {
    setTab(t);
    setLoading(true);
    setError("");
    try {
      if (t === "tools") {
        const res = await fetchAdminTools();
        setTools(res.tools);
      } else if (t === "audit") {
        const res = await fetchAuditLog(100);
        setAudit(res.entries);
      } else {
        const res = await fetchMetrics();
        setMetrics(res);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadTab("tools"); }, []);

  return (
    <>
      <Topbar title="Admin" subtitle="Tool registry · Audit log · Metrics" />
      <div className="page-body">
        <div className="d-flex align-items-center justify-content-between mb-3">
          <ul className="nav nav-tabs border-0 gap-2">
            {(["tools", "audit", "metrics"] as Tab[]).map((t) => (
              <li key={t} className="nav-item">
                <button className={`nav-link${tab === t ? " active fw-semibold" : ""}`} onClick={() => loadTab(t)}>
                  {t.charAt(0).toUpperCase() + t.slice(1)}
                </button>
              </li>
            ))}
          </ul>
          <button className="btn btn-sm btn-outline-secondary" onClick={() => loadTab(tab)} disabled={loading}>
            <i className="bi bi-arrow-clockwise me-1" />Refresh
          </button>
        </div>

        {error && <div className="alert alert-danger small">{error}</div>}
        {loading && <div className="d-flex align-items-center gap-2 text-muted py-4"><span className="spinner-border spinner-border-sm" /> Loading…</div>}

        {/* Tools tab */}
        {!loading && tab === "tools" && (
          <div className="bg-white rounded-3 border">
            <div className="p-3 border-bottom d-flex align-items-center justify-content-between">
              <span className="fw-semibold">Registered Tools</span>
              <span className="badge bg-primary">{tools.length}</span>
            </div>
            <div className="table-responsive">
              <table className="table table-hover mb-0 small">
                <thead className="table-light">
                  <tr>
                    <th>Tool Name</th>
                    <th>Description</th>
                  </tr>
                </thead>
                <tbody>
                  {tools.map((t) => (
                    <tr key={t.name}>
                      <td><code className="text-primary">{t.name}</code></td>
                      <td className="text-muted">{t.description}</td>
                    </tr>
                  ))}
                  {tools.length === 0 && (
                    <tr><td colSpan={2} className="text-center text-muted py-4">No tools found</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Audit tab */}
        {!loading && tab === "audit" && (
          <div className="bg-white rounded-3 border">
            <div className="p-3 border-bottom d-flex align-items-center justify-content-between">
              <span className="fw-semibold">Audit Log</span>
              <span className="badge bg-secondary">{audit.length} entries</span>
            </div>
            <div className="table-responsive">
              <table className="table table-hover mb-0 small">
                <thead className="table-light">
                  <tr>
                    <th>Time</th>
                    <th>Team</th>
                    <th>Tool</th>
                    <th>Status</th>
                    <th>Latency</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((e) => (
                    <tr key={e.id}>
                      <td className="text-muted">{new Date(e.timestamp).toLocaleTimeString()}</td>
                      <td><span className="badge bg-light text-dark border">{e.team_name}</span></td>
                      <td><code>{e.tool_name}</code></td>
                      <td>
                        <span className={`badge bg-${e.success ? "success" : "danger"}`}>
                          {e.success ? "OK" : "ERR"}
                        </span>
                      </td>
                      <td>{e.latency_ms.toFixed(0)} ms</td>
                    </tr>
                  ))}
                  {audit.length === 0 && (
                    <tr><td colSpan={5} className="text-center text-muted py-4">No audit entries yet</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Metrics tab */}
        {!loading && tab === "metrics" && (
          <div className="row g-3">
            {Object.entries(metrics).map(([k, v]) => (
              <div key={k} className="col-sm-6 col-lg-4">
                <div className="stat-card">
                  <div className="stat-value">{String(v)}</div>
                  <div className="stat-label text-capitalize">{k.replace(/_/g, " ")}</div>
                </div>
              </div>
            ))}
            {Object.keys(metrics).length === 0 && (
              <div className="col-12"><div className="text-center text-muted py-4">No metrics available</div></div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
