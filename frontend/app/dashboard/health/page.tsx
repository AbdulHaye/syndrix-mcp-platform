"use client";

import { useEffect, useState } from "react";
import Topbar from "@/components/Topbar";
import { fetchHealthDetailed } from "@/lib/api";
import type { HealthStatus } from "@/types";

function ServiceRow({ name, info }: { name: string; info: { status: string; detail?: string } }) {
  const isOk = info.status === "ok";
  return (
    <div className="d-flex align-items-center justify-content-between py-3 border-bottom">
      <div className="d-flex align-items-center gap-3">
        <span className={`status-dot ${isOk ? "ok" : "error"}`} />
        <div>
          <div className="fw-semibold text-capitalize">{name}</div>
          {info.detail && <div className="small text-danger mt-1">{info.detail}</div>}
        </div>
      </div>
      <span className={`badge bg-${isOk ? "success" : "danger"}`}>
        {info.status}
      </span>
    </div>
  );
}

export default function HealthPage() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const h = await fetchHealthDetailed();
      setHealth(h);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to fetch health");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { refresh(); }, []);

  const overallColor =
    health?.status === "ok" ? "success" : health?.status === "degraded" ? "warning" : "danger";

  return (
    <>
      <Topbar title="Health Status" subtitle="Live dependency checks" />
      <div className="page-body">
        <div className="d-flex align-items-center justify-content-between mb-4">
          <h5 className="fw-bold mb-0">Platform Health</h5>
          <button className="btn btn-sm btn-outline-secondary" onClick={refresh} disabled={loading}>
            <i className={`bi bi-arrow-clockwise me-1${loading ? " spin" : ""}`} />
            Refresh
          </button>
        </div>

        {loading && (
          <div className="d-flex align-items-center gap-2 text-muted">
            <span className="spinner-border spinner-border-sm" />
            Checking dependencies…
          </div>
        )}

        {error && (
          <div className="alert alert-danger">
            <i className="bi bi-exclamation-triangle me-2" />
            {error} — Is the backend running at{" "}
            <code>{process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}</code>?
          </div>
        )}

        {health && !loading && (
          <>
            <div className="row g-3 mb-4">
              <div className="col-md-4">
                <div className="stat-card text-center">
                  <div className={`display-6 fw-bold text-${overallColor}`}>
                    {health.status.toUpperCase()}
                  </div>
                  <div className="small text-muted mt-1">Overall status</div>
                </div>
              </div>
              <div className="col-md-4">
                <div className="stat-card text-center">
                  <div className="display-6 fw-bold">{health.version}</div>
                  <div className="small text-muted mt-1">Platform version</div>
                </div>
              </div>
              <div className="col-md-4">
                <div className="stat-card text-center">
                  <div className="display-6 fw-bold text-capitalize">{health.env}</div>
                  <div className="small text-muted mt-1">Environment</div>
                </div>
              </div>
            </div>

            <div className="bg-white rounded-3 border p-4">
              <h6 className="fw-bold mb-0">Dependencies</h6>
              {health.services ? (
                Object.entries(health.services).map(([name, info]) => (
                  <ServiceRow key={name} name={name} info={info} />
                ))
              ) : (
                <div className="text-muted small mt-3">
                  No detailed service info returned. Use <code>/health/detailed</code>.
                </div>
              )}
            </div>
          </>
        )}
      </div>

      <style jsx global>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        .spin { animation: spin 1s linear infinite; display: inline-block; }
      `}</style>
    </>
  );
}
