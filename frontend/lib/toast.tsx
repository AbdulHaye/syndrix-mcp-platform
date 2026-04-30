"use client";

import { createContext, useContext, useCallback, useReducer } from "react";

export type ToastType = "success" | "error" | "info" | "warning";

export interface ToastItem {
  id: string;
  type: ToastType;
  message: string;
}

interface ToastCtx {
  toasts: ToastItem[];
  toast: (type: ToastType, message: string) => void;
  dismiss: (id: string) => void;
}

const Ctx = createContext<ToastCtx | null>(null);

type Action = { type: "ADD"; item: ToastItem } | { type: "REMOVE"; id: string };

function reducer(state: ToastItem[], action: Action): ToastItem[] {
  if (action.type === "ADD") return [...state.slice(-4), action.item];
  return state.filter((t) => t.id !== action.id);
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, dispatch] = useReducer(reducer, []);

  const toast = useCallback((type: ToastType, message: string) => {
    const id = `${Date.now()}-${Math.random()}`;
    dispatch({ type: "ADD", item: { id, type, message } });
    setTimeout(() => dispatch({ type: "REMOVE", id }), 4500);
  }, []);

  const dismiss = useCallback(
    (id: string) => dispatch({ type: "REMOVE", id }),
    []
  );

  return (
    <Ctx.Provider value={{ toasts, toast, dismiss }}>{children}</Ctx.Provider>
  );
}

export function useToast() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast must be inside ToastProvider");
  return ctx;
}
