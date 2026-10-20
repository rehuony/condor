/** @vitest-environment jsdom */
import { act, StrictMode, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CandleData } from "@/lib/api/market";
import { candleChannelKey, candleStore } from "@/lib/candle-store";
import { intervalSeconds } from "@/lib/candle-history";
import { useCandleHistory, type CandleHistoryOptions } from "@/hooks/useCandleHistory";
import { useCandleStore } from "@/hooks/useCandleStore";

const getCandles = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  errorStatus: (error: { status?: number }) => error?.status,
}));
vi.mock("@/lib/api/market", () => ({
  marketApi: {
    getCandles,
  },
}));

const NOW = Date.parse("2026-10-16T16:20:00Z");
const defaults: CandleHistoryOptions = {
  server: "local", connector: "binance_perpetual", pair: "SOL-USDT", interval: "5m", lookbackSeconds: 30 * 86400,
};
const key = candleChannelKey("local", "binance_perpetual", "SOL-USDT", "5m");
const candle = (timestamp: number): CandleData => ({ timestamp, open: 100, high: 101, low: 99, close: 100, volume: 1 });
let seen: ReturnType<typeof useCandleHistory>;
let root: Root;
let container: HTMLDivElement;

function HistoryOnlyHarness(props: CandleHistoryOptions) {
  const history = useCandleHistory(props);
  useEffect(() => { seen = history; });
  return null;
}

function Harness(props: CandleHistoryOptions) {
  useCandleStore(props.server, props.connector, props.pair, props.interval, props.poolAddress);
  const history = useCandleHistory(props);
  useEffect(() => { seen = history; });
  return null;
}

async function render(props: Partial<CandleHistoryOptions> = {}, strict = false) {
  await act(async () => {
    const harness = <Harness {...defaults} {...props} />;
    root.render(strict ? <StrictMode>{harness}</StrictMode> : harness);
  });
}

