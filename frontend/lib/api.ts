"use client";

import { getAuth } from "@/lib/auth";
import type {
  HealthStatus,
  ToolInvokeRequest,
  ToolInvokeResult,
  IngestRequest,
  IngestResult,
  AuditEntry,
} from "@/types";

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const auth = getAuth();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (auth?.token) {
    headers["Authorization"] = `Bearer ${auth.token}`;
  }

  const res = await fetch(`${BASE}${path}`, { ...options, headers });

  if (!res.ok) {
    const text = await res.text();
    let msg = `HTTP ${res.status}`;
    try {
      msg = JSON.parse(text)?.detail ?? msg;
    } catch {}
    throw new Error(msg);
  }

  return res.json() as Promise<T>;
}

// ── Health ──────────────────────────────────────────────────────────────────

export async function fetchHealth(): Promise<HealthStatus> {
  return request<HealthStatus>("/health");
}

export async function fetchHealthDetailed(): Promise<HealthStatus> {
  return request<HealthStatus>("/health/detailed");
}

// ── Tool invocation (direct REST wrapper) ───────────────────────────────────

export async function invokeTool(
  req: ToolInvokeRequest
): Promise<ToolInvokeResult> {
  return request<ToolInvokeResult>("/tools/invoke", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

// ── RAG ──────────────────────────────────────────────────────────────────────

export async function ingestDocument(body: IngestRequest): Promise<IngestResult> {
  return request<IngestResult>("/ingest", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ── Admin ────────────────────────────────────────────────────────────────────

export async function fetchAdminTools(): Promise<{
  count: number;
  tools: { name: string; description: string }[];
}> {
  return request("/admin/tools");
}

export async function fetchAuditLog(
  limit = 50
): Promise<{ count: number; entries: AuditEntry[] }> {
  return request(`/admin/audit?limit=${limit}`);
}

export async function fetchMetrics(): Promise<Record<string, unknown>> {
  return request("/admin/metrics");
}

// ── Settings ─────────────────────────────────────────────────────────────────

export async function fetchSettings(): Promise<{
  settings: import("@/types").IntegrationSetting[];
}> {
  const token = getAuth()?.token ?? "";
  const { deriveKey, decryptWithKey, encryptWithKey } = await import("@/lib/crypto");
  const key = await deriveKey(token);

  // Response is a single encrypted envelope: { data: "<ciphertext>" }
  const envelope = await request<{ data: string }>("/settings");
  const json = JSON.parse(await decryptWithKey(envelope.data, key));
  return json as { settings: import("@/types").IntegrationSetting[] };
}

export async function saveSettings(
  settings: Record<string, string | null>
): Promise<{ success: boolean; saved: string[] }> {
  const token = getAuth()?.token ?? "";
  const { deriveKey, encryptWithKey, decryptWithKey } = await import("@/lib/crypto");
  const key = await deriveKey(token);

  // Encrypt the entire payload as one blob
  const ciphertext = await encryptWithKey(JSON.stringify({ settings }), key);
  const envelope = await request<{ data: string }>("/settings", {
    method: "PUT",
    body: JSON.stringify({ data: ciphertext }),
  });

  // Response is also encrypted
  const json = JSON.parse(await decryptWithKey(envelope.data, key));
  return json as { success: boolean; saved: string[] };
}
