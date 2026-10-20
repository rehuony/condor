/** Controller and bot performance history, and the capability probe. */

import { apiFetch, query } from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import { collectCursorPages, type WalkOutcome } from "@/lib/history-pagination";

export interface ControllerPerformanceSnapshot {
  timestamp: string;
  bot_name: string;
  controller_id: string;
  controller_name: string;
  connector: string;
  trading_pair: string;
  realized_pnl_quote: number;
  unrealized_pnl_quote: number;
  global_pnl_quote: number;
  global_pnl_pct: number;
  volume_traded: number;
  positions_summary: Record<string, unknown>[];
}

export interface ControllerPerformanceHistoryResponse {
  snapshots: ControllerPerformanceSnapshot[];
  next_cursor: string | null;
  interval: string;
  server_online?: boolean;
  error_hint?: string;
}

/** Which population a performance query is about. */
export type PerfSubject = "controller" | "executor";

/**
 * One point of a performance series, for either population (FEAT-087).
 *
 * Distinct from `ControllerPerformanceSnapshot`, which is the wire shape of the
 * older controller-only route: the numbers here are flat rather than nested
 * under `performance`, and volume is spelled `volume_quote`. The two stay
 * separate so neither grows a branch for the other's payload.
 */
export interface PerformanceSnapshot {
  timestamp: string;
  subject: PerfSubject | "";
  /** `controller_id` for controllers, `executor_id` for executors. */
  scope_id: string;
  status: string;
  /**
   * True only on a completed executor's final row. It is what makes a closed
   * executor's series a single query — the terminal row *is* the last point,
   * so nothing appends a final value after it.
   */
  is_terminal: boolean;
  realized_pnl_quote: number;
  unrealized_pnl_quote: number;
  global_pnl_quote: number;
  global_pnl_pct: number;
  /** Volume *generated*, in quote — on every executor type, LP included. */
  volume_quote: number;
  /**
   * `null` for controllers, whose `PerformanceReport` has no fees field.
   * Unknown is not zero: a fees figure must render the two differently.
   */
  cum_fees_quote: number | null;
  bot_name: string | null;
  controller_id: string | null;
  executor_id: string | null;
  executor_type: string | null;
  account_name: string | null;
  connector_name: string | null;
  trading_pair: string | null;
  /** Set on an executor's terminal row. `POSITION_HOLD` stays *unrealized*. */
  close_type: string | null;
}

export interface PerformanceHistoryResponse {
  snapshots: PerformanceSnapshot[];
  next_cursor: string | null;
  interval: string;
  subject: PerfSubject | "";
  /**
   * False when this server has no `/performance/history` at all — an older
   * API, which is the ordinary case rather than a failure. The client falls
   * back to its derived series and the chart says why.
   */
  supported: boolean;
  server_online?: boolean;
  error_hint?: string;
}

/**
 * Whether one server serves the shared performance surface.
 *
 * `unknown` separates "asked, and it is not there" from "could not ask": a
 * server that was merely down must not have a fallback pinned to it for the
 * whole cache TTL after it comes back.
 */
export interface PerformanceCapabilityResponse {
  supported: boolean;
  unknown: boolean;
  detail?: string | null;
}

// ── Controller performance history ──

/** Everything the history route accepts except the two the walk owns. */
export interface ControllerPerformanceHistoryQuery {
  bot_name?: string;
  controller_id?: string;
  start_time?: string;
  end_time?: string;
  interval?: string;
}

/** A whole history, assembled from however many pages it took. */
export interface ControllerPerformanceHistoryAllResponse extends ControllerPerformanceHistoryResponse {
  /** How many requests were issued. */
  pages: number;
  /** True when a cap or a failure ended the walk before the history ran out. */
  truncated: boolean;
  /** Why it stopped — `"complete"` is the only one that means "this is all of it". */
  outcome: WalkOutcome;
}

