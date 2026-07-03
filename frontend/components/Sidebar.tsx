"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearAuth, getAuth, getRoleLabel } from "@/lib/auth";
import { useSidebar } from "@/lib/sidebar";
import type { TeamRole } from "@/types";

function SyndrixLogo() {
  return (
    <svg width="36" height="36" viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="36" height="36" rx="10" fill="#6366f1" />
      <circle cx="18" cy="11.5" r="3" fill="white" />
      <circle cx="11" cy="24" r="2.5" fill="rgba(255,255,255,0.85)" />
      <circle cx="25" cy="24" r="2.5" fill="rgba(255,255,255,0.85)" />
      <line x1="18" y1="11.5" x2="11" y2="24" stroke="rgba(255,255,255,0.5)" strokeWidth="1.5" />
      <line x1="18" y1="11.5" x2="25" y2="24" stroke="rgba(255,255,255,0.5)" strokeWidth="1.5" />
      <line x1="11" y1="24" x2="25" y2="24" stroke="rgba(255,255,255,0.5)" strokeWidth="1.5" />
    </svg>
  );
}

interface SubItem { label: string; href: string; icon: string; roles: TeamRole[] }

const ALL_TEAM_ITEMS: SubItem[] = [
  { label: "CRM Tools",   href: "/dashboard/crm",         icon: "bi-people-fill",    roles: ["bd", "admin"] },
  { label: "Dev Tools",   href: "/dashboard/dev",         icon: "bi-code-slash",     roles: ["dev", "admin"] },
  { label: "Management",  href: "/dashboard/mgmt",        icon: "bi-bar-chart-line", roles: ["mgmt", "admin"] },
];

// Podio Agent is a top-level item directly under the Team Tools section.
const PODIO_AGENT_ROLES: TeamRole[] = ["bd", "admin"];

const ALL_SHARED_ITEMS: SubItem[] = [
  { label: "Knowledge Base", href: "/dashboard/rag",     icon: "bi-journal-bookmark-fill", roles: ["bd", "dev", "mgmt", "admin"] },
  { label: "Prompt Packs",   href: "/dashboard/prompts", icon: "bi-lightning-charge-fill", roles: ["bd", "dev", "mgmt", "admin"] },
  { label: "Admin",          href: "/dashboard/admin",   icon: "bi-shield-lock-fill",      roles: ["admin"] },
];

