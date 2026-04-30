"use client";

import type { AuthState, TeamRole } from "@/types";

const STORAGE_KEY = "mcp_auth";
const COOKIE_NAME = "mcp_token";

function inferRole(team: string): TeamRole {
  const t = team.toLowerCase();
  if (t.includes("bd")) return "bd";
  if (t.includes("dev")) return "dev";
  if (t.includes("mgmt") || t.includes("management")) return "mgmt";
  if (t.includes("admin")) return "admin";
  return "dev";
}

export function saveAuth(token: string, team: string): void {
  const role = inferRole(team);
  const state: AuthState = { token, team, role };
  if (typeof window !== "undefined") {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    document.cookie = `${COOKIE_NAME}=${token}; path=/; max-age=86400; SameSite=Strict`;
  }
}

export function getAuth(): AuthState | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthState;
  } catch {
    return null;
  }
}

export function clearAuth(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem(STORAGE_KEY);
    document.cookie = `${COOKIE_NAME}=; path=/; max-age=0`;
  }
}

export function isAuthenticated(): boolean {
  return getAuth() !== null;
}

export function getRoleBadgeColor(role: TeamRole): string {
  const map: Record<TeamRole, string> = {
    bd: "success",
    dev: "primary",
    mgmt: "warning",
    admin: "danger",
  };
  return map[role];
}

export function getRoleLabel(role: TeamRole): string {
  const map: Record<TeamRole, string> = {
    bd: "BD Team",
    dev: "Dev Team",
    mgmt: "Management",
    admin: "Admin",
  };
  return map[role];
}