function fetchControllerPerformanceHistoryPage(
  server: string,
  params: ControllerPerformanceHistoryQuery & {
    limit?: number;
    cursor?: string;
  } = {},
  init?: RequestInit,
) {
  return apiFetch<ControllerPerformanceHistoryResponse>(
    `/api/v1/servers/${encodeURIComponent(server)}/controller-performance/history${query(params)}`,
    init,
  );
}

/**
 * The whole history a query describes, walked page by page.
 *
 * One request answers with at most `limit` ROWS, and the route caps `limit` at
 * 1000 (CORR-260) — but the sampler writes one row per controller per dump, so
 * for a fleet of N controllers those 1000 rows are only 1000/N instants. Both
 * charts used to issue that single request and never look at `next_cursor`, so
 * the visible window silently collapsed as controllers were added: ten
 * controllers at five-minute dumps drew eight hours however far back the bots
 * actually went (CORR-237). The cursor is the only way to ask for more, and
 * since CORR-259 the route actually forwards it.
 *
 * **The interval is bound once, here, and every page carries that same value.**
 * It is not a detail: `pickSamplingInterval` chooses a resolution from how long
 * the fleet has been running (PERF-238), and a follow-up page fetched at a
 * different resolution would splice two resolutions into one series — a line
 * whose left half is hourly and whose right half is five-minutely, with no
 * visible seam. `params` is spread into every request unchanged and only
 * `limit`/`cursor` vary between pages, which is what makes that true by
 * construction rather than by discipline.
 *
 * The result is shape-compatible with a single page — same `snapshots`, same
 * `interval` — because the shared socket merges live frames into whatever these
 * queries cached (`mergeIntoMatchingQueries`) by spreading it. It adds only
 * `pages`/`truncated`/`outcome`, and `next_cursor` becomes "where a resumed
 * walk would continue", which is null exactly when the history ran out.
 */
async function fetchControllerPerformanceHistoryAll(
  server: string,
  params: ControllerPerformanceHistoryQuery = {},
  opts: {
    pageSize?: number;
    maxRows?: number;
    maxPages?: number;
    signal?: AbortSignal;
  } = {},
): Promise<ControllerPerformanceHistoryAllResponse> {
  let first: ControllerPerformanceHistoryResponse | undefined;

  const walk = await collectCursorPages<ControllerPerformanceSnapshot>(
    async ({ limit, cursor }) => {
      const page = await fetchControllerPerformanceHistoryPage(
        server,
        { ...params, limit, cursor },
        { signal: opts.signal },
      );
      first ??= page;
      return {
        rows: page.snapshots ?? [],
        nextCursor: page.next_cursor ?? null,
      };
    },
    {
      pageSize: opts.pageSize,
      maxRows: opts.maxRows,
      maxPages: opts.maxPages,
      signal: opts.signal,
      // Pages overlap when the newest one is being appended to as it is read.
      // Keyed on bot + controller + timestamp, because a bare controller id is
      // a config id two bots can share (CORR-241) and their rows land on one
      // shared dump timestamp.
      dedupeKey: (snap) => `${controllerKey(snap)}:${snap.timestamp}`,
    },
  );

  return {
    snapshots: walk.rows,
    next_cursor: walk.nextCursor,
    interval: first?.interval ?? params.interval ?? "5m",
    server_online: first?.server_online,
    error_hint: first?.error_hint,
    pages: walk.pages,
    truncated: walk.truncated,
    outcome: walk.outcome,
  };
}

