"use client";

import { useEffect, useState } from "react";
import Topbar from "@/components/Topbar";
import { fetchAdminTools, fetchAuditLog, fetchMetrics, listUsers, createUser, updateUser, deactivateUser } from "@/lib/api";
import type { AuditEntry, User, CreateUserRequest, TeamRole } from "@/types";

type Tab = "tools" | "audit" | "metrics" | "users";

interface MetricsData {
  uptime_seconds?: number;
  tools_registered?: number;
  resources_registered?: number;
  prompts_registered?: number;
  audit_entries_tracked?: number;
  requests_total?: number;
  successes?: number;
  failures?: number;
  error_rate_pct?: number;
  avg_latency_ms?: number;
  top_tools?: Record<string, number>;
  top_teams?: Record<string, number>;
  [key: string]: unknown;
}

function StatCard({ label, value, icon, color }: { label: string; value: string | number; icon: string; color: string }) {
  return (
    <div className="metric-card">
      <div className="metric-icon" style={{ background: `${color}1a` }}>
        <i className={`bi ${icon}`} style={{ color, fontSize: "1rem" }} />
      </div>
      <div className="metric-value" style={{ fontSize: "1.4rem" }}>{value}</div>
      <div className="metric-label">{label}</div>
    </div>
  );
}

function formatUptime(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${s % 60}s`;
}

const ROLES: TeamRole[] = ["bd", "dev", "mgmt", "admin"];
const ROLE_LABELS: Record<TeamRole, string> = { bd: "BD", dev: "Dev", mgmt: "Mgmt", admin: "Admin" };
const ROLE_COLORS: Record<TeamRole, string> = { bd: "#10b981", dev: "#6366f1", mgmt: "#f59e0b", admin: "#ef4444" };

function RoleBadge({ role }: { role: string }) {
  const c = ROLE_COLORS[role as TeamRole] ?? "#64748b";
  return (
    <span style={{ fontSize: "0.68rem", background: `${c}18`, color: c, borderRadius: 4, padding: "2px 8px", fontWeight: 600 }}>
      {ROLE_LABELS[role as TeamRole] ?? role}
    </span>
  );
}

export default function AdminPage() {
  const [tab, setTab] = useState<Tab>("tools");
  const [tools, setTools] = useState<{ name: string; description: string }[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [metrics, setMetrics] = useState<MetricsData>({});
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Create user form state
  const [showCreate, setShowCreate] = useState(false);
  const [newUser, setNewUser] = useState<CreateUserRequest>({ email: "", password: "", full_name: "", team_name: "", role: "dev" });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

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
      } else if (t === "users") {
        const res = await listUsers();
        setUsers(res.users);
      } else {
        const res = await fetchMetrics();
        setMetrics(res as MetricsData);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  async function handleCreateUser(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCreateError("");
    try {
      await createUser(newUser);
      setShowCreate(false);
      setNewUser({ email: "", password: "", full_name: "", team_name: "", role: "dev" });
      await loadTab("users");
    } catch (err: unknown) {
      setCreateError(err instanceof Error ? err.message : "Failed to create user");
    } finally {
      setCreating(false);
    }
  }

  async function handleToggleActive(user: User) {
    try {
      if (user.is_active) {
        await deactivateUser(user.id);
      } else {
        await updateUser(user.id, { is_active: true });
      }
      await loadTab("users");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update user");
    }
  }

  async function handleRoleChange(user: User, role: TeamRole) {
    try {
      await updateUser(user.id, { role });
      await loadTab("users");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update role");
    }
  }

  useEffect(() => { loadTab("tools"); }, []);

  const tabMeta: { key: Tab; label: string; icon: string }[] = [
    { key: "tools",   label: "Tools",   icon: "bi-tools" },
    { key: "users",   label: "Users",   icon: "bi-people-fill" },
    { key: "audit",   label: "Audit",   icon: "bi-clock-history" },
    { key: "metrics", label: "Metrics", icon: "bi-bar-chart-line" },
  ];

  return (
    <>
      <Topbar title="Admin" subtitle="Tool registry · Audit log · Live metrics" />
      <div className="page-body fade-in">
        {/* Tab bar */}
        <div className="d-flex align-items-center justify-content-between mb-3">
          <div className="d-flex gap-2">
            {tabMeta.map(({ key, label, icon }) => (
              <button
                key={key}
                onClick={() => loadTab(key)}
                style={{
                  border: `1.5px solid ${tab === key ? "#6366f1" : "#e2e8f0"}`,
                  borderRadius: 8,
                  padding: "0.4rem 0.9rem",
                  fontSize: "0.8rem",
                  fontWeight: tab === key ? 600 : 400,
                  background: tab === key ? "rgba(99,102,241,0.08)" : "white",
                  color: tab === key ? "#6366f1" : "#64748b",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <i className={`bi ${icon}`} style={{ fontSize: "0.85rem" }} />
                {label}
              </button>
            ))}
          </div>
          <button
            className="btn btn-sm d-flex align-items-center gap-2"
            style={{ border: "1.5px solid #e2e8f0", borderRadius: 8, fontSize: "0.78rem", background: "white" }}
            onClick={() => loadTab(tab)}
            disabled={loading}
          >
            <i className={`bi bi-arrow-clockwise${loading ? " spin" : ""}`} />
            Refresh
          </button>
        </div>

        {error && (
          <div className="alert mb-3" style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444", borderRadius: 8, fontSize: "0.8rem" }}>
            {error}
          </div>
        )}

        {loading && (
          <div className="d-flex align-items-center gap-2 text-muted py-4">
            <span className="spinner-border spinner-border-sm" style={{ color: "#6366f1" }} />
            <span style={{ fontSize: "0.85rem" }}>Loading…</span>
          </div>
        )}

        {/* ── Tools tab ── */}
        {!loading && tab === "tools" && (
          <div className="content-card">
            <div className="content-card-header">
              <div className="d-flex align-items-center gap-2">
                <i className="bi bi-tools" style={{ color: "#6366f1" }} />
                <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Registered Tools</span>
              </div>
              <span style={{ fontSize: "0.7rem", background: "rgba(99,102,241,0.1)", color: "#6366f1", borderRadius: 20, padding: "2px 8px", fontWeight: 600 }}>
                {tools.length}
              </span>
            </div>
            <div className="content-card-body" style={{ padding: 0 }}>
              <div className="table-responsive">
                <table className="table table-hover mb-0" style={{ fontSize: "0.82rem" }}>
                  <thead style={{ background: "#f8fafc" }}>
                    <tr>
                      <th style={{ padding: "0.65rem 1rem", fontWeight: 600, color: "#475569", borderBottom: "1px solid #e2e8f0" }}>Tool Name</th>
                      <th style={{ padding: "0.65rem 1rem", fontWeight: 600, color: "#475569", borderBottom: "1px solid #e2e8f0" }}>Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tools.map((t) => (
                      <tr key={t.name}>
                        <td style={{ padding: "0.65rem 1rem" }}>
                          <code style={{ fontSize: "0.78rem", color: "#6366f1", background: "rgba(99,102,241,0.08)", borderRadius: 4, padding: "1px 6px" }}>
                            {t.name}
                          </code>
                        </td>
                        <td style={{ padding: "0.65rem 1rem", color: "#64748b" }}>{t.description}</td>
                      </tr>
                    ))}
                    {tools.length === 0 && (
                      <tr><td colSpan={2} style={{ padding: "2rem", textAlign: "center", color: "#94a3b8" }}>No tools found</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* ── Users tab ── */}
        {!loading && tab === "users" && (
          <div className="d-flex flex-column gap-3">
            {/* Create user form */}
            {showCreate ? (
              <div className="content-card">
                <div className="content-card-header">
                  <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>New User</span>
                  <button onClick={() => setShowCreate(false)} style={{ background: "none", border: "none", cursor: "pointer", color: "#94a3b8" }}>
                    <i className="bi bi-x-lg" />
                  </button>
                </div>
                <div className="content-card-body">
                  <form onSubmit={handleCreateUser}>
                    <div className="row g-3">
                      <div className="col-md-6">
                        <label className="form-label small fw-semibold">Email</label>
                        <input type="email" className="form-control form-control-sm" required value={newUser.email} onChange={(e) => setNewUser({ ...newUser, email: e.target.value })} />
                      </div>
                      <div className="col-md-6">
                        <label className="form-label small fw-semibold">Password</label>
                        <input type="password" className="form-control form-control-sm" required minLength={8} value={newUser.password} onChange={(e) => setNewUser({ ...newUser, password: e.target.value })} />
                      </div>
                      <div className="col-md-6">
                        <label className="form-label small fw-semibold">Full Name</label>
                        <input type="text" className="form-control form-control-sm" value={newUser.full_name ?? ""} onChange={(e) => setNewUser({ ...newUser, full_name: e.target.value })} />
                      </div>
                      <div className="col-md-3">
                        <label className="form-label small fw-semibold">Team Name</label>
                        <input type="text" className="form-control form-control-sm" required placeholder="e.g. bd_team" value={newUser.team_name} onChange={(e) => setNewUser({ ...newUser, team_name: e.target.value })} />
                      </div>
                      <div className="col-md-3">
                        <label className="form-label small fw-semibold">Role</label>
                        <select className="form-select form-select-sm" value={newUser.role} onChange={(e) => setNewUser({ ...newUser, role: e.target.value as TeamRole })}>
                          {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                        </select>
                      </div>
                    </div>
                    {createError && (
                      <div className="alert mt-2 py-2 small" style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444", borderRadius: 8 }}>
                        {createError}
                      </div>
                    )}
                    <div className="d-flex gap-2 mt-3">
                      <button type="submit" className="btn btn-primary btn-sm" disabled={creating}>
                        {creating ? <><span className="spinner-border spinner-border-sm me-1" />Creating…</> : "Create User"}
                      </button>
                      <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setShowCreate(false)}>Cancel</button>
                    </div>
                  </form>
                </div>
              </div>
            ) : (
              <div className="d-flex justify-content-end">
                <button
                  className="btn btn-primary btn-sm d-flex align-items-center gap-2"
                  style={{ borderRadius: 8, fontSize: "0.8rem" }}
                  onClick={() => setShowCreate(true)}
                >
                  <i className="bi bi-person-plus-fill" />
                  Add User
                </button>
              </div>
            )}

            <div className="content-card">
              <div className="content-card-header">
                <div className="d-flex align-items-center gap-2">
                  <i className="bi bi-people-fill" style={{ color: "#6366f1" }} />
                  <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Users</span>
                </div>
                <span style={{ fontSize: "0.7rem", background: "rgba(99,102,241,0.1)", color: "#6366f1", borderRadius: 20, padding: "2px 8px", fontWeight: 600 }}>
                  {users.length}
                </span>
              </div>
              <div className="content-card-body" style={{ padding: 0 }}>
                <div className="table-responsive">
                  <table className="table table-hover mb-0" style={{ fontSize: "0.82rem" }}>
                    <thead style={{ background: "#f8fafc" }}>
                      <tr>
                        {["Name / Email", "Team", "Role", "Status", "Created", "Actions"].map((h) => (
                          <th key={h} style={{ padding: "0.65rem 1rem", fontWeight: 600, color: "#475569", borderBottom: "1px solid #e2e8f0" }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {users.map((u) => (
                        <tr key={u.id} style={{ opacity: u.is_active ? 1 : 0.5 }}>
                          <td style={{ padding: "0.65rem 1rem" }}>
                            <div style={{ fontWeight: 500, color: "#1e293b" }}>{u.full_name || "—"}</div>
                            <div style={{ fontSize: "0.72rem", color: "#94a3b8" }}>{u.email}</div>
                          </td>
                          <td style={{ padding: "0.65rem 1rem", color: "#64748b" }}>{u.team_name}</td>
                          <td style={{ padding: "0.65rem 1rem" }}>
                            <select
                              className="form-select form-select-sm"
                              style={{ fontSize: "0.75rem", padding: "2px 6px", width: "auto", border: "1px solid #e2e8f0" }}
                              value={u.role}
                              onChange={(e) => handleRoleChange(u, e.target.value as TeamRole)}
                            >
                              {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                            </select>
                          </td>
                          <td style={{ padding: "0.65rem 1rem" }}>
                            <span style={{
                              fontSize: "0.68rem",
                              background: u.is_active ? "rgba(16,185,129,0.1)" : "rgba(100,116,139,0.1)",
                              color: u.is_active ? "#10b981" : "#64748b",
                              borderRadius: 4, padding: "2px 7px", fontWeight: 600,
                            }}>
                              {u.is_active ? "Active" : "Inactive"}
                            </span>
                          </td>
                          <td style={{ padding: "0.65rem 1rem", color: "#94a3b8", whiteSpace: "nowrap" }}>
                            {new Date(u.created_at).toLocaleDateString()}
                          </td>
                          <td style={{ padding: "0.65rem 1rem" }}>
                            <button
                              className="btn btn-sm"
                              style={{
                                fontSize: "0.72rem",
                                borderRadius: 6,
                                border: `1px solid ${u.is_active ? "#fca5a5" : "#86efac"}`,
                                color: u.is_active ? "#ef4444" : "#10b981",
                                background: u.is_active ? "rgba(239,68,68,0.05)" : "rgba(16,185,129,0.05)",
                                padding: "2px 10px",
                              }}
                              onClick={() => handleToggleActive(u)}
                            >
                              {u.is_active ? "Deactivate" : "Activate"}
                            </button>
                          </td>
                        </tr>
                      ))}
                      {users.length === 0 && (
                        <tr><td colSpan={6} style={{ padding: "2rem", textAlign: "center", color: "#94a3b8" }}>No users found</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── Audit tab ── */}
        {!loading && tab === "audit" && (
          <div className="content-card">
            <div className="content-card-header">
              <div className="d-flex align-items-center gap-2">
                <i className="bi bi-clock-history" style={{ color: "#6366f1" }} />
                <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Audit Log</span>
              </div>
              <span style={{ fontSize: "0.7rem", background: "#f1f5f9", color: "#64748b", borderRadius: 20, padding: "2px 8px", fontWeight: 600 }}>
                {audit.length} entries
              </span>
            </div>
            <div className="content-card-body" style={{ padding: 0 }}>
              <div className="table-responsive">
                <table className="table table-hover mb-0" style={{ fontSize: "0.82rem" }}>
                  <thead style={{ background: "#f8fafc" }}>
                    <tr>
                      {["Time", "Team", "Tool", "Status", "Latency"].map((h) => (
                        <th key={h} style={{ padding: "0.65rem 1rem", fontWeight: 600, color: "#475569", borderBottom: "1px solid #e2e8f0" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {audit.map((e) => (
                      <tr key={e.id}>
                        <td style={{ padding: "0.65rem 1rem", color: "#94a3b8", whiteSpace: "nowrap" }}>
                          {new Date(e.timestamp).toLocaleTimeString()}
                        </td>
                        <td style={{ padding: "0.65rem 1rem" }}>
                          <span style={{ fontSize: "0.72rem", background: "#f1f5f9", color: "#475569", borderRadius: 4, padding: "2px 7px", fontWeight: 500 }}>
                            {e.team_name}
                          </span>
                        </td>
                        <td style={{ padding: "0.65rem 1rem" }}>
                          <code style={{ fontSize: "0.75rem", color: "#6366f1" }}>{e.tool_name}</code>
                        </td>
                        <td style={{ padding: "0.65rem 1rem" }}>
                          <span
                            style={{
                              fontSize: "0.68rem",
                              background: e.success ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)",
                              color: e.success ? "#10b981" : "#ef4444",
                              borderRadius: 4,
                              padding: "2px 7px",
                              fontWeight: 600,
                            }}
                          >
                            {e.success ? "OK" : "ERR"}
                          </span>
                        </td>
                        <td style={{ padding: "0.65rem 1rem", color: "#64748b" }}>
                          {e.latency_ms.toFixed(0)} ms
                        </td>
                      </tr>
                    ))}
                    {audit.length === 0 && (
                      <tr><td colSpan={5} style={{ padding: "2rem", textAlign: "center", color: "#94a3b8" }}>No audit entries yet</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* ── Metrics tab ── */}
        {!loading && tab === "metrics" && (
          <div className="d-flex flex-column gap-3">
            {/* Stat cards row */}
            <div className="row g-3">
              <div className="col-6 col-lg-3">
                <StatCard label="Uptime" value={metrics.uptime_seconds !== undefined ? formatUptime(metrics.uptime_seconds) : "—"} icon="bi-activity" color="#10b981" />
              </div>
              <div className="col-6 col-lg-3">
                <StatCard label="Total Requests" value={metrics.requests_total ?? 0} icon="bi-arrow-repeat" color="#6366f1" />
              </div>
              <div className="col-6 col-lg-3">
                <StatCard label="Error Rate" value={`${metrics.error_rate_pct ?? 0}%`} icon="bi-exclamation-triangle" color="#ef4444" />
              </div>
              <div className="col-6 col-lg-3">
                <StatCard label="Avg Latency" value={`${metrics.avg_latency_ms ?? 0} ms`} icon="bi-speedometer2" color="#f59e0b" />
              </div>
            </div>

            <div className="row g-3">
              {/* Top tools */}
              <div className="col-md-6">
                <div className="content-card h-100">
                  <div className="content-card-header">
                    <div className="d-flex align-items-center gap-2">
                      <i className="bi bi-bar-chart-fill" style={{ color: "#6366f1" }} />
                      <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Top Tools</span>
                    </div>
                  </div>
                  <div className="content-card-body">
                    {metrics.top_tools && Object.keys(metrics.top_tools).length > 0 ? (
                      Object.entries(metrics.top_tools).map(([tool, count]) => {
                        const max = Math.max(...Object.values(metrics.top_tools as Record<string, number>));
                        const pct = Math.round((count / max) * 100);
                        return (
                          <div key={tool} className="mb-2">
                            <div className="d-flex justify-content-between mb-1">
                              <code style={{ fontSize: "0.75rem", color: "#6366f1" }}>{tool}</code>
                              <span style={{ fontSize: "0.75rem", color: "#64748b" }}>{count}</span>
                            </div>
                            <div style={{ height: 6, background: "#f1f5f9", borderRadius: 3, overflow: "hidden" }}>
                              <div style={{ width: `${pct}%`, height: "100%", background: "#6366f1", borderRadius: 3, transition: "width 0.4s" }} />
                            </div>
                          </div>
                        );
                      })
                    ) : (
                      <p style={{ fontSize: "0.82rem", color: "#94a3b8" }}>No data yet</p>
                    )}
                  </div>
                </div>
              </div>

              {/* Top teams */}
              <div className="col-md-6">
                <div className="content-card h-100">
                  <div className="content-card-header">
                    <div className="d-flex align-items-center gap-2">
                      <i className="bi bi-people-fill" style={{ color: "#10b981" }} />
                      <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Top Teams</span>
                    </div>
                  </div>
                  <div className="content-card-body">
                    {metrics.top_teams && Object.keys(metrics.top_teams).length > 0 ? (
                      Object.entries(metrics.top_teams).map(([team, count]) => {
                        const max = Math.max(...Object.values(metrics.top_teams as Record<string, number>));
                        const pct = Math.round((count / max) * 100);
                        return (
                          <div key={team} className="mb-2">
                            <div className="d-flex justify-content-between mb-1">
                              <span style={{ fontSize: "0.78rem", fontWeight: 500, color: "#1e293b" }}>{team}</span>
                              <span style={{ fontSize: "0.75rem", color: "#64748b" }}>{count} calls</span>
                            </div>
                            <div style={{ height: 6, background: "#f1f5f9", borderRadius: 3, overflow: "hidden" }}>
                              <div style={{ width: `${pct}%`, height: "100%", background: "#10b981", borderRadius: 3, transition: "width 0.4s" }} />
                            </div>
                          </div>
                        );
                      })
                    ) : (
                      <p style={{ fontSize: "0.82rem", color: "#94a3b8" }}>No data yet</p>
                    )}
                  </div>
                </div>
              </div>

              {/* Registry counts */}
              <div className="col-12">
                <div className="content-card">
                  <div className="content-card-header">
                    <div className="d-flex align-items-center gap-2">
                      <i className="bi bi-grid-3x3-gap-fill" style={{ color: "#6366f1" }} />
                      <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Registry</span>
                    </div>
                  </div>
                  <div className="content-card-body">
                    <div className="row g-2">
                      {[
                        { label: "Tools", value: metrics.tools_registered ?? 0, icon: "bi-tools", color: "#6366f1" },
                        { label: "Resources", value: metrics.resources_registered ?? 0, icon: "bi-hdd-stack-fill", color: "#10b981" },
                        { label: "Prompts", value: metrics.prompts_registered ?? 0, icon: "bi-chat-dots-fill", color: "#f59e0b" },
                        { label: "Audit Entries", value: metrics.audit_entries_tracked ?? 0, icon: "bi-clock-history", color: "#8b5cf6" },
                      ].map(({ label, value, icon, color }) => (
                        <div key={label} className="col-6 col-md-3">
                          <div
                            style={{
                              border: "1.5px solid #e2e8f0",
                              borderRadius: 10,
                              padding: "0.75rem",
                              textAlign: "center",
                              background: "#f8fafc",
                            }}
                          >
                            <i className={`bi ${icon}`} style={{ color, fontSize: "1.2rem", display: "block", marginBottom: 4 }} />
                            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "#1e293b" }}>{value}</div>
                            <div style={{ fontSize: "0.72rem", color: "#64748b" }}>{label}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
