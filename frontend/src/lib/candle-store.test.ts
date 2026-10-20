import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CandleData } from "@/lib/api";
import { candleChannelKey, CandleStore } from "@/lib/candle-store";
import { historyPage, intervalSeconds, missingHistoryRange, mergeHistoryRanges } from "@/lib/candle-history";
import type { CondorWebSocket } from "@/lib/websocket";

const key = candleChannelKey("local", "binance_perpetual", "SOL-USDT", "5m");
const end = Date.parse("2026-10-16T16:20:00Z") / 1000;
const candle = (timestamp: number): CandleData => ({ timestamp, open: 100, high: 101, low: 99, close: 100, volume: 1 });

let store: CandleStore;
beforeEach(() => {
  vi.useFakeTimers();
  store = new CandleStore();
});
afterEach(() => vi.useRealTimers());

describe("candle history retention", () => {
  it.each(["5m", "1m"])("keeps a complete 30-day %s window across updates", (interval) => {
    const channel = candleChannelKey("local", "binance_perpetual", "SOL-USDT", interval);
    const step = intervalSeconds(interval);
    const from = end - 30 * 86400;
    const rows = Array.from({ length: (end - from) / step + 1 }, (_, i) => candle(from + i * step));
    store.subscribe(channel, true);
    store.mergeHistory(channel, rows, { from, to: end });
    store.mergeCandles(channel, [candle(end + step)]);
    expect(store.getCandles(channel)).toHaveLength(rows.length + 1);
    expect(store.getCandles(channel)[0].timestamp).toBe(from);
    expect(store.missingHistory(channel, { from, to: end })).toBeNull();
  });

  it("retains shared history until the last chart leaves, then trims it while a ticker remains", () => {
    store.subscribe(key);
    store.subscribe(key, true);
    store.subscribe(key, true);
    const rows = Array.from({ length: 4000 }, (_, i) => candle(end - i * 300));
    store.mergeHistory(key, rows, { from: end - 3999 * 300, to: end });
    store.unsubscribe(key, true);
    expect(store.getCandles(key)).toHaveLength(4000);
    store.unsubscribe(key, true);
    expect(store.getCandles(key)).toHaveLength(2000);
    expect(store.getCandles(key)[0].timestamp).toBe(end - 1999 * 300);
    expect(store.missingHistory(key, { from: end - 3999 * 300, to: end })).not.toBeNull();
    expect(store.subscriptions.get(key)?.refCount).toBe(1);
    store.mergeCandles(key, [candle(end + 300)]);
    expect(store.getCandles(key)).toHaveLength(2000);
  });

  it("records successful empty windows without claiming older history is exhausted", () => {
    store.subscribe(key, true);
    store.mergeHistory(key, [], { from: 1000, to: 1999 });
    expect(store.missingHistory(key, { from: 1000, to: 1999 })).toBeNull();
    expect(store.missingHistory(key, { from: 0, to: 1999 })).toEqual({ from: 0, to: 999 });
  });

  it("subscribes/rebinds only the live tail, preserving the duration for socket reconnects", () => {
    const socket = () => ({ onMessage: vi.fn(() => vi.fn()), subscribe: vi.fn(), unsubscribe: vi.fn() });
    const first = socket();
    const replacement = socket();
    store.attachWs(first as unknown as CondorWebSocket);
    store.subscribe(key, true);
    expect(first.subscribe).toHaveBeenCalledWith(key, { duration: 200 * 300 });
    store.attachWs(replacement as unknown as CondorWebSocket);
    expect(replacement.subscribe).toHaveBeenCalledWith(key, { duration: 200 * 300 });
  });

  it("keeps active histories during LRU eviction and releases idle collections", () => {
    store.subscribe(key, true);
    store.mergeHistory(key, [candle(end)], { from: end, to: end });
    for (let i = 0; i < 25; i++) {
      const channel = candleChannelKey("local", "binance", `PAIR-${i}`, "1m");
      store.mergeCandles(channel, [candle(end)]);
      store.getCandles(channel);
    }
    expect(store.getCandles(key)).toHaveLength(1);
    expect(store.collections.size).toBeLessThanOrEqual(20);
    vi.advanceTimersByTime(11 * 60_000);
    expect(store.collections.size).toBe(1);
  });
});

describe("candle history windows", () => {
  it("bounds non-aligned pages to at most 1,000 bars and 180 days", () => {
    for (const interval of ["1s", "1m", "5m", "1h", "1d", "1w"]) {
      const step = intervalSeconds(interval);
      const page = historyPage(end + 43, interval);
      const count = Math.floor(page.to / step) - Math.ceil(page.from / step) + 1;
      expect(count).toBeLessThanOrEqual(1000);
      expect(page.to - page.from).toBeLessThanOrEqual(180 * 86400);
      expect(historyPage(page.from - 1, interval).to).toBe(page.from - 1);
    }
  });

  it("merges adjacent coverage and discovers newest gaps without dropping sparse windows", () => {
    const ranges = mergeHistoryRanges([{ from: 100, to: 199 }, { from: 300, to: 399 }], { from: 200, to: 249 });
    expect(ranges).toEqual([{ from: 100, to: 249 }, { from: 300, to: 399 }]);
    expect(missingHistoryRange(ranges, { from: 150, to: 450 })).toEqual({ from: 400, to: 450 });
    expect(missingHistoryRange(ranges, { from: 150, to: 399 })).toEqual({ from: 250, to: 299 });
    expect(missingHistoryRange(ranges, { from: 150, to: 200 })).toBeNull();
  });
});
