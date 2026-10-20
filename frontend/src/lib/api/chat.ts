/** Chat session options, model/provider catalog and pending confirmations. */

import { apiFetch } from "@/lib/api";

// ── Chat ──

export interface ChatAgentOption {
  key: string;
  label: string;
  /** True for sentinels that open a model list ("openrouter:", "custom:")
   *  rather than naming a startable model. Sent explicitly because the key's
   *  shape doesn't tell you — "ollama:" also ends in a colon but IS startable. */
  picker?: boolean;
  /** Whether this machine can actually run it: no Ollama server and no
   *  installed CLI bridge are both false. Absent when the backend could not
   *  probe, which reads as "offer it" — never as "unavailable". */
  ready?: boolean;
  /** The sentence behind `ready`, naming the fix ("not installed — npm
   *  install -g @github/copilot"). Shown on the row that cannot be picked. */
  detail?: string;
}

/** A saved OpenAI-compatible endpoint (Venice AI, Together, local vLLM, ...). */
export interface CustomProvider {
  name: string;
  base_url: string;
  has_key: boolean;
}

export interface ChatOptionsResponse {
  agents: ChatAgentOption[];
  custom_providers: CustomProvider[];
  default_agent: string;
}

export interface OpenRouterModelOption {
  slug: string;
  name: string;
  context_length: number;
  prompt_price: number;
  completion_price: number;
}

/** A domain Agent a session can be bound to. Every Agent is chattable. */
export interface AgentBindingOption {
  slug: string;
  name: string;
  description: string;
  when_to_consult: string;
  /** The model it answers on unless the user overrides one — its own default. */
  agent_key: string;
}

export interface SessionOptionsResponse extends ChatOptionsResponse {
  servers: string[];
  agent_bindings: AgentBindingOption[];
}

/** Mirrors condor/runtime/models.py SessionInfo. */
export interface SessionInfo {
  key: string;
  agent_key: string;
  user_id: number | null;
  /** Originating frontend: "tg" | "web" | "mcp". */
  surface: string;
  slot: string;
  server_name: string | null;
  /** The bound Agent pinned this server; the chat cannot change it. */
  server_pinned: boolean;
  is_busy: boolean;
  alive: boolean;
  created_at: string;
  last_prompt_at: string | null;
  /** Bound domain Agent, or "" for the plain assistant. */
  agent_slug: string;
  /** Display name of whoever is answering. */
  label: string;
  /** Durable conversation behind this session. Outlives it. */
  conversation_id: string;
}

export interface SwitchSessionRequest {
  agent_slug?: string;
  agent_key?: string;
  /** Move the conversation to this server. Also a respawn — the server is
   *  baked into the MCP subprocess at spawn. */
  server_name?: string;
  /** Short recap carried into the new session — the subprocess is replaced. */
  handoff?: string;
}

/** Agent key for a model served by a saved custom endpoint. Mirrors
 *  build_custom_agent_key() in condor/preferences.py. */
export function customAgentKey(provider: string, model: string): string {
  return `custom@${provider}:${model}`;
}

/** Inverse of customAgentKey; returns null for non-custom keys. */
export function parseCustomAgentKey(
  agentKey: string,
): { provider: string; model: string } | null {
  const colon = agentKey.indexOf(":");
  if (colon < 0) return null;
  const prefix = agentKey.slice(0, colon);
  if (!prefix.startsWith("custom@")) return null;
  return {
    provider: prefix.slice("custom@".length),
    model: agentKey.slice(colon + 1),
  };
}

// ── API functions ──

/** One approval still waiting on this user, as `GET /api/v1/confirmations` lists it. */
export interface PendingConfirmation {
  id: string;
  /** The conversation that asked, so the prompt renders where the click belongs. */
  slot_id: string;
  summary: string;
  origin: string;
  /** What the call leaves behind (a controller overwrite's impact), or "". */
  detail?: string;
  expires_at: number;
  /** Seconds until the runtime denies it, measured on the server's clock. */
  expires_in?: number;
  /** The bare tool name the gate judged. */
  tool?: string;
  /** Its arguments, or null when they could not be read. */
  input?: Record<string, unknown> | null;
}

export const chatApi = {
  // ── Chat ──

  getOpenRouterModels: () =>
    apiFetch<{ models: OpenRouterModelOption[] }>(
      "/api/v1/chat/openrouter/models",
    ),

  // ── Sessions (runtime API) ──
  //
  // The same sessions the Telegram bot uses: one keyspace, so a session started
  // there is listed and killable here.

  getSessionOptions: () =>
    apiFetch<SessionOptionsResponse>("/api/v1/sessions/options"),

  destroySession: (key: string) =>
    apiFetch<{ destroyed: boolean }>(
      `/api/v1/sessions/${encodeURIComponent(key)}`,
      { method: "DELETE" },
    ),

  // Binds the chat to a different brain. The subprocess is replaced, so the
  // conversation only carries over via `handoff`.
  switchSession: (key: string, body: SwitchSessionRequest) =>
    apiFetch<{ ok: boolean; session: SessionInfo }>(
      `/api/v1/sessions/${encodeURIComponent(key)}/action`,
      { method: "POST", body: JSON.stringify({ action: "switch", ...body }) },
    ),

  // ── Custom OpenAI-compatible LLM endpoints ──

  getCustomProviders: () =>
    apiFetch<{ providers: CustomProvider[] }>(
      "/api/v1/settings/custom-providers",
    ),

  /** Validates the endpoint server-side before saving; rejects with the
   *  provider's own error text (bad key, unreachable host, wrong shape). */
  addCustomProvider: (data: {
    base_url: string;
    api_key?: string;
    name?: string;
  }) =>
    apiFetch<{ provider: CustomProvider; models: string[] }>(
      "/api/v1/settings/custom-providers",
      { method: "POST", body: JSON.stringify(data) },
    ),

  getCustomProviderModels: (name: string) =>
    apiFetch<{ provider: CustomProvider; models: string[] }>(
      `/api/v1/settings/custom-providers/${encodeURIComponent(name)}/models`,
    ),

  deleteCustomProvider: (name: string) =>
    apiFetch<{ deleted: boolean; name: string }>(
      `/api/v1/settings/custom-providers/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),

  // ── Pending approvals (FEAT-010) ──

  /**
   * Approvals this user has not answered yet, scoped to the JWT server-side.
   *
   * The WS `permission_request` event is the low-latency path and dies with the
   * socket that carried it; this is how a page that just reloaded mid-approval
   * finds out an agent is still waiting on it. Answering still goes over the
   * socket — the registry is the same one either way.
   */
  getPendingConfirmations: () =>
    apiFetch<PendingConfirmation[]>("/api/v1/confirmations"),
};
