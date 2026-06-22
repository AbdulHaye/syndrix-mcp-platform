"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { saveAuthFromLogin } from "@/lib/auth";
import { loginUser } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const response = await loginUser({ email, password });
      saveAuthFromLogin(response);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
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
              <div style={{ fontSize: "0.7rem", color: "#475569" }}>AI Hub</div>
            </div>
          </div>
          <a href="/" style={{ fontSize: "0.78rem", color: "#475569", textDecoration: "none", display: "flex", alignItems: "center", gap: "0.3rem" }}>
            <i className="bi bi-arrow-left" />
            Home
          </a>
        </div>

        <h5 className="fw-bold mb-1">Sign in</h5>
        <p className="text-muted small mb-4">Enter your credentials to access the platform.</p>

        <form onSubmit={handleLogin}>
          <div className="mb-3">
            <label className="form-label small fw-semibold">Email</label>
            <input
              type="email"
              className="form-control"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              required
            />
          </div>
          <div className="mb-4">
            <label className="form-label small fw-semibold">Password</label>
            <div className="input-group">
              <input
                type={showPassword ? "text" : "password"}
                className="form-control"
                placeholder="Enter your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
              <button
                type="button"
                className="input-group-text"
                style={{ cursor: "pointer", background: "white", border: "1px solid #dee2e6", borderLeft: "none" }}
                onClick={() => setShowPassword((v) => !v)}
                tabIndex={-1}
              >
                <i className={`bi ${showPassword ? "bi-eye-slash" : "bi-eye"}`} style={{ color: "#64748b" }} />
              </button>
            </div>
          </div>

          {error && (
            <div className="alert alert-danger py-2 small mb-3" role="alert">
              <i className="bi bi-exclamation-triangle me-2" />
              {error}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary w-100"
            disabled={loading || !email || !password}
          >
            {loading ? (
              <>
                <span className="spinner-border spinner-border-sm me-2" />
                Signing in…
              </>
            ) : (
              <>
                <i className="bi bi-box-arrow-in-right me-2" />
                Sign In
              </>
            )}
          </button>
        </form>

        <p className="text-muted text-center mt-3" style={{ fontSize: "0.75rem" }}>
          Contact your admin to get access.
        </p>
      </div>
    </div>
  );
}
