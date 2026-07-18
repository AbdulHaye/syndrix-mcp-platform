"use client";

import { getAuth } from "@/lib/auth";
import type {
  HealthStatus,
  ToolInvokeRequest,
  ToolInvokeResult,
  IngestRequest,
  IngestResult,
  AuditEntry,
  LoginRequest,
  LoginResponse,
  User,
  CreateUserRequest,
  UpdateUserRequest,
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
    let detail = "";
    try {
      detail = JSON.parse(text)?.detail ?? "";
    } catch {}
    // Always prefix with status so callers can branch on 401/403 reliably
    throw new Error(`${res.status}: ${detail || res.statusText}`);
  }

  return res.json() as Promise<T>;
}

// ── Auth ────────────────────────────────────────────────────────────────────

export async function loginUser(body: LoginRequest): Promise<LoginResponse> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    let msg = `HTTP ${res.status}`;
    try { msg = JSON.parse(text)?.detail ?? msg; } catch {}
    throw new Error(msg);
  }
  return res.json() as Promise<LoginResponse>;
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

// ── Prompts ───────────────────────────────────────────────────────────────────

export async function fetchPromptList(role?: string): Promise<{
  count: number;
  templates: import("@/types").PromptTemplate[];
}> {
  const args = role ? { role } : {};
  const envelope = await request<{ success: boolean; tool: string; result: { count: number; templates: import("@/types").PromptTemplate[] } }>(
    "/tools/invoke",
    {
      method: "POST",
      body: JSON.stringify({ tool: "prompt.list", args }),
    }
  );
  const data = envelope.result ?? { count: 0, templates: [] };
  return { count: data.count ?? 0, templates: data.templates ?? [] };
}

export async function runPrompt(
  key: string,
  variables: Record<string, string>,
  role?: string
): Promise<import("@/types").PromptRunResult> {
  const args: Record<string, unknown> = { key, variables };
  if (role) args.role = role;
  const envelope = await request<{ success: boolean; tool: string; result: import("@/types").PromptRunResult }>(
    "/tools/invoke",
    {
      method: "POST",
      body: JSON.stringify({ tool: "prompt.run", args }),
    }
  );
  return envelope.result ?? { success: false, key, error: "No result returned" };
}

// ── Podio Agent ───────────────────────────────────────────────────────────────

export async function runPodioAgent(
  message: string,
  history: { role: string; content: string }[] = []
): Promise<import("@/types").PodioAgentResponse> {
  return request<import("@/types").PodioAgentResponse>("/agent/podio", {
    method: "POST",
    body: JSON.stringify({ message, history }),
  });
}

// ── Podio Agent chat session persistence (DB-backed history) ────────────────

export async function listPodioChatSessions(): Promise<
  import("@/types").PodioChatSessionSummary[]
> {
  return request("/agent/podio/sessions");
}

export async function getPodioChatSession(
  id: string
): Promise<import("@/types").PodioChatSessionDetail> {
  return request(`/agent/podio/sessions/${id}`);
}

export async function savePodioChatSession(
  id: string,
  title: string,
  messages: import("@/types").PodioChatMessage[]
): Promise<import("@/types").PodioChatSessionSummary> {
  return request(`/agent/podio/sessions/${id}`, {
    method: "PUT",
    body: JSON.stringify({ title, messages }),
  });
}

export async function deletePodioChatSession(id: string): Promise<{ success: boolean }> {
  return request(`/agent/podio/sessions/${id}`, { method: "DELETE" });
}

// ── MyCase Agent ──────────────────────────────────────────────────────────────

export async function getMyCaseStatus(): Promise<{ connected: boolean }> {
  return request("/agent/mycase/status");
}

export async function startMyCaseConnect(): Promise<{ success: boolean; authorize_url?: string; error?: string }> {
  return request("/integrations/mycase/connect");
}

export async function disconnectMyCase(): Promise<{ success: boolean }> {
  return request("/integrations/mycase/disconnect", { method: "POST" });
}

export async function runMyCaseAgent(
  message: string,
  history: { role: string; content: string }[] = [],
  signal?: AbortSignal
): Promise<import("@/types").MyCaseAgentResponse> {
  return request<import("@/types").MyCaseAgentResponse>("/agent/mycase", {
    method: "POST",
    body: JSON.stringify({ message, history }),
    signal,
  });
}

export async function listMyCaseChatSessions(): Promise<
  import("@/types").MyCaseChatSessionSummary[]
> {
  return request("/agent/mycase/sessions");
}

export async function getMyCaseChatSession(
  id: string
): Promise<import("@/types").MyCaseChatSessionDetail> {
  return request(`/agent/mycase/sessions/${id}`);
}

export async function saveMyCaseChatSession(
  id: string,
  title: string,
  messages: import("@/types").MyCaseChatMessage[]
): Promise<import("@/types").MyCaseChatSessionSummary> {
  return request(`/agent/mycase/sessions/${id}`, {
    method: "PUT",
    body: JSON.stringify({ title, messages }),
  });
}

export async function deleteMyCaseChatSession(id: string): Promise<{ success: boolean }> {
  return request(`/agent/mycase/sessions/${id}`, { method: "DELETE" });
}

// ── Podio MCP connection (OAuth) ──────────────────────────────────────────────