/**
 * The fleet's history, fetched **one controller at a time** (FEAT-089).
 *
 * The route this wraps looks like it takes a fleet and returns a fleet's
 * history, and it does not: upstream's downsampler buckets by *time only*, so a
 * request that spans several controllers keeps one row per bucket and silently
 * drops the rest. Measured against a real 12-controller fleet over one window,
 * the same query answers with 12 of 12 controllers at `5m` and 11 of 12 at
 * `1h` — and `samplingIntervalSince` picks `15m` or coarser for any fleet older
 * than ~3.5 days (PERF-238). So the fleet chart has been quietly missing
 * controllers on every long-lived fleet, forward-filling whatever it happened
 * to receive.
 *
 * Binding `controller_id` fixes it at the source: each bucket then holds only
 * that controller's rows, so nothing is dropped at any interval. The same 12
 * controllers at `1h` come back complete in 912 rows and 1.28 MB — against
 * ~104k rows for the same span at `5m`, which is what asking for the finest
 * interval instead would cost.
 *
 * `bot_name` is bound too, because a controller id is a *config* id two bots
 * can share (CORR-241).
 *
 * The result is shape-compatible with the single-query walk it replaces —
 * same `snapshots`, same `interval` — because the shared socket merges live
 * frames into whatever these queries cached by spreading it. `truncated` is
 * true if *any* controller's walk was cut short: a fleet series missing one
 * controller's oldest end is a partial fleet series.
 */
export async function fetchControllerPerformanceHistoryByController(
  server: string,
  controllers: readonly { bot_name: string; controller_id: string }[],
  params: ControllerPerformanceHistoryQuery = {},
  opts: {
    pageSize?: number;
    /** Rows per controller. One page covers `HISTORY_POINT_BUDGET` instants. */
    maxRows?: number;
    maxPages?: number;
    signal?: AbortSignal;
    concurrency?: number;
  } = {},
): Promise<ControllerPerformanceHistoryAllResponse> {
  const { concurrency: requested, ...walkOpts } = opts;
  const targets = controllers.filter((c) => c.controller_id);
  if (targets.length === 0) {
    return {
      snapshots: [], next_cursor: null, interval: params.interval ?? "5m",
      pages: 0, truncated: false, outcome: "complete",
    };
  }

  const results: ControllerPerformanceHistoryAllResponse[] = [];
  // Bounded rather than all at once: these are sequential page walks against
  // one API that is also serving the live fleet, and a 30-controller fleet
  // opening 30 simultaneous walks is a self-inflicted outage.
  const concurrency = Math.max(1, requested ?? 4);
  for (let i = 0; i < targets.length; i += concurrency) {
    results.push(
      ...(await Promise.all(
        targets.slice(i, i + concurrency).map((c) =>
          fetchControllerPerformanceHistoryAll(
            server,
            { ...params, bot_name: c.bot_name, controller_id: c.controller_id },
            walkOpts,
          ),
        ),
      )),
    );
  }

  return {
    snapshots: results.flatMap((r) => r.snapshots),
    // Per-controller cursors cannot be merged into one, and nothing resumes
    // this walk: `refreshControllerHistory` tails by `start_time`, not by
    // cursor. Saying null would claim the history ran out, so `truncated`
    // carries that fact instead.
    next_cursor: null,
    interval: results[0]?.interval ?? params.interval ?? "5m",
    server_online: results.find((r) => r.server_online === false)?.server_online,
    error_hint: results.find((r) => r.error_hint)?.error_hint,
    pages: results.reduce((sum, r) => sum + r.pages, 0),
    truncated: results.some((r) => r.truncated),
    outcome:
      results.find((r) => r.outcome === "error")?.outcome ??
      results.find((r) => r.outcome !== "complete")?.outcome ??
      "complete",
  };
}

// ── The shared performance surface (FEAT-087) ──

/** Everything `/performance/history` accepts except the two the walk owns. */
export interface PerformanceHistoryQuery {
  subject: PerfSubject;
  bot_name?: string;
  controller_id?: string;
  executor_id?: string;
  executor_type?: string;
  account_name?: string;
  connector_name?: string;
  trading_pair?: string;
  start_time?: string;
  end_time?: string;
  interval?: string;
}

/** A whole performance history, assembled from however many pages it took. */
export interface PerformanceHistoryAllResponse extends PerformanceHistoryResponse {
  pages: number;
  truncated: boolean;
  outcome: WalkOutcome;
}