function rowsForRequest(_server: string, _connector: string, _pair: string, interval: string, _limit: number, from: number, to: number) {
  const rows: CandleData[] = [];
  const step = intervalSeconds(interval);
  for (let timestamp = Math.ceil(from / step) * step; timestamp <= to; timestamp += step) rows.push(candle(timestamp));
  return Promise.resolve(rows);
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  candleStore.collections.clear();
  candleStore.subscriptions.clear();
  candleStore.listeners.clear();
  candleStore.lastUpdateTime.clear();
  getCandles.mockReset().mockImplementation(rowsForRequest);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe("useCandleHistory", () => {
  it.each(["5m", "1m"])("loads all 30 days of %s history in serial bounded pages", async (interval) => {
    await render({ interval });
    const channel = candleChannelKey("local", "binance_perpetual", "SOL-USDT", interval);
    const step = intervalSeconds(interval);
    expect(candleStore.getCandles(channel)).toHaveLength(30 * 86400 / step + 1);
    expect(seen.status).toBe("ready");
    expect(seen.initialComplete).toBe(true);
    expect(seen.loadedFrom).toBe(NOW / 1000 - 30 * 86400);
    const requests = getCandles.mock.calls;
    for (let i = 0; i < requests.length; i++) {
      expect(requests[i][6] - requests[i][5]).toBeLessThanOrEqual(1000 * step);
      if (i) expect(requests[i][6]).toBe(requests[i - 1][5] - 1);
    }
  });

  it("finishes a finite walk even when no chart subscriber retains history", async () => {
    await act(async () => root.render(<HistoryOnlyHarness {...defaults} />));
    expect(getCandles).toHaveBeenCalledTimes(9);
    expect(seen.initialComplete).toBe(true);
    expect(candleStore.getCandles(key)).toHaveLength(2000);
  });

  it("reuses successful coverage when the same preset is explicitly selected again", async () => {
    await render();
    const before = getCandles.mock.calls.length;
    await render({ rangeRequestId: 1 });
    expect(getCandles).toHaveBeenCalledTimes(before);
    expect(seen.initialComplete).toBe(true);
  });

  it("loads one additional page per action and ignores actions during that request", async () => {
    await render({ lookbackSeconds: 3600 });
    const firstFrom = seen.loadedFrom!;
    let finish!: (rows: CandleData[]) => void;
    getCandles.mockImplementationOnce(() => new Promise<CandleData[]>((resolve) => { finish = resolve; }));
    await act(async () => { seen.loadEarlier(); seen.loadEarlier(); });
    expect(getCandles).toHaveBeenCalledTimes(2);
    const page = getCandles.mock.calls[1];
    await act(async () => finish([candle(page[5])]));
    expect(seen.loadedFrom).toBe(page[5]);
    expect(seen.loadedFrom).toBeLessThan(firstFrom);
    expect(seen.initialComplete).toBe(true);
    expect(candleStore.getCandles(key).at(-1)?.timestamp).toBe(NOW / 1000);
  });

  it("continues across empty initial pages and allows another action after an empty older page", async () => {
    getCandles.mockResolvedValue([]);
    await render();
    expect(getCandles).toHaveBeenCalledTimes(9);
    expect(seen.status).toBe("empty");
    expect(seen.initialComplete).toBe(true);
    await act(async () => seen.loadEarlier());
    expect(getCandles).toHaveBeenCalledTimes(10);
    const emptyFrom = seen.loadedFrom;
    await act(async () => seen.loadEarlier());
    expect(getCandles).toHaveBeenCalledTimes(11);
    expect(seen.loadedFrom).toBeLessThan(emptyFrom!);
  });

  it("advances by requested boundaries when every provider page is short", async () => {
    getCandles.mockImplementation(async (...args) => [candle(args[5])]);
    await render();
    expect(getCandles).toHaveBeenCalledTimes(9);
    expect(candleStore.getCandles(key)).toHaveLength(9);
    expect(seen.initialComplete).toBe(true);
    expect(seen.loadedFrom).toBe(NOW / 1000 - 30 * 86400);
  });

  it("does not cache out-of-window fallback rows as older candles", async () => {
    getCandles.mockResolvedValue([candle(NOW / 1000 + 300)]);
    await render({ lookbackSeconds: 3600 });
    expect(seen.status).toBe("empty");
    expect(candleStore.getCandles(key)).toEqual([]);
  });

  it("honors Retry-After and keeps retrying a rate limited page", async () => {
    getCandles.mockRejectedValueOnce(Object.assign(new Error("Limited"), { status: 429, retryAfter: 3 }));
    await render({ lookbackSeconds: 3600 });
    expect(seen.status).toBe("waiting");
    expect(seen.retryAt).toBe(NOW + 3000);
    await act(async () => vi.advanceTimersByTimeAsync(2999));
    expect(getCandles).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(getCandles).toHaveBeenCalledTimes(2);
    expect(seen.status).toBe("ready");
  });

  it("defaults an absent Retry-After to 60 seconds and cancels that timer on range change", async () => {
    getCandles.mockRejectedValueOnce(Object.assign(new Error("Limited"), { status: 429 }));
    await render({ lookbackSeconds: 3600 });
    expect(seen.retryAt).toBe(NOW + 60_000);
    const firstSignal = getCandles.mock.calls[0][8] as AbortSignal;
    await render({ lookbackSeconds: 3600, rangeRequestId: 1 });
    expect(firstSignal.aborted).toBe(true);
    expect(seen.status).toBe("ready");
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(getCandles).toHaveBeenCalledTimes(2);
  });

  it("retries failures twice, then exposes retry for the uncompleted page", async () => {
    getCandles.mockRejectedValue(new Error("Network unavailable"));
    await render({ lookbackSeconds: 3600 });
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    await act(async () => vi.advanceTimersByTimeAsync(4000));
    expect(getCandles).toHaveBeenCalledTimes(3);
    expect(seen.status).toBe("error");
    expect(seen.initialComplete).toBe(false);
    const page = getCandles.mock.calls[0].slice(5, 7);
    getCandles.mockImplementation(rowsForRequest);
    await act(async () => seen.retry());
    expect(getCandles.mock.calls[3].slice(5, 7)).toEqual(page);
    expect(seen.status).toBe("ready");
  });

  it("keeps completed pages when a later initial page fails, then retries only the missing window", async () => {
    getCandles.mockImplementationOnce(rowsForRequest).mockRejectedValue(new Error("Offline"));
    await render();
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    await act(async () => vi.advanceTimersByTimeAsync(4000));
    expect(seen.status).toBe("error");
    expect(candleStore.getCandles(key)).toHaveLength(1000);
    const firstPage = getCandles.mock.calls[0].slice(5, 7);
    const failedPage = getCandles.mock.calls[1].slice(5, 7);
    getCandles.mockImplementation(rowsForRequest);
    await act(async () => seen.retry());
    expect(getCandles.mock.calls[4].slice(5, 7)).toEqual(failedPage);
    expect(getCandles.mock.calls.slice(4).some((call) => call[5] === firstPage[0])).toBe(false);
    expect(candleStore.getCandles(key)).toHaveLength(8641);
    expect(seen.initialComplete).toBe(true);
  });

  it("preserves the loaded window during a failed older page and retries that page", async () => {
    await render({ lookbackSeconds: 3600 });
    const loadedFrom = seen.loadedFrom;
    getCandles.mockRejectedValue(new Error("Offline"));
    await act(async () => seen.loadEarlier());
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    await act(async () => vi.advanceTimersByTimeAsync(4000));
    expect(seen.status).toBe("error");
    expect(seen.initialComplete).toBe(true);
    expect(seen.loadedFrom).toBe(loadedFrom);
    expect(candleStore.getCandles(key)).toHaveLength(13);
    const failedPage = getCandles.mock.calls[1].slice(5, 7);
    getCandles.mockImplementation(rowsForRequest);
    await act(async () => seen.retry());
    expect(getCandles.mock.calls[4].slice(5, 7)).toEqual(failedPage);
    expect(seen.status).toBe("ready");
    expect(seen.loadedFrom).toBe(failedPage[0]);
  });

  it("aborts obsolete market requests and ignores a completion that does not honor abort", async () => {
    let finish!: (rows: CandleData[]) => void;
    getCandles.mockImplementationOnce(() => new Promise<CandleData[]>((resolve) => { finish = resolve; }));
    await render({ lookbackSeconds: 3600 });
    const signal = getCandles.mock.calls[0][8] as AbortSignal;
    await render({ lookbackSeconds: 3600, pair: "BTC-USDT" });
    expect(signal.aborted).toBe(true);
    await act(async () => finish([candle(NOW / 1000)]));
    expect(candleStore.getCandles(key)).toEqual([]);
    expect(seen.status).toBe("ready");
    expect(getCandles).toHaveBeenCalledTimes(2);
  });

  it("restarts the cancelled StrictMode setup instead of suppressing its real request", async () => {
    await render({ lookbackSeconds: 3600 }, true);
    expect(getCandles).toHaveBeenCalledTimes(2);
    expect((getCandles.mock.calls[0][8] as AbortSignal).aborted).toBe(true);
    expect(seen.status).toBe("ready");
    expect(candleStore.getCandles(key)).toHaveLength(13);
  });

  it("preserves a pinned DEX pool in every page", async () => {
    await render({ connector: "solana/raydium", poolAddress: "pool-address" });
    expect(getCandles).toHaveBeenCalledTimes(9);
    expect(getCandles.mock.calls.every((call) => call[7] === "pool-address")).toBe(true);
  });
});
