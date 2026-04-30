"use client";

import AuthGuard from "@/components/AuthGuard";
import Sidebar from "@/components/Sidebar";
import ToastContainer from "@/components/ToastContainer";
import { ToastProvider } from "@/lib/toast";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <ToastProvider>
      <AuthGuard>
        <div style={{ display: "flex" }}>
          <Sidebar />
          <div className="main-content w-100">{children}</div>
        </div>
        <ToastContainer />
      </AuthGuard>
    </ToastProvider>
  );
}