function fetchPerformanceHistoryPage(
  server: string,
  params: PerformanceHistoryQuery & { limit?: number; cursor?: string },
  init?: RequestInit,
) {
  return apiFetch<PerformanceHistoryResponse>(
    `/api/v1/servers/${encodeURIComponent(server)}/performance/history${query(params)}`,
    init,
  );
}

/**
 * The whole history a performance query describes, walked page by page.
 *
 * The same bounded walk the controller history uses, for the same reasons: one
 * request answers with at most `limit` rows, the route caps `limit` at 1000,
 * and dropping `next_cursor` silently collapses the visible window (CORR-237).
 * The interval is bound once here and every page carries it, so a follow-up
 * page cannot splice a second resolution into one line.
 *
 * **A server without the route ends the walk on the first page**, with
 * `supported: false` and no rows. That is not an error and not a short page to
 * retry: the route does not exist, and the caller draws its derived series.
 */
export async function fetchPerformanceHistoryAll(
  server: string,
  params: PerformanceHistoryQuery,
  opts: { pageSize?: number; maxRows?: number; maxPages?: number; signal?: AbortSignal } = {},
): Promise<PerformanceHistoryAllResponse> {
  let first: PerformanceHistoryResponse | undefined;

  const walk = await collectCursorPages<PerformanceSnapshot>(
    async ({ limit, cursor }) => {
      const page = await fetchPerformanceHistoryPage(
        server,
        { ...params, limit, cursor },
        { signal: opts.signal },
      );
      first ??= page;
      // An unsupported route has no next page and never will. Reporting no
      // cursor ends the walk on this one request rather than on the row cap.
      return {
        rows: page.supported === false ? [] : (page.snapshots ?? []),
        nextCursor: page.supported === false ? null : (page.next_cursor ?? null),
      };
    },
    {
      pageSize: opts.pageSize,
      maxRows: opts.maxRows,
      maxPages: opts.maxPages,
      signal: opts.signal,
      // Pages overlap when the newest one is being appended to as it is read.
      // Keyed on the scope joined to the instant — one row per scope per dump.
      dedupeKey: (row) => `${row.bot_name ?? ""}:${row.scope_id}:${row.timestamp}`,
    },
  );

  return {
    snapshots: walk.rows,
    next_cursor: walk.nextCursor,
    interval: first?.interval ?? params.interval ?? "5m",
    subject: params.subject,
    supported: first?.supported ?? true,
    server_online: first?.server_online,
    error_hint: first?.error_hint,
    pages: walk.pages,
    truncated: walk.truncated,
    outcome: walk.outcome,
  };
}

export const performanceApi = {
  /** One page of controller performance history. Charts want the whole thing — see below. */
  getControllerPerformanceHistory: fetchControllerPerformanceHistoryPage,

  /**
   * Every page of controller performance history, at one constant interval.
   *
   * What the charts call: a single page is at most 1000 rows, and rows are per
   * controller, so one request is a window that shrinks as the fleet grows
   * (CORR-237).
   */
  getControllerPerformanceHistoryAll: fetchControllerPerformanceHistoryAll,

  /**
   * The fleet's history, one controller at a time — the only way to get all of
   * them at an interval coarser than `5m` (FEAT-089).
   */
  getControllerPerformanceHistoryByController:
    fetchControllerPerformanceHistoryByController,

  /**
   * Whether this server serves `/performance/history` (FEAT-087).
   *
   * One request per server, cached server-side with the other per-server data,
   * so every chart on the page shares one answer and a tree click costs
   * nothing.
   */
  getPerformanceCapability: (server: string) =>
    apiFetch<PerformanceCapabilityResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/performance/capability`,
    ),

  /** One scope's sampled performance series, for either population (FEAT-087). */
  getPerformanceHistory: fetchPerformanceHistoryAll,
};
