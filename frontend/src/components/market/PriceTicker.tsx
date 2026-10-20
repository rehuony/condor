import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { marketApi } from "@/lib/api/market";
import { useLastClose } from "@/hooks/useCandleStore";
import { candleChannelKey } from "@/lib/candle-store";
import { formatCompactVolume } from "@/lib/formatters";
import { changeColumnLabel, changeWindowTitle, formatChange } from "@/lib/marketChange";
import { useTickers } from "@/components/market/useTickers";

interface PriceTickerProps {
  server: string;
  connector: string;
  pair: string;
  /** Candle interval to track — defaults to "1m" for most responsive updates */
  interval?: string;
  /**
   * Whether `/market/prices` answers for this connector. False for gateway DEX
   * networks, where the candle close is the only price and the REST call would
   * only 502; bid/ask/spread are then simply absent.
   */
  hasRestPrice?: boolean;
  /**
   * Whether to carry the 24h stats (change, volume) beside the price. Off by
   * default: they cost a whole-connector ticker fetch, which only the trade
   * header — where the market is the subject of the page — is worth paying.
   */
  showStats?: boolean;
}

/**
 * One reading in the header: what it is on top, what it says below.
 *
 * Every number in this strip is a column of the same shape, so the eye reads
 * the labels as one row and the values as another instead of decoding each
 * pair separately — the layout every derivatives venue converged on.
 */
function Stat({
  label,
  title,
  valueClass = "text-[var(--color-text)]",
  className = "",
  children,
}: {
  label: string;
  title?: string;
  valueClass?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={className} title={title}>
      <p className="text-[10px] leading-tight text-[var(--color-text-muted)]">{label}</p>
      <p className={`text-xs font-semibold tabular-nums leading-tight ${valueClass}`}>{children}</p>
    </div>
  );
}

export function PriceTicker({
  server,
  connector,
  pair,
  interval = "1m",
  hasRestPrice = true,
  showStats = false,
}: PriceTickerProps) {
  const candlePrice = useLastClose(
    server && connector && pair ? server : null,
    connector,
    pair,
    interval,
  );

  // REST fallback for bid/ask/spread (less frequent)
  const { data: price } = useQuery({
    queryKey: ["price", server, connector, pair],
    queryFn: () => marketApi.getPrice(server, connector, pair),
    enabled: !!server && !!connector && !!pair && hasRestPrice,
    refetchInterval: 15_000,
  });

  // 24h stats for this pair, off the connector-wide ticker snapshot the market
  // browser already caches — same query key, so opening the browser costs
  // nothing extra and this strip inherits its 60s refresh.
  const { byPair } = useTickers(server, connector, showStats && hasRestPrice);
  const ticker = showStats ? byPair.get(pair) : undefined;

  // Use candle close as primary price, fall back to REST mid_price
  const displayPrice = candlePrice != null && candlePrice > 0 ? candlePrice : (price?.mid_price ?? 0);
  const marketKey = candleChannelKey(server, connector, pair, interval);
  const [lastTick, setLastTick] = useState({
    marketKey,
    price: displayPrice,
    direction: "flat",
  });

  // Update before committing a changed quote, so a new market never inherits
  // either the previous market's close or its tick colour.
  if (lastTick.marketKey !== marketKey || !Object.is(lastTick.price, displayPrice)) {
    const direction =
      lastTick.marketKey === marketKey && displayPrice > 0 && lastTick.price > 0
        ? displayPrice > lastTick.price
          ? "up"
          : displayPrice < lastTick.price
            ? "down"
            : "flat"
        : "flat";
    setLastTick({ marketKey, price: displayPrice, direction });
  }

  if (!pair) return null;

  const spread = price?.best_ask && price?.best_bid
    ? price.best_ask - price.best_bid
    : 0;
  const mid = price ? (price.best_ask + price.best_bid) / 2 : 0;
  const spreadPct = mid > 0 ? (spread / mid) * 100 : 0;

  const dirColor =
    lastTick.direction === "up"
      ? "text-[var(--color-green)]"
      : lastTick.direction === "down"
        ? "text-[var(--color-red)]"
        : "text-[var(--color-text)]";

  const windowS = ticker?.change_window_s ?? null;
  const changeClass =
    ticker?.change_pct == null
      ? "text-[var(--color-text-muted)]"
      : ticker.change_pct >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";

  return (
    // One fixed-height row that wraps: a reading that does not fit beside the
    // ones before it drops onto a second line, which the height clips. So the
    // strip shows as many readings as its width holds, in this order.
    <div className="flex h-[2.125rem] flex-wrap items-center gap-x-5 gap-y-8 overflow-hidden whitespace-nowrap md:gap-x-6">
      {/* Mark: the one value the page is about, so it keeps the size and the
          tick colour. It still wears a label, so the row of headings above the
          numbers is unbroken. */}
      <div>
        <p className="text-[10px] leading-tight text-[var(--color-text-muted)]">Mark</p>
        <p className={`text-sm font-bold tabular-nums leading-tight md:text-base ${displayPrice > 0 ? dirColor : "text-[var(--color-text-muted)]"}`}>
          {displayPrice > 0
            ? displayPrice.toLocaleString("en-US", { maximumFractionDigits: 8 })
            : "—"}
        </p>
      </div>

      {price && price.best_bid > 0 && (
        <>
          <Stat label="Bid" valueClass="text-[var(--color-green)]">
            {price.best_bid.toLocaleString("en-US", { maximumFractionDigits: 8 })}
          </Stat>

          <Stat label="Ask" valueClass="text-[var(--color-red)]">
            {price.best_ask.toLocaleString("en-US", { maximumFractionDigits: 8 })}
          </Stat>

          <Stat label="Spread">
            {spreadPct.toFixed(3)}%
          </Stat>
        </>
      )}

      {showStats && (
        <>
          {/* The change label states its own window — the backend measures
              against its hourly snapshots, so it is only "24h" when it really
              is 24h. Same rule as the market browser's column.

              Keep the slots while data is pending or unavailable so the
              header and chart do not move when each request completes. */}
          <Stat
            label={changeColumnLabel(windowS)}
            title={changeWindowTitle(windowS)}
            valueClass={changeClass}
          >
            {formatChange(ticker?.change_pct ?? null)}
          </Stat>

          <Stat
            label="24h Volume"
            title={
              ticker?.usd_volume != null
                ? `$${ticker.usd_volume.toLocaleString("en-US", { maximumFractionDigits: 2 })} traded in 24h`
                : ticker
                  ? "The quote asset could not be priced in USD"
                  : "24h volume is not available yet"
            }
          >
            {ticker?.usd_volume != null ? formatCompactVolume(ticker.usd_volume) : "—"}
          </Stat>
        </>
      )}
    </div>
  );
}
