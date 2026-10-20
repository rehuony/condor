/**
 * The chart draws the range its picker names, not every candle the store holds.
 *
 * The candle store is shared by every chart on a channel and never trims to one
 * chart's range: once a 1-day chart has loaded, the channel holds a day of
 * candles for a 1-hour chart too. The chart drew all of them, and only ever
 * redrew when older history arrived — so picking a shorter range after a longer
 * one left the longer history on screen.
 *
 * @vitest-environment jsdom
 */

import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import type { CandleData } from "@/lib/api";
import type { ExecutorOverlay } from "@/lib/executor-overlays";
import { chartDouble } from "@/test/lightweight-charts-double";
import { TradeChart } from "./TradeChart";

const HOUR = 3600;
const DAY = 24 * HOUR;
const NEWEST = 1_700_086_400;

/** `count` 1m candles ending at `newest`, oldest first — the store's order. */
function minutes(count: number, newest = NEWEST): CandleData[] {
  return Array.from({ length: count }, (_, i) => ({
    timestamp: newest - (count - 1 - i) * 60,
    open: 1,
    high: 2,
    low: 0.5,
    close: 1.5,
    volume: 1,
  }));
}

/** What a 1-day chart leaves in the shared store. */
const A_DAY = minutes(1440);
const EXECUTOR: ExecutorOverlay = {
  executorId: "selected", type: "position", side: "buy", status: "completed", closeType: "take_profit",
  pnl: 10, pnlPct: 0.01, volume: 100, fees: 0, priceLines: [], markers: [],
  timeRange: { start: NEWEST - 1800, end: NEWEST - 600 },
  segment: {
    entryTime: NEWEST - 1800, exitTime: NEWEST - 600,
    entryPrice: 1, exitPrice: 1.5, color: "#008800",
  },
};

const store = vi.hoisted(() => ({ candles: [] as CandleData[] }));
const live = vi.hoisted(() => ({ listeners: new Map<string, (rows: CandleData[]) => void>() }));
const history = vi.hoisted(() => ({
  from: null as number | null,
  initialComplete: true,
  status: "ready",
  loadEarlier: vi.fn(),
  retry: vi.fn(),
}));

vi.mock("@/hooks/useCandleStore", () => ({
  useCandleStore: () => ({
    candles: store.candles,
    isStale: false,
  }),
}));

vi.mock("@/hooks/useCandleHistory", () => ({
  useCandleHistory: ({ lookbackSeconds }: { lookbackSeconds: number }) => ({
    loadedFrom: history.from ?? NEWEST - lookbackSeconds, loadedTo: NEWEST,
    status: history.status, initialComplete: history.initialComplete, loadEarlier: history.loadEarlier, retry: history.retry,
  }),
}));

vi.mock("@/hooks/useRates", () => ({
  useRates: () => ({
    formatPnlValue: (v: number) => String(v),
    formatValue: (v: number) => String(v),
  }),
}));

vi.mock("@/lib/api", () => ({
  api: { getCandles: vi.fn(async () => []) },
}));

