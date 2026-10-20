/** Gateway/DEX reads and writes: wallets, pools, swaps and liquidity. */

import { apiFetch } from "@/lib/api";

/**
 * One pool as the DEX browser and workspace see it: the market numbers from the
 * upstream, plus the venue facts the server decides (`lp_supported`, `tradable`,
 * `trading_pair`) because they depend on which connectors Condor drives, not on
 * anything the browser can know.
 */
export interface PoolSummary {
  address: string;
  name: string;
  source: "gecko" | "gateway";
  dex_id: string;
  /** Chain as the caller named it, and the Gateway network its rows link to. */
  network: string;
  gecko_network: string;
  gateway_network: string;
  base_symbol: string;
  quote_symbol: string;
  base_token_symbol: string;
  quote_token_symbol: string;
  base_token_address: string;
  quote_token_address: string;
  /** `<base_mint>-<quote_symbol>`, the form LP and DEX order executors carry. */
  trading_pair: string;
  /** An `lp_executor` provider (`meteora/clmm`), or null when the pool is not CLMM. */
  lp_provider: string | null;
  lp_supported: boolean;
  /** False when GeckoTerminal indexes this chain but Gateway does not reach it. */
  tradable: boolean;
  has_bins: boolean;
  reserve_usd: number | null;
  volume_24h: number | null;
  price_change_24h: number | null;
  current_price?: number | null;
  base_token_price_usd?: number | null;
  quote_token_price_usd?: number | null;
  /** Gateway CLMM only: Meteora reports `apr` as 24h fee/TVL, `apy` annualized. */
  apr?: number | null;
  apy?: number | null;
  /** True when `apr`/`apy` were derived from volume x fee tier, not reported. */
  apr_estimated?: boolean;
  fees_24h?: number | null;
  bin_step?: number | null;
  base_fee_percentage?: number | null;
  pool_created_at?: string | null;
}

/** One CLMM bin: the liquidity sitting at one price step of a pool. */
export interface LiquidityBin {
  price: number;
  base_amount: number;
  quote_amount: number;
  /** Null when the pool's token prices are unknown; size bars by amount then. */
  liquidity_usd: number | null;
}

export interface PoolBins {
  bins: LiquidityBin[];
  active_price: number | null;
  bin_step: number | null;
  /** False is a state to render (with `reason`), never an error to surface. */
  available: boolean;
  reason: string | null;
}

export interface PoolQuery {
  source: "gecko" | "gateway" | "orca";
  /** source=gecko: the chain, as a Gateway network id or a gecko chain id. */
  network?: string;
  /** source=gecko: trending | top | new | token. */
  view?: string;
  /** source=gateway: the CLMM connector. */
  connector?: string;
  /** source=gecko+view=token: a token address. source=gateway|orca: free text. */
  query?: string;
  /** source=gecko: GeckoTerminal dex ids to keep. Empty means every venue. */
  dexes?: string[];
  limit?: number;
  /** 1-based. Every upstream pages; only orca knows whether a next page exists. */
  page?: number;
}

/**
 * How much of the shared GeckoTerminal budget is left, and whether it just ran out.
 *
 * Every DEX view reads the same per-IP budget, and every failure behind it
 * degrades to an empty list — so without this a throttled fetch and a chain with
 * no pools look identical in the table.
 */
export interface DexUpstream {
  /** The breaker is open: gecko answered 429 and Condor has stopped calling. */
  rate_limited: boolean;
  /** *This* response was short because the request was refused. Can be true while
   *  `rate_limited` is false: a spent per-minute budget refuses without tripping. */
  throttled_request: boolean;
  /** Seconds left on the cooldown, at the time the response was produced. */
  retry_in: number;
  requests_last_minute: number;
  budget: number;
  /** Monotonic counters, so a poller can spot a refusal it did not witness. */
  throttled_calls: number;
  count_429: number;
  seconds_since_429: number | null;
}

/**
 * What became of one token Condor tried to register with Gateway.
 *
 * `symbol_taken` is the one worth surfacing: another token on the list already
 * holds this ticker, so Gateway refused to save this one and its balance will
 * keep reading 0 until a human decides which token owns the symbol.
 */
export type DexTokenVerdict = "listed" | "added" | "symbol_taken" | "failed";

/** The token that already holds a ticker a new token wanted. */
export interface DexTokenConflict {
  symbol: string;
  address: string;
  name?: string | null;
}

/** A page of pools. `has_more`, not a count — no upstream reports a total. */
export interface PoolPage {
  pools: PoolSummary[];
  has_more: boolean;
  /** Absent on the Gateway/Orca tabs, which do not read GeckoTerminal. */
  upstream?: DexUpstream;
}

/** One venue GeckoTerminal indexes on a chain, for the browser's dex filter. */
export interface DexVenue {
  id: string;
  name: string;
}

/** A chain the pool browser can browse, as Gateway names it. */
export interface DexChain {
  network_id: string;
  chain: string;
  label: string;
}

export interface DexPoolInfo {
  pool_address: string | null;
  dex_id: string | null;
  lp_provider: string | null;
  lp_supported: boolean;
  current_price: number | null;
  base_symbol: string | null;
  quote_symbol: string | null;
}

