"use client";

import { useEffect, useState } from "react";
import { fetchHealth } from "@/lib/api";
import { useTheme } from "@/lib/theme";

interface TopbarProps {
  title: string;
  subtitle?: string;
}

export default function Topbar({ title, subtitle }: TopbarProps) {
  const [backendOk, setBackendOk] = useState<boolean | null>(null);
  const { dark, toggle } = useTheme();

  useEffect(() => {
    fetchHealth()
      .then(() => setBackendOk(true))
      .catch(() => setBackendOk(false));
  }, []);

  return (
    <header className="topbar">
      <div className="flex-grow-1">
        <div className="fw-bold" style={{ lineHeight: 1.2 }}>{title}</div>
        {subtitle && (
          <div className="text-muted" style={{ fontSize: "0.75rem" }}>{subtitle}</div>
        )}
      </div>

      <div className="d-flex align-items-center gap-2 small text-muted">
        {backendOk === null ? (
          <span className="spinner-border spinner-border-sm" style={{ width: 10, height: 10 }} />
        ) : (
          <span className={`status-dot ${backendOk ? "ok" : "error"}`} />
        )}
        <span className="d-none d-sm-inline">
          {backendOk === false ? "Backend offline" : backendOk ? "Online" : "Checking…"}
        </span>
      </div>

      <button
        className="btn btn-sm btn-outline-secondary"
        onClick={toggle}
        title={dark ? "Switch to light mode" : "Switch to dark mode"}
      >
        <i className={`bi ${dark ? "bi-sun" : "bi-moon"}`} />
      </button>
    </header>
  );
}
