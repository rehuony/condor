/**
 * The REST candle backfill survives StrictMode's double mount (CORR-289).
 *
 * The effect cancels its in-flight fetch on cleanup, so a mount/unmount/mount —
 * exactly what StrictMode does in development — throws the first fetch away and
 * must issue a second one. A ref remembering the last key defeated that: the
 * second pass matched the ref the first pass wrote and returned early, so the
 * chart merged nothing and every dev chart drew only what the WS could offer.
 *
 * The production contract is the other half: one backfill per distinct set of
 * the effect's six dependencies, and no refetch when the props do not change.
 *
 * The same double mount fires the chart-init effect twice in one tick, so
 * `import("lightweight-charts")` is requested twice concurrently — and a
 * per-file `vi.mock` answered the stub to the first request and raw-imported
 * the real library for the second. It is the concurrency, not StrictMode: two
 * plain mounts in one `act()` split the same way, and neither a static nor an
 * awaited warm-up import of the module changes it. The real `ChartWidget` then
 * builds against a jsdom that has no 2D canvas and schedules a draw frame,
 * which fired after teardown, outside every test, and exited the whole suite 1
 * with "2 unhandled errors" while every test reported green (CORR-360).
 *
 * The library is now substituted at the resolver, so both requests get the same
 * double and no real widget can be built at all (CORR-368). This file keeps its
 * own frame queue and 2D context anyway: they are what proves the StrictMode
 * mount leaves nothing behind, and they cost nothing.
 *
 * @vitest-environment jsdom
 */

