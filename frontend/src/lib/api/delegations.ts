/** Delegated tasks the agent hands to the operator. */

import { apiFetch } from "@/lib/api";

// How a delegation ended. `running`…`stopped` come from the live registry;
// `interrupted` is a task whose process died mid-flight, and `unknown` a
// transcript too old to say — both only ever arrive from history. `timeout` is
// a code run cut off by its budget (FEAT-061): the store records it apart from
// `error`, so the wire keeps it apart rather than flattening the distinction.
export type DelegationStatus =
  | "running"
  | "done"
  | "error"
  | "stopped"
  | "interrupted"
  | "timeout"
  | "unknown";

/**
 * Which channel a run came through (FEAT-058).
 *
 * `delegate` — a fire-and-forget background task handed to a detached Agent
 * instance. `consult` — the blocking ask one agent makes of another
 * (`delegate(action="ask")`), which records a ledger entry and no transcript.
 * `code` — a snippet the agent ran (FEAT-061); it comes from its own store and
 * the history route merges it in, so a row is a row whichever store answered.
 *
 * The name on the wire (and the route, and the directory) stays "delegation"
 * for both: it is where these records already lived, and splitting the store
 * would cost every reader a merge for a rename nobody would see.
 */
export type DelegationKind = "delegate" | "consult" | "code";

// Delegation = one agent run. The registry is in-process, but the record
// outlives it on disk — so this shape also comes back from history (FEAT-035),
// where `ended_at`/`tool_count` are known and the live registry has nothing to
// say. Fields a given kind does not have are absent rather than zeroed: a
// consult has no tool count, a delegation records no caller.
export interface Delegation {
  task_id: string;
  agent: string;
  user_id: number;
  chat_id: number;
  server_name: string | null;
  task: string;
  status: DelegationStatus;
  result: string;
  error: string;
  /** Absent on records written before kinds existed — those were delegations. */
  kind?: DelegationKind;
  /** Consults only: the slug of the agent that asked. "" is a person asking. */
  caller?: string;
  /** The conversation that started this task; "" when there was none. */
  conversation_id: string;
  /** Wall-clock start (epoch seconds) — drives the elapsed time. */
  started_at: number;
  ended_at?: number;
  tool_count?: number;
}

/** A history row: the record minus the bodies, which the sheet fetches on open. */
export type DelegationSummary = Omit<Delegation, "result" | "error">;

/**
 * One recorded snippet, in full (FEAT-061).
 *
 * A code row in the Activity feed is a `DelegationSummary` like any other; this
 * is what opening it fetches, and it is a different shape on purpose — a
 * delegation has one body, a code run has four (what ran, what it printed, what
 * it returned, how it broke).
 */
export interface CodeRun {
  id: string;
  created: number;
  agent: string;
  user_id: number;
  label: string;
  server: string;
  /** The store's own word: what `_CODE_STATUS` maps into a feed row's status. */
  status: "ok" | "error" | "timeout";
  duration_ms: number;
  code: string;
  stdout: string;
  /** The snippet's `result` global, rendered; "" when it set none. */
  result: string;
  error: string;
  traceback: string;
  report_id: string;
  session_key: string;
}

// One entry of a delegation's session transcript. Mirrors the three shapes the
// runner's event sink folds into `DelegateTask.events`; tool entries are patched
// in place server-side, so `id` is stable across a status flip.
export type DelegationEvent =
  | { type: "thought"; text: string }
  | { type: "text"; text: string }
  | {
      type: "tool";
      id: string;
      name: string;
      status: string;
      kind: string;
      input: Record<string, unknown> | null;
      /** Clipped at 2000 chars, the same boundary the on-disk transcript uses. */
      output: string | null;
    };

export const delegationsApi = {
  // ── Delegations (fire-and-forget background agent tasks) ──

  getDelegations: () =>
    apiFetch<{ delegations: Delegation[] }>("/api/v1/agents/delegations"),

  /** Every delegation ever recorded, newest first — live ones included, and the
   *  ones whose process is long gone. Optionally scoped to one agent. */
  /** Recorded runs, newest first. `kind` omitted means every channel. */
  getDelegationHistory: (agent?: string, limit = 100, kind?: DelegationKind) =>
    apiFetch<{ delegations: DelegationSummary[] }>(
      `/api/v1/agents/delegations/history?limit=${limit}` +
        (agent ? `&agent=${encodeURIComponent(agent)}` : "") +
        (kind ? `&kind=${kind}` : ""),
    ),

  /** One code run in full — code, stdout, result, traceback (FEAT-061).
   *  Behind the same gate that ran it: a run's output is as sensitive as the
   *  snippet was, so a caller without `code_run` gets a 403 and a run that is
   *  not theirs a 404. */
  getCodeRun: (runId: string) =>
    apiFetch<CodeRun>(`/api/v1/code/runs/${encodeURIComponent(runId)}`),

  /** One delegation's full record, from the registry or from disk. */
  getDelegation: (taskId: string) =>
    apiFetch<Delegation>(
      `/api/v1/agents/delegations/${encodeURIComponent(taskId)}`,
    ),

  /** The human-facing transcript. Separate from the (agent-facing) status route
   *  on purpose — see `get_delegation_events` in condor/web/routes/agents.py.
   *  `markdown` is the fallback for records written before the events sidecar:
   *  no structured events, just the transcript as it was rendered to disk. */
  getDelegationEvents: (taskId: string) =>
    apiFetch<{
      task_id: string;
      status: DelegationStatus;
      events: DelegationEvent[];
      markdown: string;
    }>(`/api/v1/agents/delegations/${encodeURIComponent(taskId)}/events`),

  stopDelegation: (taskId: string) =>
    apiFetch<{ task_id: string; status: string }>(
      `/api/v1/agents/delegations/${encodeURIComponent(taskId)}/stop`,
      { method: "POST" },
    ),
};
