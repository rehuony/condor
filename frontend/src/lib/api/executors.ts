/** Executor pages, detail and the deploy/stop operations. */

import { apiFetch, query } from "@/lib/api";

export interface ExecutorInfo {
  id: string;
  type: string;
  connector: string;
  trading_pair: string;
  side: string;
  status: string;
  close_type: string;
  pnl: number;
  volume: number;
  timestamp: number;
  controller_id: string;
  cum_fees_quote: number;
  net_pnl_pct: number;
  entry_price: number;
  current_price: number;
  close_timestamp: number;
  custom_info: Record<string, unknown>;
  config: Record<string, unknown>;
}

/**
 * An executor row from an *archived* run (backend `NormalizedExecutor`).
 *
 * Only the archived reader resolves a USD rate per row, so only the archived
 * type carries one. Declaring `usd_rate` on the shared `ExecutorInfo` promised
 * a field the live `/executors` route and the WS frames never send (backend
 * `ExecutorInfo` has no such field), which invited a live surface to write
 * `ex.usd_rate ?? 1` and report a BRL or EUR figure behind a `$`.
 *
 * Required, not optional: the backend defaults it to 1.0 and
 * `QuoteRates.for_pair` falls back to 1.0 for an unresolvable quote, so an
 * archived row always arrives with a number.
 */
export interface ArchivedExecutor extends ExecutorInfo {
  /** USD value of one unit of this market's quote. `pnl`, `volume` and
   *  `cum_fees_quote` are quote-denominated; multiply by this to show USD. */
  usd_rate: number;
}

/** Executor totals for a period, aggregated server-side over the full history.
 *  `pnl` and `volume` are USD-denominated; `converted` is false when some quote
 *  asset had no path to USD and its rows were counted in their own quote. */
export interface ExecutorPeriodSummary {
  period: string;
  pnl: number;
  volume: number;
  count: number;
  converted: boolean;
}

export interface PositionHeld {
  connector_name: string;
  trading_pair: string;
  position_side?: string;
  side?: string;
  net_amount_base?: number;
  amount?: number;
  buy_breakeven_price?: number;
  entry_price?: number;
  current_price?: number;
  unrealized_pnl_quote?: number;
  unrealized_pnl?: number;
  leverage?: number;
  controller_id?: string;
}

export interface PositionsResponse {
  positions: PositionHeld[];
  summary: Record<string, unknown>;
}

export const executorsApi = {
  getExecutors: (
    server: string,
    params?: {
      executor_type?: string;
      trading_pair?: string;
      status?: string;
      controller_id?: string;
      limit?: number;
    },
  ) => {
    return apiFetch<ExecutorInfo[]>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors${query(params ?? {})}`,
    );
  },

  getExecutorsSummary: (server: string, period: string) =>
    apiFetch<ExecutorPeriodSummary>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors/summary?period=${encodeURIComponent(period)}`,
    ),

  getExecutorsPage: (
    server: string,
    params: {
      cursor?: string;
      limit?: number;
      executor_type?: string;
      trading_pair?: string;
      status?: string;
      controller_id?: string;
    } = {},
  ) => {
    return apiFetch<{ executors: ExecutorInfo[]; next_cursor: string | null }>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors/page${query({ ...params, limit: params.limit ?? 50 })}`,
    );
  },

  // No `controller_id`: the create route reads only these three fields, so an
  // id sent here would be dropped and the executor would come back under the
  // trading API's `main` default anyway. See `run-attribution.ts`.
  createExecutor: (
    server: string,
    data: {
      executor_type: string;
      config: Record<string, unknown>;
      account_name?: string;
    },
  ) =>
    apiFetch<{ status: string; executor_id: string }>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors`,
      { method: "POST", body: JSON.stringify(data) },
    ),

  stopExecutor: (server: string, executorId: string, keepPosition = false) =>
    apiFetch<{ status: string; result: unknown }>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors/${encodeURIComponent(executorId)}/stop?keep_position=${keepPosition}`,
      { method: "POST" },
    ),

  deleteExecutor: (server: string, executorId: string) =>
    apiFetch<{ deleted: boolean; executor_id: string }>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors/${encodeURIComponent(executorId)}`,
      { method: "DELETE" },
    ),

  getPositionsHeld: (server: string) =>
    apiFetch<PositionsResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors/positions`,
    ),

  clearPositionHeld: (
    server: string,
    connector: string,
    pair: string,
    controllerId?: string,
  ) => {
    const params = controllerId
      ? `?controller_id=${encodeURIComponent(controllerId)}`
      : "";
    return apiFetch<{ status: string }>(
      `/api/v1/servers/${encodeURIComponent(server)}/executors/positions/${encodeURIComponent(connector)}/${encodeURIComponent(pair)}${params}`,
      { method: "DELETE" },
    );
  },
};
