/** Market data: candles, trading rules, prices and connector lists. */

import { apiFetch } from "@/lib/api";

export interface CandleData {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface MarketPrice {
  connector: string;
  trading_pair: string;
  mid_price: number;
  best_bid: number;
  best_ask: number;
}

/**
 * The pool a DEX pair trades in — the same one the chart's candles come from.
 *
 * `lp_supported` is false, with a null `lp_provider`, whenever the deepest pool is
 * not one an `lp_executor` can add liquidity to (a router, a plain AMM, Uniswap
 * v4). That is a normal answer, not an error: the LP panel responds by asking for
 * a pool address by hand. `current_price` is the base priced in quote — the scale
 * every LP bound is expressed in — and is null when GeckoTerminal reports no price.
 */
/** Wire shape of `GET /market/venues`; mapped to `VenueTraits` on the way in. */
interface WireVenue {
  name: string;
  hummingbot_market_data: boolean;
  clmm_lp: boolean;
  /**
   * Optional only so a frontend that gets ahead of its backend degrades the safe
   * way: a server that predates ARCH-271 omits the key, and an absent trait must
   * read as *credentialed* (the pre-ARCH-272 status quo, where every venue the
   * endpoint reported was one the account held keys on). Reading it as `false`
   * would put the whole panel behind the view-only overlay.
   */
  credentialed?: boolean;
}

export interface OrderBookLevel {
  price: number;
  amount: number;
}

export interface OrderBookResponse {
  connector: string;
  trading_pair: string;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
}

export interface TradingRule {
  trading_pair: string;
  min_order_size: number;
  min_notional_size: number;
  min_price_increment: number;
  min_base_amount_increment: number;
}

export interface TradingRulesResponse {
  connector: string;
  rules: TradingRule[];
}

export interface Ticker {
  trading_pair: string;
  price: number;
  base_volume: number;
  quote_volume: number;
  /** 24h volume in USD; null when the quote asset couldn't be priced. */
  usd_volume: number | null;
  /**
   * Price change in percent against the snapshot closest to 24h ago; null until
   * the backend has a reference, and for a pair listed after it was taken.
   */
  change_pct: number | null;
  /**
   * The window `change_pct` was actually measured over, in seconds — never
   * assume 24h, label the column from this.
   */
  change_window_s: number | null;
}

export interface TickersResponse {
  connector: string;
  tickers: Ticker[];
  updated_at: number | null;
}

export const marketApi = {
  getConnectedExchanges: (server: string) =>
    apiFetch<string[]>(
      `/api/v1/servers/${encodeURIComponent(server)}/market/connected-exchanges`,
    ),

  /** Venues the trade panel can offer, with the traits each UI decision rests on. */
  getVenues: (server: string) =>
    apiFetch<{ venues: WireVenue[] }>(
      `/api/v1/servers/${encodeURIComponent(server)}/market/venues`,
    ).then((r) =>
      (r.venues ?? []).map((v) => ({
        name: v.name,
        hummingbotMarketData: !!v.hummingbot_market_data,
        clmmLp: !!v.clmm_lp,
        credentialed: v.credentialed ?? true,
      })),
    ),

  getPrice: (server: string, connector: string, pair: string) =>
    apiFetch<MarketPrice>(
      `/api/v1/servers/${encodeURIComponent(server)}/market/prices?connector=${encodeURIComponent(connector)}&trading_pair=${encodeURIComponent(pair)}`,
    ),

  getRates: (server: string, tradingPairs: string[], connector?: string) =>
    apiFetch<{ rates: Record<string, number | null> }>(
      `/api/v1/servers/${encodeURIComponent(server)}/market/rates`,
      {
        method: "POST",
        body: JSON.stringify({ trading_pairs: tradingPairs, connector }),
      },
    ),

  getTradingRules: (server: string, connector: string) =>
    apiFetch<TradingRulesResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/market/trading-rules?connector=${encodeURIComponent(connector)}`,
    ),

  getTickers: (server: string, connector: string) =>
    apiFetch<TickersResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/market/tickers?connector=${encodeURIComponent(connector)}`,
    ),

  getOrderBook: (server: string, connector: string, pair: string, depth = 20) =>
    apiFetch<OrderBookResponse>(
      `/api/v1/servers/${encodeURIComponent(server)}/market/order-book?connector=${encodeURIComponent(connector)}&trading_pair=${encodeURIComponent(pair)}&depth=${depth}`,
    ),

  getCandles: async (
    server: string,
    connector: string,
    pair: string,
    interval = "1m",
    limit = 1000,
    startTime?: number,
    endTime?: number,
    poolAddress?: string,
    signal?: AbortSignal,
  ) => {
    let url = `/api/v1/servers/${encodeURIComponent(server)}/market/candles?connector=${encodeURIComponent(connector)}&trading_pair=${encodeURIComponent(pair)}&interval=${encodeURIComponent(interval)}&limit=${limit}`;
    if (startTime !== undefined) url += `&start_time=${startTime}`;
    if (endTime !== undefined) url += `&end_time=${endTime}`;
    // DEX/LP executors: the pool address routes the backend to GeckoTerminal,
    // since these connectors have no CEX candle feed.
    if (poolAddress) url += `&pool_address=${encodeURIComponent(poolAddress)}`;
    signal?.throwIfAborted();
    const controller = new AbortController();
    const abort = () => controller.abort(signal?.reason);
    signal?.addEventListener("abort", abort, { once: true });
    // Allow the upstream's 30-second deadline to report its error, then bound the
    // browser request (including its body) so a stalled proxy cannot hold the chart.
    const timeout = setTimeout(() => controller.abort(new DOMException(
      "Candle history request timed out. Please retry.", "TimeoutError",
    )), 40_000);
    try {
      return await apiFetch<CandleData[]>(url, { signal: controller.signal });
    } finally {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
    }
  },

  // Resolve a token address → ticker (GeckoTerminal). Not server-scoped; used to
  // render LP/DEX pairs, which are stored as `<address>-<quote>`.
  getTokenSymbol: (mint: string, network?: string) =>
    apiFetch<{ mint: string; symbol: string }>(
      `/api/v1/market/token-symbol?mint=${encodeURIComponent(mint)}${network ? `&network=${encodeURIComponent(network)}` : ""}`,
    ),
};
