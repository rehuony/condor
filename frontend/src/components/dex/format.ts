import { num } from "@/lib/dex/pools";


/**
 * Compact dollars with a billions tier for pool TVL and volume figures.
 * Only a *missing* figure renders as an em-dash; a genuine `$0` renders as
 * `$0.00` — zero TVL is a fact about the pool, not absent data.
 */
export function usdCompact(v: unknown): string {
  const n = num(v);
  if (n === null) return "—";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${n.toFixed(2)}`;
}

/**
 * Percent with a K% cap: a 25000% memecoin move renders as "25K%" instead of
 * stretching the column with "25000.00%". Em-dash for a missing figure.
 */
export function pct(v: unknown): string {
  const n = num(v);
  if (n === null) return "—";
  if (Math.abs(n) >= 10_000) return `${(n / 1000).toFixed(0)}K%`;
  return `${n.toFixed(2)}%`;
}
