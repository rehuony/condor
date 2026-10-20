/** Portfolio balances, history and the account snapshot. */

import { apiFetch } from "@/lib/api";

export interface BalanceItem {
  token: string;
  total: number;
  available: number;
  usd_value: number;
}

export interface ConnectorBalance {
  account_name?: string;
  connector: string;
  balances: BalanceItem[];
  total_usd: number;
  note?: string | null;
  equity_usd?: number | null;
  unrealized_pnl_usd?: number | null;
}

export interface PortfolioResponse {
  server: string;
  connectors: ConnectorBalance[];
  total_usd: number;
  equity_usd?: number | null;
  unrealized_pnl_usd?: number | null;
}

export interface PortfolioHistoryPoint {
  timestamp: number;
  total_usd: number;
  tokens?: Record<string, number>;
}

export interface PortfolioHistoryResponse {
  server: string;
  points: PortfolioHistoryPoint[];
  interval: string;
  top_tokens?: string[];
  valuation?: "wallet" | "equity";
}

export interface ConsolidatedPosition {
  connector_name: string;
  trading_pair: string;
  position_side: string;
  amount: number;
  entry_price: number;
  notional_value: number;
  current_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  cum_fees: number;
  executor_count: number;
  /** The executors this row aggregates — how a hold is traced back to a pool. */
  executor_ids?: string[];
  leverage: number;
  controller_id: string;
  source: "executor" | "bot";
  source_name: string;
  /**
   * Set only on a pool workspace: whether this hold is known to be *this* pool's.
   *
   * `true` when a contributing LP executor records the pool on screen, `false`
   * when nothing ties it to any pool — a swap is routed by the aggregator, so
   * the position it leaves behind is a fact about the pair, not about a pool.
   * Undefined off a pool page, where the question does not arise.
   */
  pool_scoped?: boolean;
}

export interface ConsolidatedPositionsResponse {
  executor_positions: ConsolidatedPosition[];
  bot_positions: ConsolidatedPosition[];
}

export const portfolioApi = {
  getPortfolio: (server: string, refresh = false) =>
    apiFetch<PortfolioResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/portfolio${refresh ? "?refresh=true" : ""}`,
    ),

  getPortfolioHistory: (server: string, range = "1D", breakdown = false) =>
    apiFetch<PortfolioHistoryResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/portfolio/history?range=${encodeURIComponent(range)}${breakdown ? "&breakdown=true" : ""}`,
    ),

  getConsolidatedPositions: (server: string) =>
    apiFetch<ConsolidatedPositionsResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/positions`,
    ),
};
