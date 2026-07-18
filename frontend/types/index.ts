export type TeamRole = "bd" | "dev" | "mgmt" | "admin";

export interface AuthState {
  token: string;
  team: string;
  role: TeamRole;
  email?: string;
  full_name?: string;
  user_id?: string;
}

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  team_name: string;
  role: TeamRole;
  is_active: boolean;
  created_at: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: {
    id: string;
    email: string;
    full_name: string | null;
    team_name: string;
    role: TeamRole;
    is_active: boolean;
  };
}

export interface CreateUserRequest {
  email: string;
  password: string;
  full_name?: string;
  team_name: string;
  role: TeamRole;
}

export interface UpdateUserRequest {
  full_name?: string;
  team_name?: string;
  role?: TeamRole;
  is_active?: boolean;
  password?: string;
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

export interface IntegrationSetting {
  key: string;
  value: string;
  is_secret: boolean;
  is_set: boolean;
}

export interface NavItem {
  label: string;
  href: string;
  icon: string;
  roles: TeamRole[];
}

export interface PromptTemplate {
  key: string;
  title: string;
  description: string;
  category: "bd" | "dev" | "shared";
  variables: string[];
}

export interface PromptRunResult {
  success: boolean;
  key: string;
  title?: string;
  output?: string;
  model?: string;
  error?: string;
  required?: string[];
}

// ── Podio Agent ───────────────────────────────────────────────────────────────

export interface PodioAgentStep {
  tool: string;
  args: Record<string, unknown>;
  result: unknown;
}

export interface PodioAgentResponse {
  success: boolean;
  reply: string;
  steps: PodioAgentStep[];
  model?: string;
  error?: string;
}

export interface PodioChatMessage {
  role: "user" | "assistant";
  content: string;
  steps?: PodioAgentStep[];
  error?: boolean;
}

export interface PodioChatSessionSummary {
  id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface PodioChatSessionDetail extends PodioChatSessionSummary {
  messages: PodioChatMessage[];
}

// ── MyCase Agent ──────────────────────────────────────────────────────────────
// Structurally identical to the Podio Agent's shapes (same step-card / chat-session
// UI is reused) — aliased rather than duplicated so the two stay in sync by
// construction.

export type MyCaseAgentStep = PodioAgentStep;

export interface MyCaseAgentResponse {
  success: boolean;
  reply: string;
  steps: MyCaseAgentStep[];
  model?: string;
  error?: string;
}

export type MyCaseChatMessage = PodioChatMessage;
export type MyCaseChatSessionSummary = PodioChatSessionSummary;
export type MyCaseChatSessionDetail = PodioChatSessionDetail;

export interface PodioWorkspace {
  space_id: number;
  name: string | null;
  org_id?: number | null;
  org_name?: string | null;
}

export interface PodioWorkspacesResponse {
  success: boolean;
  workspaces: PodioWorkspace[];
  current_space_id: number | null;
  error?: string;
}
