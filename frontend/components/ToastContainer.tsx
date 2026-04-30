"use client";

import { useToast } from "@/lib/toast";
import type { ToastItem, ToastType } from "@/lib/toast";

const CONFIG: Record<ToastType, { icon: string; bg: string }> = {
  success: { icon: "bi-check-circle-fill", bg: "text-bg-success" },
  error:   { icon: "bi-x-circle-fill",     bg: "text-bg-danger"  },
  warning: { icon: "bi-exclamation-triangle-fill", bg: "text-bg-warning" },
  info:    { icon: "bi-info-circle-fill",   bg: "text-bg-primary" },
};

function Toast({ item }: { item: ToastItem }) {
  const { dismiss } = useToast();
  const { icon, bg } = CONFIG[item.type];
  return (
    <div
      className={`toast show align-items-center border-0 ${bg}`}
      role="alert"
      style={{ minWidth: 300 }}
    >
      <div className="d-flex">
        <div className="toast-body d-flex align-items-center gap-2">
          <i className={`bi ${icon}`} />
          {item.message}
        </div>
        <button
          type="button"
          className="btn-close btn-close-white me-2 m-auto"
          onClick={() => dismiss(item.id)}
        />
      </div>
    </div>
  );
}

export default function ToastContainer() {
  const { toasts } = useToast();
  if (!toasts.length) return null;
  return (
    <div
      className="toast-container position-fixed top-0 end-0 p-3"
      style={{ zIndex: 9999 }}
    >
      {toasts.map((t) => (
        <Toast key={t.id} item={t} />
      ))}
    </div>
  );
}
