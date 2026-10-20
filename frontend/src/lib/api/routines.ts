/** Routine catalog, instances, schedules and hooks. */

import { apiFetch } from "@/lib/api";

import type { ReportsListResponse } from "./reports";

// ── Routines ──

export interface RoutineFieldInfo {
  type: string;
  default: unknown;
  description: string;
  widget?: "select";
  options_from?: string;
}

export interface RoutineInfo {
  name: string;
  description: string;
  is_continuous: boolean;
  category: string;
  source: string;
  fields: Record<string, RoutineFieldInfo>;
  /** Source-file modification time (epoch seconds), or null if unavailable. */
  last_modified: number | null;
  report_count: number;
}

export interface RoutineInstance {
  instance_id: string;
  routine_name: string;
  config: Record<string, unknown>;
  status: string;
  source: string;
  /** The server the run was launched against. */
  server_name?: string;
  /**
   * The conversation that asked for this run, "" for the scheduler, the
   * dashboard and Telegram. Set since ARCH-089, and what scopes a run to a
   * chat — the routine's own name cannot, since an agent runs shared-library
   * routines under their bare name.
   */
  conversation_id?: string;
  schedule?: Record<string, unknown>;
  created_at: number;
  last_run_at: number | null;
  last_result: string | null;
  last_duration: number | null;
  /** The report the last run rendered, if it rendered one. */
  report_id?: string | null;
  run_count: number;
  has_result?: boolean;
  result_text?: string;
  has_chart?: boolean;
  table_data?: Record<string, unknown>[] | null;
  table_columns?: string[] | null;
  sections?: Record<string, unknown>[] | null;
  error?: string | null;
}

export interface RoutineHooks {
  telegram: { enabled: boolean; chat_ids: string[] };
  trigger: "success" | "always" | "failure";
}

export const routinesApi = {
  // ── Routines ──

  getRoutines: () => apiFetch<RoutineInfo[]>("/api/v1/routines"),

  getRoutineInstances: () =>
    apiFetch<RoutineInstance[]>("/api/v1/routines/instances"),

  getRoutineInstance: (id: string) =>
    apiFetch<RoutineInstance>(
      `/api/v1/routines/instances/${encodeURIComponent(id)}`,
    ),

  /**
   * Run a routine once.
   *
   * `opts` is the conversation behind the run, when there is one: the route
   * resolves `session_key` into a conversation id, stamps the instance with it
   * — which is what puts the run in that conversation's dock — and posts the
   * outcome back into the chat when it lands (`on_complete` defaults to
   * `notify` there). Omitted, the run is what it always was: nobody's but the
   * dashboard's.
   */
  runRoutine: (
    server: string,
    name: string,
    config: Record<string, unknown> = {},
    opts?: { sessionKey?: string; attributeTo?: string },
  ) =>
    apiFetch<{ instance_id: string }>(`/api/v1/routines/run`, {
      method: "POST",
      body: JSON.stringify({
        routine_name: name,
        server_name: server,
        config,
        ...(opts?.sessionKey ? { session_key: opts.sessionKey } : {}),
        ...(opts?.attributeTo ? { attribute_to: opts.attributeTo } : {}),
      }),
    }),

  scheduleRoutine: (
    server: string,
    name: string,
    config: Record<string, unknown> = {},
    interval_sec: number = 300,
  ) =>
    apiFetch<{ instance_id: string }>(`/api/v1/routines/schedule`, {
      method: "POST",
      body: JSON.stringify({
        routine_name: name,
        server_name: server,
        config,
        interval_sec,
      }),
    }),

  stopRoutineInstance: (id: string) =>
    apiFetch<{ stopped: boolean }>(
      `/api/v1/routines/instances/${encodeURIComponent(id)}/stop`,
      {
        method: "POST",
      },
    ),

  getRoutineSource: (name: string) =>
    apiFetch<{ filename: string; source: string }>(
      `/api/v1/routines/${encodeURIComponent(name)}/source`,
    ),

  getRoutineReports: (name: string) =>
    apiFetch<ReportsListResponse>(
      `/api/v1/routines/${encodeURIComponent(name)}/reports`,
    ),

  getRoutineFieldOptions: (source: string, server: string) =>
    apiFetch<{ options: string[] }>(
      `/api/v1/routines/options/${encodeURIComponent(source)}?server=${encodeURIComponent(server)}`,
    ),

  getRoutineHooks: (name: string) =>
    apiFetch<RoutineHooks>(
      `/api/v1/routines/${encodeURIComponent(name)}/hooks`,
    ),

  saveRoutineHooks: (name: string, hooks: RoutineHooks) =>
    apiFetch<RoutineHooks>(
      `/api/v1/routines/${encodeURIComponent(name)}/hooks`,
      {
        method: "PUT",
        body: JSON.stringify(hooks),
      },
    ),
};