import { act, StrictMode, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { chartDouble, createChart } from "@/tests/support/lightweight-charts-double";
import { TradeChart } from "@/components/trade/TradeChart";
import { candleChannelKey, candleStore } from "@/lib/candle-store";
import { intervalSeconds } from "@/lib/candle-history";
import type { CandleData } from "@/lib/api/market";

const CANDLES = [
  { timestamp: 1_700_000_000, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 },
  { timestamp: 1_700_000_060, open: 1.5, high: 2.5, low: 1, close: 2, volume: 12 },
];

const apiState = vi.hoisted(() => ({
  getCandles: vi.fn(),
}));

vi.mock("@/hooks/useRates", () => ({
  useRates: () => ({
    formatPnlValue: (v: number) => String(v),
    formatValue: (v: number) => String(v),
  }),
}));

vi.mock("@/lib/api", () => ({
  errorStatus: (error: { status?: number }) => error?.status,
}));
vi.mock("@/lib/api/market", () => ({
  marketApi: {
    getCandles: (...args: unknown[]) => apiState.getCandles(...args),
  },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

/** The double's factory, read back to prove the component reached the chart. */
const createChartStub = createChart as unknown as Mock;

/** Frames the render scheduled, so teardown can prove none outlive the tree. */
const pendingFrames = new Map<number, FrameRequestCallback>();

/**
 * jsdom ships no canvas backend, and asking it for one only prints
 * "Not implemented: HTMLCanvasElement's getContext()" and answers null — which a
 * real chart widget dereferences. The calls just have to answer; this window
 * belongs to this file alone, so it is patched once and never restored.
 */
HTMLCanvasElement.prototype.getContext = (() =>
  new Proxy({} as Record<string, unknown>, {
    get(target, prop) {
      if (typeof prop !== "string") return undefined;
      if (!(prop in target)) target[prop] = vi.fn(() => ({ width: 0 }));
      return target[prop];
    },
  })) as unknown as typeof HTMLCanvasElement.prototype.getContext;

let container: HTMLDivElement;
let root: Root;

const PROPS: ComponentProps<typeof TradeChart> = {
  server: "local",
  connector: "binance",
  pair: "BTC-USDT",
  interval: "1m",
  lookbackSeconds: 3600,
  startPrice: 100,
  endPrice: 200,
  limitPrice: 150,
  side: 1,
  minSpread: 0.001,
  activePickField: null,
  onPriceSet: vi.fn(),
};

/** Render the chart, optionally wrapped the way `main.tsx` wraps the app. */
async function render(strict: boolean, extra: Partial<ComponentProps<typeof TradeChart>> = {}) {
  const tree = <TradeChart {...PROPS} {...extra} />;
  await act(async () => {
    root.render(strict ? <StrictMode>{tree}</StrictMode> : tree);
  });
  // The fetch resolves a microtask after the effect ran; let its `.then` land.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  // The chart's own dynamic import settles a few hops further out, so give the
  // whole chain a macrotask: the chart is then built and read inside the test
  // rather than somewhere after it.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  // Both paths ask for the chart library — the StrictMode one twice at once —
  // and the resolver hands the double to every request, so the component got a
  // chart and jsdom got no canvas. The module identity is a result here, not an
  // assumption, and it holds on the concurrent path too.
  expect(createChartStub).toHaveBeenCalled();
  expect(container.querySelector("canvas")).toBeNull();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  // Own the frame queue instead of letting jsdom fire it off a timer: a frame
  // that runs after its test is an exception belonging to no test at all.
  let nextFrame = 1;
  pendingFrames.clear();
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    const id = nextFrame++;
    pendingFrames.set(id, cb);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    pendingFrames.delete(id);
  });
  // Discards the charts the last test built and reinstates the price scale.
  chartDouble.reset({ toPrice: () => 100 });
  for (const sub of candleStore.subscriptions.values()) {
    if (sub.teardownTimer !== null) clearTimeout(sub.teardownTimer);
  }
  candleStore.collections.clear();
  candleStore.subscriptions.clear();
  candleStore.listeners.clear();
  candleStore.lastUpdateTime.clear();
  vi.spyOn(Date, "now").mockReturnValue(CANDLES[1].timestamp * 1000);
  vi.spyOn(candleStore, "mergeHistory");
  apiState.getCandles.mockReset();
  apiState.getCandles.mockResolvedValue(CANDLES);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  // Unmounting destroys the chart, which cancels its own frame, so the queue has
  // to be empty. Drain it either way, then report what was still queued.
  const queued = [...pendingFrames.keys()];
  pendingFrames.clear();
  expect(queued).toEqual([]);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("TradeChart REST backfill", () => {
  it("merges the fetched candles when StrictMode mounts the effect twice", async () => {
    await render(true);

    expect(apiState.getCandles).toHaveBeenCalled();
    expect(candleStore.mergeHistory).toHaveBeenCalledWith(expect.any(String), CANDLES, expect.any(Object));
  });

  it("merges the fetched candles on a single mount", async () => {
    await render(false);

    expect(apiState.getCandles).toHaveBeenCalledTimes(1);
    expect(candleStore.mergeHistory).toHaveBeenCalledWith(expect.any(String), CANDLES, expect.any(Object));
  });

  it("does not refetch while the six dependencies stay the same", async () => {
    await render(false);
    expect(apiState.getCandles).toHaveBeenCalledTimes(1);

    await render(false, { limitPrice: 151 });

    expect(apiState.getCandles).toHaveBeenCalledTimes(1);
  });

  it("refetches when a dependency changes", async () => {
    await render(false);
    await render(false, { interval: "5m" });

    expect(apiState.getCandles).toHaveBeenCalledTimes(2);
    expect(apiState.getCandles).toHaveBeenLastCalledWith(
      "local",
      "binance",
      "BTC-USDT",
      "5m",
      1000,
      expect.any(Number),
      expect.any(Number),
      undefined,
      expect.any(AbortSignal),
    );
  });
});

const HISTORY_NOW = Date.parse("2026-10-16T16:20:00Z") / 1000;
const candle = (timestamp: number, close = 100): CandleData => ({
  timestamp, open: 100, high: Math.max(101, close), low: 99, close, volume: 1,
});

function rowsForWindow(interval: string, from: number, to: number) {
  const rows: CandleData[] = [];
  const step = intervalSeconds(interval);
  for (let timestamp = Math.ceil(from / step) * step; timestamp <= to; timestamp += step) {
    rows.push(candle(timestamp));
  }
  return rows;
}

function deferPages() {
  const pages: {
    from: number;
    to: number;
    interval: string;
    signal: AbortSignal;
    resolve: (rows: CandleData[]) => void;
  }[] = [];
  apiState.getCandles.mockImplementation((
    _server: string, _connector: string, _pair: string, interval: string,
    _limit: number, from: number, to: number, _pool: string | undefined, signal: AbortSignal,
  ) => new Promise<CandleData[]>((resolve) => {
    pages.push({ from, to, interval, signal, resolve });
  }));
  return pages;
}

/** Advance the chart's scheduled reveal without running periodic store timers. */
async function flushFrames() {
  const callbacks = [...pendingFrames.values()];
  pendingFrames.clear();
  await act(async () => {
    for (const callback of callbacks) callback(performance.now());
  });
}

function chartPane() {
  return chartDouble.charts.at(-1)!.container as HTMLElement;
}

function candleDraws() {
  return (chartDouble.series!.setData as Mock).mock.calls
    .map(([rows]) => rows as { time: number; close: number }[])
    .filter((rows) => rows.length > 0);
}

function expectInitialPlaceholder() {
  expect(getComputedStyle(chartPane()).visibility).toBe("hidden");
  expect(candleDraws()).toEqual([]);
  expect(chartDouble.series!.update).not.toHaveBeenCalled();
  expect(chartDouble.timeScale!.fitContent).not.toHaveBeenCalled();
}

describe("TradeChart initial presentation", () => {
  beforeEach(() => {
    vi.mocked(Date.now).mockReturnValue(HISTORY_NOW * 1000);
  });

  it.each([
    { interval: "5m", days: 3, expectedPages: 1 },
    { interval: "1m", days: 30, expectedPages: 44 },
  ])("reveals the complete $days day $interval window without a recent-candle preview", async ({ interval, days, expectedPages }) => {
    const pages = deferPages();
    const lookbackSeconds = days * 86400;
    const channel = candleChannelKey("local", "binance", "BTC-USDT", interval);
    await render(false, { interval, lookbackSeconds });

    // This is the same store notification path the WebSocket candle message uses.
    await act(async () => {
      candleStore.mergeCandles(channel, [candle(HISTORY_NOW, 123)]);
    });
    expectInitialPlaceholder();
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();

    for (let index = 0; index < expectedPages; index++) {
      expect(pages).toHaveLength(index + 1);
      const page = pages[index];
      await act(async () => page.resolve(rowsForWindow(interval, page.from, page.to)));
      if (index < expectedPages - 1) expectInitialPlaceholder();
    }

    // fitContent is applied by the chart's paint frame; data alone must not
    // reveal the pane before that initial viewport is ready.
    expect(getComputedStyle(chartPane()).visibility).toBe("hidden");
    expect(candleDraws()).toHaveLength(1);
    await flushFrames();
    expect(pages).toHaveLength(expectedPages);
    expect(getComputedStyle(chartPane()).visibility).toBe("visible");
    expect(container.querySelector('[aria-busy="true"]')).toBeNull();
    const draws = candleDraws();
    expect(draws).toHaveLength(1);
    expect(draws[0]).toHaveLength(lookbackSeconds / intervalSeconds(interval) + 1);
    expect(draws[0][0].time).toBe(HISTORY_NOW - lookbackSeconds);
    expect(draws[0].at(-1)?.time).toBe(HISTORY_NOW);
    expect(chartDouble.timeScale!.fitContent).toHaveBeenCalledTimes(1);

    await act(async () => candleStore.mergeCandles(channel, [candle(HISTORY_NOW, 124)]));
    expect(chartDouble.series!.update).toHaveBeenLastCalledWith(expect.objectContaining({ close: 124 }));
    expect(chartDouble.timeScale!.fitContent).toHaveBeenCalledTimes(1);
  });

  it("shows a fully cached window immediately without fetching or a second fit", async () => {
    const channel = candleChannelKey("local", "binance", "BTC-USDT", "5m");
    const range = { from: HISTORY_NOW - 3 * 86400, to: HISTORY_NOW };
    candleStore.subscribe(channel, true);
    candleStore.mergeHistory(channel, rowsForWindow("5m", range.from, range.to), range);
    try {
      await render(false, { interval: "5m", lookbackSeconds: 3 * 86400 });
      await flushFrames();

      expect(apiState.getCandles).not.toHaveBeenCalled();
      expect(getComputedStyle(chartPane()).visibility).toBe("visible");
      expect(candleDraws()).toHaveLength(1);
      expect(candleDraws()[0]).toHaveLength(865);
      expect(chartDouble.timeScale!.fitContent).toHaveBeenCalledTimes(1);
    } finally {
      candleStore.unsubscribe(channel, true);
    }
  });

  it("keeps partial history hidden through a failed page and reveals it after Retry", async () => {
    const pages = deferPages();
    await render(false, { interval: "5m", lookbackSeconds: 30 * 86400 });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    apiState.getCandles.mockRejectedValue(new Error("Offline"));
    const first = pages[0];
    await act(async () => first.resolve(rowsForWindow("5m", first.from, first.to)));
    await act(async () => vi.advanceTimersByTimeAsync(6000));

    expectInitialPlaceholder();
    const retry = [...container.querySelectorAll("button")].find((button) => button.textContent === "Retry");
    expect(retry).toBeDefined();
    const attemptsBeforeRetry = apiState.getCandles.mock.calls.length;
    apiState.getCandles.mockImplementation((
      _server: string, _connector: string, _pair: string, interval: string,
      _limit: number, from: number, to: number,
    ) => Promise.resolve(rowsForWindow(interval, from, to)));
    await act(async () => retry!.click());
    await flushFrames();

    expect(getComputedStyle(chartPane()).visibility).toBe("visible");
    expect(candleDraws()).toHaveLength(1);
    expect(candleDraws()[0]).toHaveLength(8641);
    expect(chartDouble.timeScale!.fitContent).toHaveBeenCalledTimes(1);
    expect(apiState.getCandles.mock.calls.slice(attemptsBeforeRetry).every((call) => call[6] < first.from)).toBe(true);
  });

  it("keeps the first frame hidden while rate limited, then reveals the complete window", async () => {
    const pages = deferPages();
    await render(false, { interval: "5m", lookbackSeconds: 30 * 86400 });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    apiState.getCandles.mockRejectedValueOnce(Object.assign(new Error("Limited"), { status: 429, retryAfter: 3 }));
    apiState.getCandles.mockImplementation((
      _server: string, _connector: string, _pair: string, interval: string,
      _limit: number, from: number, to: number,
    ) => Promise.resolve(rowsForWindow(interval, from, to)));
    const first = pages[0];
    await act(async () => first.resolve(rowsForWindow("5m", first.from, first.to)));

    expectInitialPlaceholder();
    expect(container.textContent).toContain("Rate limited");
    await act(async () => vi.advanceTimersByTimeAsync(2999));
    expectInitialPlaceholder();
    await act(async () => vi.advanceTimersByTimeAsync(1));
    await flushFrames();

    expect(getComputedStyle(chartPane()).visibility).toBe("visible");
    expect(candleDraws()[0]).toHaveLength(8641);
    expect(chartDouble.timeScale!.fitContent).toHaveBeenCalledTimes(1);
  });

  it("ignores an obsolete StrictMode request and draws only the active market", async () => {
    const pages = deferPages();
    await render(true, { interval: "5m", lookbackSeconds: 3 * 86400 });
    expect(pages).toHaveLength(2);
    expect(pages[0].signal.aborted).toBe(true);
    await render(true, { pair: "SOL-USDT", interval: "5m", lookbackSeconds: 3 * 86400 });
    expect(pages).toHaveLength(3);
    expect(pages[1].signal.aborted).toBe(true);
    await act(async () => {
      candleStore.mergeCandles(candleChannelKey("local", "binance", "BTC-USDT", "5m"), [candle(HISTORY_NOW, 999)]);
      for (const page of pages.slice(0, 2)) page.resolve(rowsForWindow("5m", page.from, page.to));
    });
    expectInitialPlaceholder();

    const active = pages[2];
    await act(async () => active.resolve(rowsForWindow("5m", active.from, active.to)));
    await flushFrames();
    expect(getComputedStyle(chartPane()).visibility).toBe("visible");
    expect(candleDraws()).toHaveLength(1);
    expect(candleDraws()[0]).toHaveLength(865);
    expect(candleDraws()[0].every((row) => row.close === 100)).toBe(true);
    expect(chartDouble.timeScale!.fitContent).toHaveBeenCalledTimes(1);
  });
});
