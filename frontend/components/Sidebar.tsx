"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearAuth, getAuth, getRoleBadgeColor, getRoleLabel } from "@/lib/auth";
import type { TeamRole } from "@/types";

interface NavItem {
  label: string;
  href: string;
  icon: string;
  roles: TeamRole[];
  section?: string;
}

const NAV_ITEMS: NavItem[] = [
  { label: "Overview", href: "/dashboard", icon: "bi-house", roles: ["bd", "dev", "mgmt", "admin"] },
  { label: "Health", href: "/dashboard/health", icon: "bi-heart-pulse", roles: ["bd", "dev", "mgmt", "admin"] },

  { label: "CRM Tools", href: "/dashboard/crm", icon: "bi-people", roles: ["bd", "admin"], section: "BD Team" },

  { label: "Dev Tools", href: "/dashboard/dev", icon: "bi-code-slash", roles: ["dev", "admin"], section: "Dev Team" },

  { label: "Management", href: "/dashboard/mgmt", icon: "bi-bar-chart-line", roles: ["mgmt", "admin"], section: "Management" },

  { label: "Knowledge Base", href: "/dashboard/rag", icon: "bi-journal-bookmark", roles: ["bd", "dev", "mgmt", "admin"], section: "Shared" },

  { label: "Admin", href: "/dashboard/admin", icon: "bi-shield-lock", roles: ["admin"], section: "Admin" },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const auth = getAuth();
  const role = auth?.role ?? "dev";

  const visibleItems = NAV_ITEMS.filter((item) => item.roles.includes(role));

  function handleLogout() {
    clearAuth();
    router.push("/login");
  }

  // Group by section label
  const sections: { label: string | null; items: NavItem[] }[] = [];
  let currentSection: { label: string | null; items: NavItem[] } | null = null;

  for (const item of visibleItems) {
    const sec = item.section ?? null;
    if (!currentSection || currentSection.label !== sec) {
      currentSection = { label: sec, items: [] };
      sections.push(currentSection);
    }
    currentSection.items.push(item);
  }

  return (
    <aside className="sidebar">
      <Link href="/dashboard" className="sidebar-brand">
        <div className="brand-icon">
          <i className="bi bi-cpu text-white" style={{ fontSize: "1rem" }} />
        </div>
        Syndrix
      </Link>

      <nav className="sidebar-nav">
        {sections.map((section, si) => (
          <div key={si}>
            {section.label && (
              <div className="sidebar-section-label">{section.label}</div>
            )}
            {section.items.map((item) => {
              const isActive =
                item.href === "/dashboard"
                  ? pathname === "/dashboard"
                  : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`sidebar-link${isActive ? " active" : ""}`}
                >
                  <i className={`bi ${item.icon}`} />
                  {item.label}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="sidebar-footer">
        {auth && (
          <div className="mb-2">
            <span className={`badge bg-${getRoleBadgeColor(role)}`}>
              {getRoleLabel(role)}
            </span>
            <div className="mt-1 text-truncate" style={{ maxWidth: 180 }}>
              {auth.team}
            </div>
          </div>
        )}
        <button
          className="btn btn-sm btn-outline-secondary w-100"
          onClick={handleLogout}
        >
          <i className="bi bi-box-arrow-left me-1" />
          Sign out
        </button>
      </div>
    </aside>
  );
}
