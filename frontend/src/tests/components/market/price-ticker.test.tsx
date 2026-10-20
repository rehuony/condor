/** @vitest-environment jsdom */

import { act, Profiler, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CandleData } from "@/lib/api/market";

const queryData = new Map<string, unknown>();
const candles = new Map<string, CandleData[]>();
const listeners = new Map<string, Set<() => void>>();

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => ({
    data: queryData.get(JSON.stringify(queryKey)),
  }),
}));

vi.mock("@/lib/candle-store", () => ({
  candleChannelKey: (server: string, connector: string, pair: string, interval: string) =>
    `candles:${server}:${connector}:${pair}:${interval}`,
  candleStore: {
    subscribe: (key: string) => candles.get(key) ?? [],
    getCandles: (key: string) => candles.get(key) ?? [],
    unsubscribe: () => {},
    onUpdate: (key: string, listener: () => void) => {
      if (!listeners.has(key)) listeners.set(key, new Set());
      listeners.get(key)!.add(listener);
      return () => listeners.get(key)!.delete(listener);
    },
  },
}));

const { PriceTicker } = await import("@/components/market/PriceTicker");

type Props = ComponentProps<typeof PriceTicker>;
const initial = { server: "alpha", connector: "binance", pair: "SOL-USDT" };

let container: HTMLDivElement;
let root: Root;
const committedMarks: string[] = [];

function value(label: string) {
  return [...container.querySelectorAll("p")]
    .find((node) => node.textContent === label)?.nextElementSibling as HTMLElement | undefined;
}

function render(props: Partial<Props> = {}) {
  act(() => {
    root.render(
      <Profiler id="ticker" onRender={() => committedMarks.push(value("Mark")?.textContent ?? "")}>
        <PriceTicker {...initial} {...props} />
      </Profiler>,
    );
  });
}

function setPrice(mid_price: number, props: Partial<Props> = {}, best_bid = 0, best_ask = 0) {
  const { server, connector, pair } = { ...initial, ...props };
  queryData.set(JSON.stringify(["price", server, connector, pair]), {
    connector, trading_pair: pair, mid_price, best_bid, best_ask,
  });
}

function setClose(close: number, props: Partial<Props> = {}) {
  const { server, connector, pair, interval = "1m" } = { ...initial, ...props };
  const key = `candles:${server}:${connector}:${pair}:${interval}`;
  candles.set(key, [{ timestamp: 1, open: close, high: close, low: close, close, volume: 1 }]);
  for (const listener of listeners.get(key) ?? []) listener();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  queryData.clear();
  candles.clear();
  listeners.clear();
  committedMarks.length = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("PriceTicker", () => {
  it("keeps all requested readings while price and stats arrive separately", () => {
    render({ showStats: true });
    expect(value("Mark")?.textContent).toBe("—");
    expect(value("Δ")?.textContent).toBe("—");
    expect(value("24h Volume")?.textContent).toBe("—");
    expect(value("24h Volume")?.parentElement?.title).toBe("24h volume is not available yet");

    setPrice(105);
    render({ showStats: true });
    expect(value("Mark")?.textContent).toBe("105");
    expect(value("Δ")?.textContent).toBe("—");
    expect(value("Bid")).toBeUndefined();
    expect(value("Ask")).toBeUndefined();

    queryData.set(JSON.stringify(["tickers", "alpha", "binance"]), {
      tickers: [{ trading_pair: "SOL-USDT", change_pct: 0, change_window_s: 86400, usd_volume: 0 }],
    });
    render({ showStats: true });
    expect(value("24h Δ")?.textContent).toBe("+0.00%");
    expect(value("24h Volume")?.textContent).toBe("$0");

    queryData.set(JSON.stringify(["tickers", "alpha", "binance"]), {
      tickers: [{ trading_pair: "SOL-USDT", change_pct: null, change_window_s: null, usd_volume: null }],
    });
    render({ showStats: true });
    expect(value("Δ")?.textContent).toBe("—");
    expect(value("24h Volume")?.textContent).toBe("—");
  });

  it.each([
    { pair: "BTC-USDT" },
    { connector: "kucoin" },
    { server: "beta" },
  ])("never commits a previous market's candle or tick colour after switching to %o", (next) => {
    setClose(200);
    render();
    act(() => setClose(201));
    expect(value("Mark")?.className).toContain("color-green");

    setPrice(60000, next);
    committedMarks.length = 0;
    render(next);
    expect(committedMarks.length).toBeGreaterThan(0);
    expect(committedMarks.every((mark) => mark === "60,000")).toBe(true);
    expect(value("Mark")?.className).not.toMatch(/color-(green|red)/);

    act(() => setClose(60001, next));
    expect(value("Mark")?.textContent).toBe("60,001");
    expect(value("Mark")?.className).toContain("color-green");

    act(() => setClose(999));
    expect(value("Mark")?.textContent).toBe("60,001");
  });

  it("shows a placeholder immediately when a new market has no price", () => {
    setClose(200);
    setPrice(199);
    render();

    committedMarks.length = 0;
    render({ pair: "BTC-USDT" });
    expect(committedMarks.length).toBeGreaterThan(0);
    expect(committedMarks.every((mark) => mark === "—")).toBe(true);
  });

  it("supports candle-only prices without optional stats and remains empty without a pair", () => {
    render({ hasRestPrice: false });
    expect(container.textContent).toBe("Mark—");
    act(() => setClose(105));
    expect(container.textContent).toBe("Mark105");

    render({ pair: "", hasRestPrice: false });
    expect(container.textContent).toBe("");
    expect([...listeners.values()].every((set) => set.size === 0)).toBe(true);
  });
});