function AccordionGroup({
  icon, iconBg, label, items, role, pathname, onLinkClick,
}: {
  icon: string; iconBg: string; label: string;
  items: SubItem[]; role: TeamRole; pathname: string; onLinkClick: () => void;
}) {
  const visible = items.filter((i) => i.roles.includes(role));
  if (visible.length === 0) return null;
  const hasActive = visible.some((i) => pathname.startsWith(i.href));
  const [open, setOpen] = useState(hasActive);

  return (
    <div className="sb-section">
      <div className={`sb-section-header${hasActive ? " active" : ""}`} onClick={() => setOpen((o) => !o)}>
        <div className="sb-section-icon" style={{ background: iconBg }}>
          <i className={`bi ${icon}`} style={{ color: "white", fontSize: "0.85rem" }} />
        </div>
        <span className="sb-section-label">{label}</span>
        <i className={`bi bi-chevron-right sb-chevron${open ? " open" : ""}`} />
      </div>
      <div className={`sb-items ${open ? "expanded" : "collapsed"}`}>
        {visible.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`sb-link${pathname.startsWith(item.href) ? " active" : ""}`}
            onClick={onLinkClick}
          >
            <i className={`bi ${item.icon}`} />
            {item.label}
          </Link>
        ))}
      </div>
    </div>
  );
}

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  // getAuth() reads localStorage (empty during SSR). Defer it to after mount so the
  // first client render matches the server HTML and avoids a hydration mismatch.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const auth = mounted ? getAuth() : null;
  const role = (auth?.role ?? "dev") as TeamRole;
  const { open, close } = useSidebar();

  function handleLogout() {
    clearAuth();
    close();
    router.push("/login");
  }

  const displayName = auth?.full_name || auth?.email || auth?.team || "Guest";
  const initials = displayName
    .split(/[\s_\-@]/)
    .slice(0, 2)
    .map((w: string) => w[0]?.toUpperCase() ?? "")
    .join("");

  const isOverview = pathname === "/dashboard";

  return (
    <>
      {open && <div className="sidebar-backdrop" onClick={close} />}

      <aside className={`sidebar${open ? " mobile-open" : ""}`}>
        <Link href="/" className="sidebar-brand" onClick={close}>
          <SyndrixLogo />
          <div>
            <div className="sidebar-brand-name">Syndrix</div>
            <div className="sidebar-brand-tag">AI Hub</div>
          </div>
        </Link>

        <nav className="sidebar-nav">
          {/* ── 1. Overview ── */}
          <Link
            href="/dashboard"
            className={`sb-single${isOverview ? " active" : ""}`}
            onClick={close}
          >
            <div className="sb-section-icon" style={{ background: isOverview ? "rgba(99,102,241,0.25)" : "rgba(255,255,255,0.06)" }}>
              <i className="bi bi-grid-fill" style={{ color: isOverview ? "#a5b4fc" : "#94a3b8", fontSize: "0.85rem" }} />
            </div>
            Overview
          </Link>

          <div className="sb-divider" />

          {/* ── 2. Team Tools ── */}
          <div className="sb-group-label">Team Tools</div>
          {PODIO_AGENT_ROLES.includes(role) && (
            <Link
              href="/dashboard/podio-agent"
              className={`sb-single${pathname.startsWith("/dashboard/podio-agent") ? " active" : ""}`}
              onClick={close}
            >
              <div
                className="sb-section-icon"
                style={{ background: pathname.startsWith("/dashboard/podio-agent") ? "rgba(99,102,241,0.25)" : "rgba(255,255,255,0.06)" }}
              >
                <i
                  className="bi bi-robot"
                  style={{ color: pathname.startsWith("/dashboard/podio-agent") ? "#a5b4fc" : "#94a3b8", fontSize: "0.85rem" }}
                />
              </div>
              Podio Agent
            </Link>
          )}
          <AccordionGroup
            icon="bi-people-fill" iconBg="rgba(16,185,129,0.75)"
            label="CRM & BD"
            items={ALL_TEAM_ITEMS.filter((i) => i.href === "/dashboard/crm")}
            role={role} pathname={pathname} onLinkClick={close}
          />
          <AccordionGroup
            icon="bi-code-slash" iconBg="rgba(99,102,241,0.75)"
            label="Development"
            items={ALL_TEAM_ITEMS.filter((i) => i.href === "/dashboard/dev")}
            role={role} pathname={pathname} onLinkClick={close}
          />
          <AccordionGroup
            icon="bi-bar-chart-fill" iconBg="rgba(245,158,11,0.75)"
            label="Management"
            items={ALL_TEAM_ITEMS.filter((i) => i.href === "/dashboard/mgmt")}
            role={role} pathname={pathname} onLinkClick={close}
          />

          <div className="sb-divider" />

          {/* ── 3. Shared ── */}
          <div className="sb-group-label">Shared</div>
          <AccordionGroup
            icon="bi-journal-bookmark-fill" iconBg="rgba(139,92,246,0.75)"
            label="Knowledge Base"
            items={ALL_SHARED_ITEMS.filter((i) => i.href === "/dashboard/rag")}
            role={role} pathname={pathname} onLinkClick={close}
          />
          <AccordionGroup
            icon="bi-lightning-charge-fill" iconBg="rgba(245,158,11,0.75)"
            label="Prompt Packs"
            items={ALL_SHARED_ITEMS.filter((i) => i.href === "/dashboard/prompts")}
            role={role} pathname={pathname} onLinkClick={close}
          />
          <AccordionGroup
            icon="bi-shield-lock-fill" iconBg="rgba(239,68,68,0.75)"
            label="Admin"
            items={ALL_SHARED_ITEMS.filter((i) => i.href === "/dashboard/admin")}
            role={role} pathname={pathname} onLinkClick={close}
          />

          <div className="sb-divider" />

          {/* ── 4. Settings (all roles) ── */}
          {(() => {
            const isSettings = pathname.startsWith("/dashboard/settings");
            return (
              <Link
                href="/dashboard/settings"
                className={`sb-single${isSettings ? " active" : ""}`}
                onClick={close}
              >
                <div className="sb-section-icon" style={{ background: isSettings ? "rgba(99,102,241,0.25)" : "rgba(255,255,255,0.06)" }}>
                  <i className="bi bi-gear-fill" style={{ color: isSettings ? "#a5b4fc" : "#94a3b8", fontSize: "0.85rem" }} />
                </div>
                Settings
              </Link>
            );
          })()}
        </nav>

        <div className="sidebar-footer">
          <div className="sb-user-row">
            <div className="sb-avatar">{initials}</div>
            <div style={{ overflow: "hidden", flex: 1, minWidth: 0 }}>
              <div className="sb-user-name">{displayName}</div>
              <div className="sb-user-role">{getRoleLabel(role)}</div>
            </div>
          </div>
          <button className="sb-logout" onClick={handleLogout}>
            <i className="bi bi-box-arrow-left" />
            Sign out
          </button>
        </div>
      </aside>
    </>
  );
}
