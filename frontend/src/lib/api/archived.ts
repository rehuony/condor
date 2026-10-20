/** Archived bot records and their stored detail. */

import { apiFetch } from "@/lib/api";

import type { ArchivedExecutor } from "./executors";

// ── Archived Bots ──

export interface ArchivedBotSummary {
  bot_name: string;
  db_path: string;
  total_trades: number;
  total_orders: number;
  trading_pairs: string[];
  exchanges: string[];
  start_time: number | null;
  end_time: number | null;
}

export interface PnlPoint {
  timestamp: number;
  pnl: number;
}

export interface ArchivedControllerRollup {
  /** "" for the executors that ran under no controller — an LP or manual leg. */
  controller_id: string;
  pnl_usd: number;
  volume_usd: number;
  fees_usd: number;
  executor_count: number;
  first_ts: number;
  last_ts: number;
  trading_pairs: string[];
  connectors: string[];
}

/**
 * The stored report for a run, or one controller of it.
 *
 * `report_id` is null for the ordinary case: nobody has charted this subject
 * yet, or the report that did has since been pruned from the index.
 */
export interface ArchivedRunReport {
  report_id: string | null;
  created_at: string | null;
  title: string;
}

export interface ArchivedBotPerformance {
  bot_name: string;
  db_path: string;
  total_pnl: number;
  total_fees: number;
  total_volume: number;
  trade_count: number;
  buy_count: number;
  sell_count: number;
  pnl_by_pair: Record<string, number>;
  cumulative_pnl: PnlPoint[];
  trading_pairs: string[];
  exchanges: string[];
  executors: ArchivedExecutor[];
  primary_connector: string;
  primary_trading_pair: string;
  executor_count: number;
  /** Quote currency of the primary market, e.g. "BRL". */
  quote_currency: string;
  /** USD rate per quote currency seen in the run. */
  usd_rates: Record<string, number>;
  /** False when a quote had no path to USD; its figures stay in that currency. */
  converted: boolean;
  /** "trades" or "executors" — which source the headline stats came from. */
  stats_source: string;
}

export interface PaginatedExecutors {
  executors: ArchivedExecutor[];
  total: number;
  offset: number;
  limit: number;
}

export const archivedApi = {
  // ── Archived Bots ──

  getArchivedBots: (server: string) =>
    apiFetch<{ bots: ArchivedBotSummary[] }>(
      `/api/v1/servers/${encodeURIComponent(server)}/archived`,
    ),

  getArchivedBotPerformance: (
    server: string,
    dbPath: string,
    includeExecutors = false,
  ) =>
    apiFetch<ArchivedBotPerformance>(
      `/api/v1/servers/${encodeURIComponent(server)}/archived/performance?db_path=${encodeURIComponent(dbPath)}&include_executors=${includeExecutors}`,
    ),

  getArchivedExecutors: (
    server: string,
    dbPath: string,
    offset = 0,
    limit = 50,
  ) =>
    apiFetch<PaginatedExecutors>(
      `/api/v1/servers/${encodeURIComponent(server)}/archived/executors?db_path=${encodeURIComponent(dbPath)}&offset=${offset}&limit=${limit}`,
    ),

  getArchivedControllers: (server: string, dbPath: string) =>
    apiFetch<{ controllers: ArchivedControllerRollup[] }>(
      `/api/v1/servers/${encodeURIComponent(server)}/archived/controllers?db_path=${encodeURIComponent(dbPath)}`,
    ),

  /**
   * The stored report for this run, or one controller of it.
   *
   * The subject key is built server-side from these parts — the dashboard sends
   * components, never a key, so a lookup cannot be pointed at another server.
   */
  getArchivedReport: (server: string, dbPath: string, controllerId = "") =>
    apiFetch<ArchivedRunReport>(
      `/api/v1/servers/${encodeURIComponent(server)}/archived/report?db_path=${encodeURIComponent(dbPath)}&controller_id=${encodeURIComponent(controllerId)}`,
    ),
};
