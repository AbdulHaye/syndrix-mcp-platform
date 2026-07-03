"use client";

import { useEffect, useState } from "react";
import Topbar from "@/components/Topbar";
import { fetchHealthDetailed } from "@/lib/api";
import { getAuth, getRoleLabel } from "@/lib/auth";
import type { HealthStatus } from "@/types";

const SERVICE_ICONS: Record<string, string> = {
  database:  "bi-database-fill",
  redis:     "bi-lightning-fill",
  ollama:    "bi-cpu-fill",
  postgres:  "bi-database-fill",
  celery:    "bi-gear-fill",
};

function ServiceCard({ name, info }: { name: string; info: { status: string; detail?: string } }) {
  const ok = info.status === "ok";
  const degraded = info.status === "degraded";
  const dotCls = ok ? "ok" : degraded ? "degraded" : "error";
  const icon = SERVICE_ICONS[name.toLowerCase()] ?? "bi-hdd-network-fill";

  return (
    <div className="service-row">
      <div className="d-flex align-items-center gap-3">
        <div
          style={{
            width: 38,
            height: 38,
            borderRadius: 9,
            background: ok ? "rgba(16,185,129,0.1)" : degraded ? "rgba(245,158,11,0.1)" : "rgba(239,68,68,0.1)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <i
            className={`bi ${icon}`}
            style={{ color: ok ? "#10b981" : degraded ? "#f59e0b" : "#ef4444", fontSize: "1rem" }}
          />
        </div>
        <div>
          <div className="fw-semibold text-capitalize" style={{ fontSize: "0.875rem" }}>{name}</div>
          {info.detail && (
            <div className="small mt-1" style={{ color: "#ef4444", fontSize: "0.75rem" }}>{info.detail}</div>
          )}
        </div>
      </div>
      <div className="d-flex align-items-center gap-2">
        <span className={`service-dot ${dotCls}`} />
        <span
          style={{
            fontSize: "0.72rem",
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            color: ok ? "#10b981" : degraded ? "#f59e0b" : "#ef4444",
          }}
        >
          {info.status}
        </span>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  // getAuth() (localStorage) and new Date() differ between SSR and the client, which
  // causes hydration mismatches. Defer both to after mount.
  const [mounted, setMounted] = useState(false);
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setMounted(true);
    setNow(new Date());
  }, []);
  const auth = mounted ? getAuth() : null;
  const role = auth?.role ?? "dev";
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    setLoading(true);
    try {
      const h = await fetchHealthDetailed();
      setHealth(h);
    } catch {
      setHealth({ status: "error", env: "unknown", version: "?" });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { refresh(); }, []);

  const overallOk = health?.status === "ok";
  const overallDegraded = health?.status === "degraded";
  const statusColor = overallOk ? "#10b981" : overallDegraded ? "#f59e0b" : "#ef4444";
  const statusBg    = overallOk ? "rgba(16,185,129,0.1)" : overallDegraded ? "rgba(245,158,11,0.1)" : "rgba(239,68,68,0.1)";

  const serviceCount = health?.services ? Object.keys(health.services).length : 0;
  const healthyCount = health?.services
    ? Object.values(health.services).filter((s) => s.status === "ok").length
    : 0;

  const timeStr = now?.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) ?? "";
  const dateStr = now?.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" }) ?? "";
  const subtitle = now ? `${dateStr} · ${timeStr}` : "Syndrix AI Hub";

  return (
    <>
      <Topbar title="Overview" subtitle={subtitle} />

      <div className="page-body fade-in">
        {/* ── Page header ── */}
        <div className="page-header">
          <div>
            <div className="page-header-title">
              Welcome back, {auth?.team ?? "Guest"}
            </div>
            <div className="page-header-subtitle">
              {getRoleLabel(role)} · Syndrix AI Hub
            </div>
          </div>
          <button
            className="btn btn-sm d-flex align-items-center gap-2"
            style={{
              background: "var(--primary)",
              color: "white",
              border: "none",
              borderRadius: "var(--radius-sm)",
              padding: "0.45rem 1rem",
              fontSize: "0.8rem",
              fontWeight: 500,
            }}
            onClick={refresh}
            disabled={loading}
          >
            <i className={`bi bi-arrow-clockwise${loading ? " spin" : ""}`} />
            Refresh
          </button>
        </div>

        {/* ── Metric cards ── */}
        <div className="row g-3 mb-4">
          <div className="col-6 col-lg-3">
            <div className="metric-card h-100">
              <div className="metric-icon" style={{ background: statusBg }}>
                <i className="bi bi-activity" style={{ color: statusColor }} />
              </div>
              {loading ? (
                <div className="skeleton" style={{ height: 28, width: 80, marginBottom: 6 }} />
              ) : (
                <div className="metric-value" style={{ fontSize: "1.6rem", color: statusColor }}>
                  {health?.status?.toUpperCase() ?? "—"}
                </div>
              )}
              <div className="metric-label">Platform Status</div>
            </div>
          </div>

          <div className="col-6 col-lg-3">
            <div className="metric-card h-100">
              <div className="metric-icon" style={{ background: "rgba(99,102,241,0.1)" }}>
                <i className="bi bi-hdd-stack-fill" style={{ color: "#6366f1" }} />
              </div>
              {loading ? (
                <div className="skeleton" style={{ height: 28, width: 60, marginBottom: 6 }} />
              ) : (
                <div className="metric-value" style={{ fontSize: "1.6rem" }}>
                  {healthyCount}<span style={{ fontSize: "1rem", fontWeight: 400, color: "var(--text-muted)" }}>/{serviceCount}</span>
                </div>
              )}
              <div className="metric-label">Services Healthy</div>
            </div>
          </div>

          <div className="col-6 col-lg-3">
            <div className="metric-card h-100">
              <div className="metric-icon" style={{ background: "rgba(16,185,129,0.1)" }}>
                <i className="bi bi-person-badge-fill" style={{ color: "#10b981" }} />
              </div>
              <div className="metric-value" style={{ fontSize: "1.6rem" }}>
                {getRoleLabel(role)}
              </div>
              <div className="metric-label">Your Role</div>
            </div>
          </div>

          <div className="col-6 col-lg-3">
            <div className="metric-card h-100">
              <div className="metric-icon" style={{ background: "rgba(245,158,11,0.1)" }}>
                <i className="bi bi-tag-fill" style={{ color: "#f59e0b" }} />
              </div>
              {loading ? (
                <div className="skeleton" style={{ height: 28, width: 50, marginBottom: 6 }} />
              ) : (
                <div className="metric-value" style={{ fontSize: "1.6rem" }}>
                  {health?.version ?? "—"}
                </div>
              )}
              <div className="metric-label">Version · {health?.env ?? "…"}</div>
            </div>
          </div>
        </div>

        {/* ── Service health ── */}
        <div className="content-card">
          <div className="content-card-header">
            <div className="d-flex align-items-center gap-2">
              <i className="bi bi-heartbeat" style={{ color: "var(--primary)", fontSize: "1rem" }} />
              <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Dependency Health</span>
            </div>
            {!loading && health && (
              <span
                className="badge"
                style={{
                  background: statusBg,
                  color: statusColor,
                  fontWeight: 600,
                  fontSize: "0.7rem",
                  padding: "0.3rem 0.65rem",
                  borderRadius: 20,
                }}
              >
                {healthyCount}/{serviceCount} healthy
              </span>
            )}
          </div>

          <div className="content-card-body">
            {loading && (
              <div>
                {[1, 2, 3, 4].map((i) => (
                  <div key={i} className="service-row">
                    <div className="d-flex align-items-center gap-3">
                      <div className="skeleton" style={{ width: 38, height: 38, borderRadius: 9 }} />
                      <div>
                        <div className="skeleton" style={{ width: 100, height: 14, marginBottom: 5 }} />
                        <div className="skeleton" style={{ width: 60, height: 11 }} />
                      </div>
                    </div>
                    <div className="skeleton" style={{ width: 50, height: 20, borderRadius: 10 }} />
                  </div>
                ))}
              </div>
            )}

            {!loading && health?.services && Object.entries(health.services).map(([name, info]) => (
              <ServiceCard key={name} name={name} info={info} />
            ))}

            {!loading && !health?.services && (
              <div className="empty-state py-4">
                <i className="bi bi-wifi-off" />
                <div style={{ fontSize: "0.875rem" }}>
                  Could not reach backend. Is the server running at{" "}
                  <code style={{ fontSize: "0.8rem" }}>
                    {process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}
                  </code>?
                </div>
              </div>
            )}
          </div>
        </div>

      </div>
    </>
  );
}
