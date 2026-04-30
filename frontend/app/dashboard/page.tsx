"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Topbar from "@/components/Topbar";
import ToolCard from "@/components/ToolCard";
import { fetchHealth } from "@/lib/api";
import { getAuth, getRoleLabel } from "@/lib/auth";
import type { HealthStatus } from "@/types";

const TOOL_SECTIONS = [
  {
    label: "CRM & BD",
    roles: ["bd", "admin"],
    color: "#198754",
    items: [
      { icon: "bi-person-lines-fill", bg: "#198754", title: "Contact Lookup", desc: "Find and view CRM contacts from Podio.", href: "/dashboard/crm" },
      { icon: "bi-search", bg: "#20c997", title: "Lead Search", desc: "Search leads by keyword or filter.", href: "/dashboard/crm" },
      { icon: "bi-chat-left-text", bg: "#0dcaf0", title: "Send Message", desc: "SMS or email a GHL contact.", href: "/dashboard/crm" },
    ],
  },
  {
    label: "Development",
    roles: ["dev", "admin"],
    color: "#0d6efd",
    items: [
      { icon: "bi-github", bg: "#0d6efd", title: "Repo Search", desc: "Search GitHub repositories.", href: "/dashboard/dev" },
      { icon: "bi-ticket", bg: "#6f42c1", title: "Create Ticket", desc: "Open a GitHub issue.", href: "/dashboard/dev" },
      { icon: "bi-file-earmark-code", bg: "#fd7e14", title: "Spec Generator", desc: "Generate a tech spec using the local LLM.", href: "/dashboard/dev" },
    ],
  },
  {
    label: "Management",
    roles: ["mgmt", "admin"],
    color: "#ffc107",
    items: [
      { icon: "bi-clipboard-data", bg: "#ffc107", title: "Daily Report", desc: "Team activity summary for today.", href: "/dashboard/mgmt" },
      { icon: "bi-heart-pulse", bg: "#dc3545", title: "Client Health", desc: "Health score for a client account.", href: "/dashboard/mgmt" },
    ],
  },
  {
    label: "Knowledge Base",
    roles: ["bd", "dev", "mgmt", "admin"],
    color: "#6f42c1",
    items: [
      { icon: "bi-search", bg: "#6f42c1", title: "Semantic Search", desc: "Search the internal knowledge base.", href: "/dashboard/rag" },
      { icon: "bi-cloud-upload", bg: "#0dcaf0", title: "Ingest Document", desc: "Add a document to the knowledge base.", href: "/dashboard/rag" },
    ],
  },
];

export default function DashboardPage() {
  const router = useRouter();
  const auth = getAuth();
  const role = auth?.role ?? "dev";
  const [health, setHealth] = useState<HealthStatus | null>(null);

  useEffect(() => {
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth({ status: "error", env: "unknown", version: "?" }));
  }, []);

  const visibleSections = TOOL_SECTIONS.filter((s) =>
    s.roles.includes(role)
  );

  return (
    <>
      <Topbar
        title="Overview"
        subtitle={`Welcome back — ${getRoleLabel(role)}`}
      />
      <div className="page-body">
        {/* Stats row */}
        <div className="row g-3 mb-4">
          <div className="col-sm-6 col-lg-3">
            <div className="stat-card">
              <div className="d-flex align-items-center gap-2 mb-1">
                <i className="bi bi-activity text-primary" />
                <span className="small text-muted">Backend</span>
              </div>
              <div className="stat-value">
                {health === null ? (
                  <span className="spinner-border spinner-border-sm" />
                ) : (
                  <span
                    className={`badge fs-6 bg-${health.status === "ok" ? "success" : health.status === "degraded" ? "warning" : "danger"}`}
                  >
                    {health.status.toUpperCase()}
                  </span>
                )}
              </div>
              <div className="stat-label">Platform status</div>
            </div>
          </div>
          <div className="col-sm-6 col-lg-3">
            <div className="stat-card">
              <div className="d-flex align-items-center gap-2 mb-1">
                <i className="bi bi-person-badge text-success" />
                <span className="small text-muted">Logged in as</span>
              </div>
              <div className="stat-value" style={{ fontSize: "1.2rem" }}>
                {auth?.team ?? "—"}
              </div>
              <div className="stat-label">{getRoleLabel(role)}</div>
            </div>
          </div>
          <div className="col-sm-6 col-lg-3">
            <div className="stat-card">
              <div className="d-flex align-items-center gap-2 mb-1">
                <i className="bi bi-tools text-warning" />
                <span className="small text-muted">Available tools</span>
              </div>
              <div className="stat-value">
                {visibleSections.reduce((a, s) => a + s.items.length, 0)}
              </div>
              <div className="stat-label">for your role</div>
            </div>
          </div>
          <div className="col-sm-6 col-lg-3">
            <div className="stat-card">
              <div className="d-flex align-items-center gap-2 mb-1">
                <i className="bi bi-tag text-info" />
                <span className="small text-muted">Version</span>
              </div>
              <div className="stat-value" style={{ fontSize: "1.4rem" }}>
                {health?.version ?? "—"}
              </div>
              <div className="stat-label">{health?.env ?? "…"}</div>
            </div>
          </div>
        </div>

        {/* Tool sections */}
        {visibleSections.map((section) => (
          <div key={section.label} className="mb-4">
            <h6 className="fw-bold mb-3 d-flex align-items-center gap-2">
              <span
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: "50%",
                  background: section.color,
                  display: "inline-block",
                }}
              />
              {section.label}
            </h6>
            <div className="row g-3">
              {section.items.map((item) => (
                <div key={item.title} className="col-sm-6 col-lg-4">
                  <ToolCard
                    icon={item.icon}
                    iconBg={item.bg}
                    title={item.title}
                    description={item.desc}
                    onClick={() => router.push(item.href)}
                  />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
