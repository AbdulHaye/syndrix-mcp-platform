"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { saveAuth } from "@/lib/auth";
import { fetchHealth } from "@/lib/api";

const DEV_PRESETS = [
  {
    label: "BD Team",
    team: "bd_team",
    token: process.env.NEXT_PUBLIC_DEV_TOKEN_BD || "bd-secret-token-123",
    icon: "bi-briefcase",
    color: "success",
  },
  {
    label: "Dev Team",
    team: "dev_team",
    token: process.env.NEXT_PUBLIC_DEV_TOKEN_DEV || "dev-secret-token-456",
    icon: "bi-code-slash",
    color: "primary",
  },
  {
    label: "Management",
    team: "mgmt_team",
    token: process.env.NEXT_PUBLIC_DEV_TOKEN_MGMT || "mgmt-secret-token-789",
    icon: "bi-bar-chart",
    color: "warning",
  },
];

export default function LoginPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [teamName, setTeamName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleLogin(t: string, team: string) {
    setLoading(true);
    setError("");
    try {
      saveAuth(t, team);
      // Verify the token works
      await fetchHealth();
      router.push("/dashboard");
    } catch (err) {
      setError("Could not connect to the platform. Check the token and make sure the backend is running.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        {/* Logo */}
        <div className="d-flex align-items-center justify-content-between mb-4">
          <div className="d-flex align-items-center gap-2">
            <div style={{ width: 40, height: 40, background: "#6366f1", borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <i className="bi bi-cpu text-white fs-5" />
            </div>
            <div>
              <div className="fw-bold" style={{ lineHeight: 1.2, color: "#0f172a" }}>Syndrix</div>
              <div style={{ fontSize: "0.7rem", color: "#475569" }}></div>
            </div>
          </div>
          <a href="/" style={{ fontSize: "0.78rem", color: "#475569", textDecoration: "none", display: "flex", alignItems: "center", gap: "0.3rem" }}>
            <i className="bi bi-arrow-left" />
            Home
          </a>
        </div>

        <h5 className="fw-bold mb-1">Sign in</h5>
        <p className="text-muted small mb-4">Use your team bearer token to access the platform.</p>

        {/* Quick-select presets (dev convenience) */}
        <div className="mb-4">
          <div className="small text-muted mb-2 fw-semibold">Quick select (development)</div>
          <div className="d-flex flex-column gap-2">
            {DEV_PRESETS.map((p) => (
              <button
                key={p.team}
                className={`btn btn-outline-${p.color} btn-sm d-flex align-items-center gap-2`}
                onClick={() => handleLogin(p.token, p.team)}
                disabled={loading}
              >
                <i className={`bi ${p.icon}`} />
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <div className="d-flex align-items-center gap-2 mb-4">
          <hr className="flex-grow-1" />
          <span className="text-muted small">or enter manually</span>
          <hr className="flex-grow-1" />
        </div>

        {/* Manual token entry */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (token && teamName) handleLogin(token, teamName);
          }}
        >
          <div className="mb-3">
            <label className="form-label small fw-semibold">Team Name</label>
            <input
              type="text"
              className="form-control"
              placeholder="e.g. bd_team"
              value={teamName}
              onChange={(e) => setTeamName(e.target.value)}
            />
          </div>
          <div className="mb-3">
            <label className="form-label small fw-semibold">Bearer Token</label>
            <input
              type="password"
              className="form-control"
              placeholder="Paste your token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </div>

          {error && (
            <div className="alert alert-danger py-2 small" role="alert">
              <i className="bi bi-exclamation-triangle me-2" />
              {error}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary w-100"
            disabled={loading || !token || !teamName}
          >
            {loading ? (
              <>
                <span className="spinner-border spinner-border-sm me-2" />
                Connecting…
              </>
            ) : (
              <>
                <i className="bi bi-box-arrow-in-right me-2" />
                Sign In
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