vi.mock("@/lib/candle-store", () => ({
  candleChannelKey: (...parts: unknown[]) => parts.join(":"),
  candleStore: {
    onUpdate: vi.fn((key: string, listener: (rows: CandleData[]) => void) => {
      live.listeners.set(key, listener);
      return () => { if (live.listeners.get(key) === listener) live.listeners.delete(key); };
    }),
  },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
const frames = new Map<number, FrameRequestCallback>();

const PROPS: ComponentProps<typeof TradeChart> = {
  server: "local",
  connector: "binance_perpetual",
  pair: "BTC-USDT",
  interval: "1m",
  lookbackSeconds: HOUR,
  startPrice: 100,
  endPrice: 200,
  limitPrice: 150,
  side: 1,
  minSpread: 0.001,
  activePickField: null,
  onPriceSet: vi.fn(),
};

async function render(
  lookbackSeconds: number,
  extra: Partial<ComponentProps<typeof TradeChart>> = {},
  flushFrames = true,
) {
  await act(async () => {
    root.render(<TradeChart {...PROPS} lookbackSeconds={lookbackSeconds} {...extra} />);
  });
  // The chart library arrives through a dynamic import a few hops out; give it
  // a macrotask so the series exists and the data effect has run.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  if (flushFrames) {
    await act(async () => {
      for (const [id, frame] of [...frames]) { frames.delete(id); frame(0); }
    });
  }
}

/** The bar times of every full `setData()` so far, oldest render first. */
function draws(): number[][] {
  const setData = chartDouble.series!.setData as Mock;
  return setData.mock.calls
    .filter(([bars]) => !bars.length || "open" in bars[0])
    .map(([bars]) => (bars as { time: number }[]).map((b) => b.time));
}

function pane(): HTMLElement {
  return chartDouble.charts.at(-1)!.container as HTMLElement;
}

function busy(): string | null {
  return container.querySelector('[role="region"][aria-label="Candlestick chart"]')!.getAttribute("aria-busy");
}

function emitLive(rows: CandleData[]): void {
  for (const listener of live.listeners.values()) listener(rows);
}

function fits(): number {
  return (chartDouble.timeScale!.fitContent as Mock).mock.calls.length;
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
  frames.clear();
  let frameId = 0;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    frames.set(++frameId, cb);
    return frameId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  history.from = null;
  history.initialComplete = true;
  history.status = "ready";
  history.loadEarlier.mockClear();
  history.retry.mockClear();
  live.listeners.clear();
  chartDouble.reset();
  store.candles = A_DAY;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  expect(frames.size).toBe(0);
  container.remove();
  vi.unstubAllGlobals();
});

describe("TradeChart range", () => {
  it("reveals a fitted chart on its draw frame without overlays restoring the previous default scale", async () => {
    await render(HOUR, {
      executorOverlays: [EXECUTOR],
      onChartReady: () => {
        const scale = chartDouble.timeScale!;
        (scale.getVisibleLogicalRange as Mock).mockReturnValue({ from: -50, to: 50 });
        (scale.timeToIndex as Mock).mockReturnValue(0);
      },
    }, false);

    expect(fits()).toBe(1);
    expect(pane().style.visibility).toBe("hidden");
    expect(chartDouble.timeScale!.setVisibleLogicalRange).not.toHaveBeenCalled();
    expect(frames.size).toBe(1);

    await act(async () => {
      for (const [id, frame] of [...frames]) { frames.delete(id); frame(0); }
    });
    expect(pane().style.visibility).toBe("visible");
    expect(fits()).toBe(1);
    expect(chartDouble.timeScale!.setVisibleLogicalRange).not.toHaveBeenCalled();
  });

  it("first presents the complete range after partial history and live ticks arrive", async () => {
    history.initialComplete = false;
    history.status = "loading";
    store.candles = minutes(30);
    await render(DAY);

    expect(pane().style.visibility).toBe("hidden");
    expect(pane().style.pointerEvents).toBe("none");
    expect(busy()).toBe("true");
    expect(draws().filter((bars) => bars.length)).toEqual([]);
    expect(fits()).toBe(0);
    emitLive(minutes(31, NEWEST + 60));
    expect(chartDouble.series!.update).not.toHaveBeenCalled();

    store.candles = minutes(600);
    await render(DAY);
    expect(draws().filter((bars) => bars.length)).toEqual([]);
    expect(fits()).toBe(0);

    history.initialComplete = true;
    history.status = "ready";
    store.candles = A_DAY;
    await render(DAY);

    expect(pane().style.visibility).toBe("visible");
    expect(busy()).toBe("false");
    expect(draws().filter((bars) => bars.length)).toHaveLength(1);
    expect(draws().at(-1)).toHaveLength(1440);
    expect(fits()).toBe(1);
  });

  it("retains a complete chart and live updates while a different preset loads", async () => {
    store.candles = minutes(61);
    await render(HOUR);
    const previous = draws().at(-1);
    history.initialComplete = false;
    history.status = "loading";
    await render(DAY);

    store.candles = minutes(600);
    await render(DAY);
    expect(pane().style.visibility).toBe("visible");
    expect(busy()).toBe("true");
    expect(draws()).toHaveLength(1);
    expect(draws().at(-1)).toEqual(previous);
    expect(fits()).toBe(1);
    emitLive(minutes(601, NEWEST + 60));
    expect(chartDouble.series!.update).toHaveBeenCalledWith(expect.objectContaining({ time: NEWEST + 60 }));

    store.candles = A_DAY;
    history.initialComplete = true;
    history.status = "ready";
    await render(DAY);
    expect(draws()).toHaveLength(2);
    expect(draws().at(-1)).toHaveLength(1440);
    expect(fits()).toBe(2);
    expect(busy()).toBe("false");
  });

  it.each(["waiting", "error"])("keeps the first chart hidden when initial history is %s", async (status) => {
    history.initialComplete = false;
    history.status = status;
    store.candles = minutes(30);
    await render(DAY);

    expect(pane().style.visibility).toBe("hidden");
    expect(draws().filter((bars) => bars.length)).toEqual([]);
    expect(fits()).toBe(0);
    expect(busy()).toBe(status === "waiting" ? "true" : "false");
    const notice = container.querySelector('[role="status"]')!;
    expect(notice.textContent).toMatch(status === "waiting" ? /Rate limited/ : /Could not load/);
    if (status === "error") {
      await act(async () => (notice.querySelector("button") as HTMLButtonElement).click());
      expect(history.retry).toHaveBeenCalledTimes(1);
    }
  });

  it("retains the displayed range after a different preset fails", async () => {
    await render(HOUR);
    const previous = draws().at(-1);
    history.initialComplete = false;
    history.status = "loading";
    await render(DAY);
    history.status = "error";
    await render(DAY);

    expect(pane().style.visibility).toBe("visible");
    expect(draws()).toHaveLength(1);
    expect(draws().at(-1)).toEqual(previous);
    expect(fits()).toBe(1);
    const notice = container.querySelector('[role="status"]')!;
    expect(notice.textContent).toMatch(/Could not load/);
    expect(notice.textContent).toContain("Retry");
    await act(async () => (notice.querySelector("button") as HTMLButtonElement).click());
    expect(history.retry).toHaveBeenCalledTimes(1);
  });

  it("clears the previous range when the requested window completes without candles", async () => {
    store.candles = minutes(61, NEWEST - 2 * HOUR);
    await render(DAY);
    history.initialComplete = false;
    history.status = "loading";
    await render(HOUR);
    history.initialComplete = true;
    history.status = "empty";
    await render(HOUR);

    expect(draws().at(-1)).toEqual([]);
    expect(pane().style.visibility).toBe("hidden");
    expect(fits()).toBe(1);
    const notice = container.querySelector('[role="status"]')!;
    expect(notice.textContent).toContain("No candles in this period");
    await act(async () => (notice.querySelector("button") as HTMLButtonElement).click());
    expect(history.loadEarlier).toHaveBeenCalledTimes(1);
  });

  it("fits the first available history after loading earlier from an empty initial window", async () => {
    store.candles = [];
    history.status = "empty";
    await render(HOUR);
    expect(fits()).toBe(0);
    const loadEarlier = container.querySelector('[role="status"] button') as HTMLButtonElement;
    await act(async () => loadEarlier.click());

    history.from = NEWEST - 2 * HOUR;
    history.status = "ready";
    store.candles = minutes(30, NEWEST - HOUR);
    await render(HOUR);

    expect(pane().style.visibility).toBe("visible");
    expect(draws().at(-1)).toHaveLength(30);
    expect(fits()).toBe(1);
  });

  it("replaces an initial empty notice with live candles while keeping earlier empty-window feedback", async () => {
    store.candles = [];
    history.status = "empty";
    await render(HOUR);
    expect(container.textContent).toContain("No candles in this period");

    store.candles = minutes(1);
    emitLive(store.candles);
    await render(HOUR);

    expect(pane().style.visibility).toBe("visible");
    expect(draws().at(-1)).toEqual([NEWEST]);
    expect(fits()).toBe(1);
    expect(container.textContent).not.toContain("No candles in this period");

    // An explicitly requested earlier page can still be empty while the
    // already displayed recent candle remains useful.
    history.from = NEWEST - 2 * HOUR;
    await render(HOUR);
    expect(pane().style.visibility).toBe("visible");
    const notice = container.querySelector('[role="status"]')!;
    expect(notice.textContent).toContain("No candles in this period");
    expect(notice.textContent).toContain("Load earlier");
    await act(async () => (notice.querySelector("button") as HTMLButtonElement).click());
    expect(history.loadEarlier).toHaveBeenCalledTimes(1);
  });

  it.each([
    { name: "market", extra: { pair: "ETH-USDT" } },
    { name: "interval", extra: { interval: "5m" } },
  ])("hides the old channel while a different $name initializes", async ({ extra }) => {
    await render(HOUR);
    const previousListener = [...live.listeners.values()][0];
    history.initialComplete = false;
    history.status = "loading";
    store.candles = minutes(30);
    await render(HOUR, extra);

    expect(pane().style.visibility).toBe("hidden");
    expect(busy()).toBe("true");
    expect(fits()).toBe(1);
    expect(draws().filter((bars) => bars.length)).toHaveLength(1);
    previousListener(minutes(31, NEWEST + 60));
    emitLive(minutes(31, NEWEST + 60));
    expect(chartDouble.series!.update).not.toHaveBeenCalled();

    history.initialComplete = true;
    history.status = "ready";
    store.candles = A_DAY;
    await render(HOUR, extra);
    expect(pane().style.visibility).toBe("visible");
    expect(draws().at(-1)).toHaveLength(61);
    expect(fits()).toBe(2);
    previousListener(minutes(31, NEWEST + 60));
    expect(chartDouble.series!.update).not.toHaveBeenCalled();
    emitLive(minutes(61, NEWEST + 60));
    expect(chartDouble.series!.update).toHaveBeenCalledTimes(1);
  });

  it("defers the initial executor view until complete history is displayed", async () => {
    history.initialComplete = false;
    history.status = "loading";
    store.candles = minutes(30);
    const onCustomRange = vi.fn();
    const extra = { executorOverlays: [EXECUTOR], selectedExecutorId: "selected", onCustomRange };
    await render(HOUR, extra);

    expect(chartDouble.timeScale!.setVisibleRange).not.toHaveBeenCalled();
    expect(onCustomRange).not.toHaveBeenCalled();
    history.initialComplete = true;
    history.status = "ready";
    store.candles = A_DAY;
    await render(HOUR, extra);

    expect(chartDouble.timeScale!.setVisibleRange).toHaveBeenCalledExactlyOnceWith({
      from: NEWEST - 2160, to: NEWEST - 240,
    });
    expect(onCustomRange).toHaveBeenCalledTimes(1);
    await render(HOUR, { ...extra, rangeRequestId: 1 });
    expect(chartDouble.timeScale!.setVisibleRange).toHaveBeenCalledTimes(1);
    expect(fits()).toBe(2);
  });

  it("draws only the picked range when the store holds more", async () => {
    await render(HOUR);

    const [drawn] = draws().slice(-1);
    expect(drawn).toHaveLength(61);
    expect(drawn[0]).toBe(NEWEST - HOUR);
    expect(drawn[drawn.length - 1]).toBe(NEWEST);
  });

  it("redraws to a shorter range picked after a longer one", async () => {
    await render(DAY);
    expect(draws().slice(-1)[0]).toHaveLength(1440);

    await render(HOUR);

    expect(draws()).toHaveLength(2);
    expect(draws()[1]).toHaveLength(61);
    expect(draws()[1][0]).toBe(NEWEST - HOUR);
    expect(fits()).toBe(2);
  });

  it("redraws back out to a longer range the store already holds", async () => {
    await render(HOUR);
    await render(DAY);

    expect(draws()).toHaveLength(2);
    expect(draws()[1]).toHaveLength(1440);
  });

  it("refits when the current preset is explicitly requested again", async () => {
    await render(HOUR, { rangeRequestId: 0 });
    await render(HOUR, { rangeRequestId: 1 });
    expect(draws()).toHaveLength(2);
    expect(fits()).toBe(2);
  });

  it("prepends history without fitting away the current viewport", async () => {
    await render(HOUR);
    const scale = chartDouble.timeScale!;
    (scale.getVisibleLogicalRange as Mock).mockReturnValue({ from: 5.5, to: 35.5 });
    (scale.timeToIndex as Mock).mockReturnValueOnce(10).mockReturnValueOnce(70);
    history.from = NEWEST - 2 * HOUR;
    await render(HOUR);
    expect(draws().at(-1)).toHaveLength(121);
    expect(fits()).toBe(1);
    expect(scale.setVisibleLogicalRange).toHaveBeenCalledWith({ from: 65.5, to: 95.5 });
  });

  it("marks a real wheel navigation custom and requests an earlier page near the edge", async () => {
    const onCustomRange = vi.fn();
    await render(HOUR, { onCustomRange });
    const scale = chartDouble.timeScale!;
    (scale.getVisibleLogicalRange as Mock)
      .mockReturnValueOnce({ from: 50, to: 100 })
      .mockReturnValue({ from: -10, to: 40 });
    (chartDouble.series!.barsInLogicalRange as Mock).mockReturnValue({ barsBefore: -10 });
    const pane = container.querySelector(".absolute.inset-0")!;
    await act(async () => {
      pane.dispatchEvent(new WheelEvent("wheel", { deltaX: -100, bubbles: true }));
      for (const [id, frame] of [...frames]) { frames.delete(id); frame(0); }
    });
    expect(onCustomRange).toHaveBeenCalledTimes(1);
    expect(history.loadEarlier).toHaveBeenCalledTimes(1);
  });

  it("does not refit a completed preset after the user has started navigating", async () => {
    await render(HOUR);
    history.initialComplete = false;
    history.status = "loading";
    await render(DAY);
    const scale = chartDouble.timeScale!;
    (scale.getVisibleLogicalRange as Mock)
      .mockReturnValueOnce({ from: 10, to: 50 })
      .mockReturnValue({ from: 5, to: 45 });
    await act(async () => {
      container.querySelector(".absolute.inset-0")!.dispatchEvent(new WheelEvent("wheel", { deltaX: -20, bubbles: true }));
      for (const [id, frame] of [...frames]) { frames.delete(id); frame(0); }
    });
    expect(history.loadEarlier).not.toHaveBeenCalled();
    history.initialComplete = true;
    history.status = "ready";
    await render(DAY);
    expect(fits()).toBe(1);
  });

  it("preserves navigation inside the shared time span when a narrower preset completes", async () => {
    const onCustomRange = vi.fn();
    await render(DAY, { onCustomRange });
    history.initialComplete = false;
    history.status = "loading";
    await render(HOUR, { onCustomRange });
    const scale = chartDouble.timeScale!;
    let viewport = { from: 1385, to: 1415 };
    (scale.getVisibleLogicalRange as Mock).mockImplementation(() => viewport);
    (scale.setVisibleLogicalRange as Mock).mockImplementation((range: typeof viewport) => { viewport = range; });
    (scale.timeToIndex as Mock).mockImplementation((time: number) => {
      if (time !== NEWEST - HOUR) return null;
      return draws().at(-1)?.length === 1440 ? 1379 : 0;
    });
    await act(async () => {
      pane().dispatchEvent(new WheelEvent("wheel", { deltaX: -20, bubbles: true }));
      viewport = { from: 1380, to: 1410 };
      for (const [id, frame] of [...frames]) { frames.delete(id); frame(0); }
    });

    history.initialComplete = true;
    history.status = "ready";
    await render(HOUR, { onCustomRange });

    expect(draws().at(-1)).toHaveLength(61);
    expect(onCustomRange).toHaveBeenCalledTimes(1);
    expect(fits()).toBe(1);
    expect(scale.setVisibleLogicalRange).toHaveBeenCalledWith({ from: 1, to: 31 });
    expect(viewport).toEqual({ from: 1, to: 31 });
    expect(history.loadEarlier).not.toHaveBeenCalled();
  });

  it("keeps movement when REST completes before its navigation frame runs", async () => {
    const onCustomRange = vi.fn();
    await render(HOUR, { onCustomRange });
    history.initialComplete = false;
    history.status = "loading";
    await render(DAY, { onCustomRange });
    const scale = chartDouble.timeScale!;
    (scale.getVisibleLogicalRange as Mock)
      .mockReturnValueOnce({ from: 10, to: 25 })
      .mockReturnValue({ from: 5, to: 20 });
    (scale.timeToIndex as Mock).mockReturnValueOnce(0).mockReturnValueOnce(31);

    container.querySelector(".absolute.inset-0")!.dispatchEvent(new WheelEvent("wheel", { deltaX: -20, bubbles: true }));
    expect(frames.size).toBe(1);
    history.initialComplete = true;
    history.status = "ready";
    store.candles = A_DAY;
    await render(DAY, { onCustomRange });

    expect(onCustomRange).toHaveBeenCalledTimes(1);
    expect(fits()).toBe(1);
    expect(scale.setVisibleLogicalRange).toHaveBeenCalledWith({ from: 36, to: 51 });
    expect(frames.size).toBe(0);
    expect(history.loadEarlier).not.toHaveBeenCalled();
  });

  it("discards pending old movement when the same preset is requested again", async () => {
    const onCustomRange = vi.fn();
    await render(HOUR, { onCustomRange });
    (chartDouble.timeScale!.getVisibleLogicalRange as Mock)
      .mockReturnValueOnce({ from: 10, to: 50 })
      .mockReturnValue({ from: 5, to: 45 });
    container.querySelector(".absolute.inset-0")!.dispatchEvent(new WheelEvent("wheel", { deltaX: -20, bubbles: true }));

    await render(HOUR, { onCustomRange, rangeRequestId: 1 });

    expect(fits()).toBe(2);
    expect(onCustomRange).not.toHaveBeenCalled();
    expect(history.loadEarlier).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
  });

  it("observes pending movement before an overlay-only refresh", async () => {
    const onCustomRange = vi.fn();
    await render(HOUR, { onCustomRange });
    history.initialComplete = false;
    history.status = "loading";
    await render(DAY, { onCustomRange });
    (chartDouble.timeScale!.getVisibleLogicalRange as Mock)
      .mockReturnValueOnce({ from: 10, to: 50 })
      .mockReturnValue({ from: 5, to: 45 });
    container.querySelector(".absolute.inset-0")!.dispatchEvent(new WheelEvent("wheel", { deltaX: -20, bubbles: true }));
    const extra = { onCustomRange, executorOverlays: [EXECUTOR] };
    await render(DAY, extra);

    expect(onCustomRange).toHaveBeenCalledTimes(1);
    expect(frames.size).toBe(0);
    expect(history.loadEarlier).not.toHaveBeenCalled();

    history.initialComplete = true;
    history.status = "ready";
    await render(DAY, extra);
    expect(fits()).toBe(1);
  });

  it("keeps a selected executor view through history completion until a preset is requested", async () => {
    const onCustomRange = vi.fn();
    await render(HOUR, { executorOverlays: [EXECUTOR], onCustomRange });
    history.initialComplete = false;
    history.status = "loading";
    const extra = { executorOverlays: [EXECUTOR], selectedExecutorId: "selected", onCustomRange };
    await render(DAY, extra);
    const scale = chartDouble.timeScale!;
    expect(scale.setVisibleRange).toHaveBeenCalledExactlyOnceWith({
      from: NEWEST - 2160, to: NEWEST - 240,
    });
    expect(onCustomRange).toHaveBeenCalledTimes(1);

    history.initialComplete = true;
    history.status = "ready";
    await render(DAY, extra);
    expect(fits()).toBe(1);

    await render(DAY, { ...extra, rangeRequestId: 1 });
    expect(fits()).toBe(2);
    expect(scale.setVisibleRange).toHaveBeenCalledTimes(1);
    expect(onCustomRange).toHaveBeenCalledTimes(1);
  });

  it("leaves a live tick to the incremental update", async () => {
    await render(HOUR);
    expect(draws()).toHaveLength(1);

    // A newer bar appends and the window slides one bar forward with it.
    store.candles = minutes(1441, NEWEST + 60);
    await render(HOUR);

    expect(draws()).toHaveLength(1);
    expect(fits()).toBe(1);
  });

  it("draws every candle in a reconnect snapshot with multiple new tail bars", async () => {
    await render(HOUR);
    store.candles = minutes(1443, NEWEST + 3 * 60);
    await render(HOUR);
    expect(draws()).toHaveLength(2);
    expect(draws().at(-1)?.slice(-3)).toEqual([NEWEST + 60, NEWEST + 120, NEWEST + 180]);
    expect(fits()).toBe(1);
  });
});
