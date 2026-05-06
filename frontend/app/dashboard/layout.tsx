"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import AuthGuard from "@/components/AuthGuard";
import Sidebar from "@/components/Sidebar";
import ToastContainer from "@/components/ToastContainer";
import { ToastProvider } from "@/lib/toast";
import { SidebarProvider } from "@/lib/sidebar";

function MobileNav() {
  const pathname = usePathname();
  const tabs = [
    { label: "Overview", href: "/dashboard",    icon: "bi-grid-fill" },
    { label: "Tools",    href: "/dashboard/dev", icon: "bi-tools" },
    { label: "Shared",   href: "/dashboard/rag", icon: "bi-journal-bookmark-fill" },
  ];
  return (
    <nav className="mobile-nav">
      <div className="mobile-nav-items">
        {tabs.map((tab) => {
          const active = tab.href === "/dashboard" ? pathname === "/dashboard" : pathname.startsWith(tab.href);
          return (
            <Link key={tab.href} href={tab.href} className={`mobile-nav-item${active ? " active" : ""}`}>
              <i className={`bi ${tab.icon}`} />
              {tab.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <SidebarProvider>
      <ToastProvider>
        <AuthGuard>
          <div style={{ display: "flex" }}>
            <Sidebar />
            <div className="main-content w-100">{children}</div>
          </div>
          <MobileNav />
          <ToastContainer />
        </AuthGuard>
      </ToastProvider>
    </SidebarProvider>
  );
}