export interface PodioSelectedWorkspace {
  space_id: number | string;
  name: string | null;
  org_name: string | null;
}

export async function getPodioStatus(): Promise<{ connected: boolean; workspace: PodioSelectedWorkspace | null }> {
  return request("/integrations/podio/status");
}

export async function listPodioOrganizations(): Promise<{
  success: boolean;
  organizations: { org_id: number; name: string; spaces_count?: number }[];
  error?: string;
}> {
  return request("/integrations/podio/organizations");
}

export async function listPodioSpaces(orgId: number): Promise<{
  success: boolean;
  spaces: { space_id: number; name: string; org_id?: number }[];
  error?: string;
}> {
  return request(`/integrations/podio/spaces?org_id=${orgId}`);
}

export async function setPodioWorkspace(
  spaceId: number,
  name?: string | null,
  orgName?: string | null
): Promise<{ success: boolean; space_id: number; name: string | null }> {
  return request("/integrations/podio/workspace", {
    method: "POST",
    body: JSON.stringify({ space_id: spaceId, name: name ?? null, org_name: orgName ?? null }),
  });
}

export async function startPodioConnect(): Promise<{ success: boolean; authorize_url?: string; error?: string }> {
  return request("/integrations/podio/connect");
}

export async function getPodioTools(): Promise<{ success: boolean; count?: number; tools?: string[]; error?: string }> {
  return request("/integrations/podio/tools");
}

export async function disconnectPodio(): Promise<{ success: boolean }> {
  return request("/integrations/podio/disconnect", { method: "POST" });
}

// ── Podio Files (custom MCP server: REST upload + attach) ─────────────────────

export async function getPodioFilesStatus(): Promise<{ connected: boolean }> {
  return request("/integrations/podio-files/status");
}

export async function startPodioFilesConnect(): Promise<{ success: boolean; authorize_url?: string; error?: string }> {
  return request("/integrations/podio-files/connect");
}

export async function disconnectPodioFiles(): Promise<{ success: boolean }> {
  return request("/integrations/podio-files/disconnect", { method: "POST" });
}

export async function downloadPodioFile(fileId: number, filename?: string): Promise<void> {
  const auth = getAuth();
  const res = await fetch(`${BASE}/integrations/podio-files/download/${fileId}`, {
    headers: auth?.token ? { Authorization: `Bearer ${auth.token}` } : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    let msg = `HTTP ${res.status}`;
    try { msg = JSON.parse(text)?.detail ?? msg; } catch {}
    throw new Error(msg);
  }
  const disposition = res.headers.get("Content-Disposition") || "";
  const nameMatch = disposition.match(/filename="([^"]+)"/);
  const name = filename || nameMatch?.[1] || `podio_file_${fileId}`;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export async function downloadPodioExport(appId: number, filename?: string): Promise<void> {
  const auth = getAuth();
  const res = await fetch(`${BASE}/integrations/podio-files/export/${appId}`, {
    headers: auth?.token ? { Authorization: `Bearer ${auth.token}` } : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    let msg = `HTTP ${res.status}`;
    try { msg = JSON.parse(text)?.detail ?? msg; } catch {}
    throw new Error(msg);
  }
  const disposition = res.headers.get("Content-Disposition") || "";
  const nameMatch = disposition.match(/filename="([^"]+)"/);
  const name = filename || nameMatch?.[1] || `podio_export_${appId}.xlsx`;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export async function uploadPodioFile(
  file: File,
  itemId?: number,
): Promise<{ success: boolean; file_id?: number; filename?: string; attached_to_item?: number; error?: string }> {
  const auth = getAuth();
  const form = new FormData();
  form.append("file", file);
  if (itemId != null) form.append("item_id", String(itemId));
  const res = await fetch(`${BASE}/integrations/podio-files/upload`, {
    method: "POST",
    headers: auth?.token ? { Authorization: `Bearer ${auth.token}` } : undefined,
    body: form,
  });
  if (!res.ok) {
    const text = await res.text();
    let msg = `HTTP ${res.status}`;
    try { msg = JSON.parse(text)?.detail ?? msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

// ── LLM model selection ───────────────────────────────────────────────────────

export async function listLlmModels(agent: "podio" | "mycase" = "podio"): Promise<{
  ollama: string[];
  google: string[];
  groq: string[];
  mistral: string[];
  openai: string[];
  anthropic: string[];
  zai: string[];
  selected: string;
}> {
  return request(`/llm/models?agent=${agent}`);
}

export async function setLlmModel(
  model: string,
  agent: "podio" | "mycase" = "podio"
): Promise<{ success: boolean; model: string; agent: string }> {
  return request("/llm/model", { method: "POST", body: JSON.stringify({ model, agent }) });
}

// ── User Management ──────────────────────────────────────────────────────────

export async function listUsers(): Promise<{ count: number; users: User[] }> {
  return request("/admin/users");
}

export async function createUser(
  body: CreateUserRequest
): Promise<{ success: boolean; user: User }> {
  return request("/admin/users", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updateUser(
  id: string,
  body: UpdateUserRequest
): Promise<{ success: boolean; user_id: string }> {
  return request(`/admin/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function deactivateUser(
  id: string
): Promise<{ success: boolean; user_id: string }> {
  return request(`/admin/users/${id}`, { method: "DELETE" });
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
