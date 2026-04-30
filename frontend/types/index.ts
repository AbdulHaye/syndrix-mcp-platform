export type TeamRole = "bd" | "dev" | "mgmt" | "admin";

export interface AuthState {
  token: string;
  team: string;
  role: TeamRole;
}

export interface HealthStatus {
  status: "ok" | "degraded" | "error";
  env: string;
  version: string;
  services?: Record<string, { status: string; detail?: string }>;
}

export interface ApiResult<T = unknown> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

export interface ToolInvokeRequest {
  tool: string;
  args: Record<string, unknown>;
}

export interface ToolInvokeResult {
  success: boolean;
  tool: string;
  result?: unknown;
  error?: string;
}

export interface AuditEntry {
  id: string;
  timestamp: string;
  team_name: string;
  tool_name: string;
  input_summary: string;
  success: boolean;
  latency_ms: number;
  error_msg?: string;
}

export interface IngestRequest {
  title: string;
  content: string;
  source?: string;
  chunk_size?: number;
}

export interface IngestResult {
  success: boolean;
  title: string;
  source: string;
  chunks_total: number;
  chunks_stored: number;
  chunks_failed: number;
  document_ids: string[];
  error?: string;
}

export interface NavItem {
  label: string;
  href: string;
  icon: string;
  roles: TeamRole[];
}