export const dexApi = {
  getDexPool: (server: string, connector: string, pair: string) =>
    apiFetch<DexPoolInfo>(
      `/api/v1/servers/${encodeURIComponent(server)}/market/dex-pool?connector=${encodeURIComponent(connector)}&trading_pair=${encodeURIComponent(pair)}`,
    ),

  /** Pools to browse, from GeckoTerminal (by chain) or Gateway CLMM (by connector). */
  getDexPools: (server: string, q: PoolQuery): Promise<PoolPage> => {
    const params = new URLSearchParams({ source: q.source });
    if (q.network) params.set("network", q.network);
    if (q.view) params.set("view", q.view);
    if (q.connector) params.set("connector", q.connector);
    if (q.query) params.set("query", q.query);
    if (q.dexes?.length) params.set("dexes", q.dexes.join(","));
    params.set("limit", String(q.limit ?? 20));
    params.set("page", String(q.page ?? 1));
    return apiFetch<{
      pools: PoolSummary[];
      has_more?: boolean;
      upstream?: DexUpstream;
    }>(
      `/api/v1/servers/${encodeURIComponent(server)}/dex/pools?${params}`,
    ).then((r) => ({
      pools: r.pools ?? [],
      has_more: !!r.has_more,
      upstream: r.upstream,
    }));
  },

  /**
   * The chains the pool browser can offer: the intersection of what this Gateway
   * is configured for and what GeckoTerminal indexes. Asked of the server rather
   * than hardcoded, so a chain the operator adds to Gateway shows up on its own.
   */
  getDexChains: (server: string) =>
    apiFetch<{ chains: DexChain[] }>(
      `/api/v1/servers/${encodeURIComponent(server)}/dex/chains`,
    ).then((r) => r.chains ?? []),

  /** The venues a chain has, so the dex filter offers what exists, not a guess. */
  getDexVenues: (server: string, network: string) =>
    apiFetch<{ dexes: DexVenue[] }>(
      `/api/v1/servers/${encodeURIComponent(server)}/dex/dexes?network=${encodeURIComponent(network)}`,
    ).then((r) => r.dexes ?? []),

  /**
   * Several pools by address, for the favourites view. A favourite is a saved
   * address, so its TVL and volume are re-read rather than replayed from
   * whenever it was starred.
   */
  getDexPoolsByAddress: (
    server: string,
    network: string,
    addresses: string[],
  ): Promise<PoolPage> =>
    apiFetch<{ pools: PoolSummary[]; upstream?: DexUpstream }>(
      `/api/v1/servers/${encodeURIComponent(server)}/dex/pools-by-address?network=${encodeURIComponent(network)}&addresses=${encodeURIComponent(addresses.join(","))}`,
    ).then((r) => ({
      pools: r.pools ?? [],
      has_more: false,
      upstream: r.upstream,
    })),

  /**
   * Whether GeckoTerminal is currently throttling Condor.
   *
   * Reads process-local counters only, so polling it while a cooldown banner
   * counts down costs nothing of the budget the banner is waiting on.
   */
  getDexUpstream: (server: string) =>
    apiFetch<DexUpstream>(
      `/api/v1/servers/${encodeURIComponent(server)}/dex/upstream`,
    ),

  /**
   * Make sure Gateway's token list holds a pool's two tokens.
   *
   * Gateway resolves a swap by mint address on its own, but not a *balance*: it
   * skips token accounts whose mint is not on the list, so an unregistered token
   * reads as 0 and the panel greys out its percentage presets. Called when a pool
   * opens rather than when an order is sent, and idempotent server-side, so
   * re-opening the same pool costs nothing.
   */
  ensureDexTokens: (server: string, network: string, addresses: string[]) =>
    apiFetch<{ tokens: Record<string, DexTokenVerdict> }>(
      `/api/v1/servers/${encodeURIComponent(server)}/dex/tokens`,
      { method: "POST", body: JSON.stringify({ network, addresses }) },
    ),

  /**
   * Register one token because the user asked to, where the automatic ensure
   * only reported. Retries a `failed` verdict outright; on `symbol_taken` the
   * answer names the ticker's current holder, and a second call with
   * `replace: true` deletes that holder and registers this token in its place.
   */
  addDexToken: (
    server: string,
    network: string,
    address: string,
    symbol?: string,
    replace?: boolean,
  ) =>
    apiFetch<{ verdict: DexTokenVerdict; conflict: DexTokenConflict | null }>(
      `/api/v1/servers/${encodeURIComponent(server)}/dex/tokens/add`,
      {
        method: "POST",
        body: JSON.stringify({ network, address, symbol, replace }),
      },
    ),

  /** One pool by address, so /dex/{network}/{address} renders from the URL alone. */
  getDexPoolByAddress: (server: string, address: string, network: string) =>
    apiFetch<PoolSummary>(
      `/api/v1/servers/${encodeURIComponent(server)}/dex/pools/${encodeURIComponent(address)}?network=${encodeURIComponent(network)}`,
    ),

  /**
   * A pool's liquidity bins, for the depth column beside its chart. Answers
   * `available: false` with a reason rather than failing: a venue Condor cannot
   * read bins from is something to say, not an error to raise.
   */
  getPoolBins: (
    server: string,
    address: string,
    network: string,
    connector: string,
  ) =>
    apiFetch<PoolBins>(
      `/api/v1/servers/${encodeURIComponent(server)}/dex/pools/${encodeURIComponent(address)}/bins?network=${encodeURIComponent(network)}&connector=${encodeURIComponent(connector)}`,
    ),
};
