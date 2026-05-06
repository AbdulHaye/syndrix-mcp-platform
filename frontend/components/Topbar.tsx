"use client";

import { useEffect, useState } from "react";
import { fetchHealth } from "@/lib/api";
import { useSidebar } from "@/lib/sidebar";

interface TopbarProps {
  title: string;
  subtitle?: string;
}

export default function Topbar({ title, subtitle }: TopbarProps) {
  const [backendOk, setBackendOk] = useState<boolean | null>(null);
  const { toggle: toggleSidebar } = useSidebar();

  useEffect(() => {
    fetchHealth()
      .then(() => setBackendOk(true))
      .catch(() => setBackendOk(false));
  }, []);

  return (
    <header className="topbar">
      <button className="topbar-menu-btn" onClick={toggleSidebar} aria-label="Toggle menu">
        <i className="bi bi-list" />
      </button>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="topbar-title">{title}</div>
        {subtitle && <div className="topbar-subtitle">{subtitle}</div>}
      </div>

      <div className="d-flex align-items-center gap-2">
        <div className="topbar-status">
          {backendOk === null ? (
            <span className="spinner-border spinner-border-sm" style={{ width: 8, height: 8, borderWidth: "1.5px" }} />
          ) : (
            <span className={`status-dot ${backendOk ? "ok" : "error"}`} />
          )}
          <span className="status-label d-none d-sm-inline">
            {backendOk === null ? "Checking…" : backendOk ? "Online" : "Offline"}
          </span>
        </div>
      </div>
    </header>
  );
}
