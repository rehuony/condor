/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";

import { executorsApi } from "@/lib/api/executors";
import { marketApi } from "@/lib/api/market";
import { portfolioApi } from "@/lib/api/portfolio";
import type { VenueTraits } from "@/lib/connector-capabilities";
import { LAST_MARKET_KEY } from "@/lib/sessionState";
import { CreateExecutor } from "@/pages/CreateExecutor";

// Keep the page, its forms, router, and query lifecycle real; no chart or socket
// is needed to verify which executor an external URL selects.
vi.mock("@/components/trade/TradeChart", () => ({ TradeChart: () => null }));
vi.mock("@/hooks/useWebSocket", () => ({ useCondorWebSocket: () => ({}) }));

const VENUES: VenueTraits[] = [{
  name: "binance_perpetual",
  hummingbotMarketData: true,
  clmmLp: false,
  credentialed: true,
}];

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let resolveVenues: (venues: VenueTraits[]) => void;

function LocationProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output aria-label="Current location">{location.pathname + location.search}</output>
      <button onClick={() => navigate("/trade?type=dca&ref=chart-link")}>Navigate to DCA</button>
    </>
  );
}

async function settle() {
  for (let i = 0; i < 3; i++) {
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
  }
}

async function render(url: string) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ServerContext.Provider value={{ server: "srv", setServer: () => {} }}>
          <MemoryRouter initialEntries={[url]}>
            <Routes>
              <Route path="/trade" element={<CreateExecutor />} />
              <Route path="/dex" element={<h1>DEX markets</h1>} />
            </Routes>
            <LocationProbe />
          </MemoryRouter>
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  await settle();
}

function button(label: string) {
  return [...container.querySelectorAll("button")].find((node) => node.textContent?.trim() === label);
}

async function click(label: string) {
  const target = button(label);
  expect(target, `Expected a ${label} button`).toBeDefined();
  await act(async () => target!.click());
  await settle();
}

function params() {
  const location = container.querySelector('[aria-label="Current location"]')!.textContent!;
  return new URL(location, "https://condor.test").searchParams;
}

function input(label: string) {
  const control = [...container.querySelectorAll("label")].find((node) => node.textContent?.trim() === label);
  expect(control, `Expected a ${label} field`).toBeDefined();
  return document.getElementById(control!.htmlFor) as HTMLInputElement;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  localStorage.setItem(LAST_MARKET_KEY, JSON.stringify({ connector: "binance_perpetual", pair: "SOL-USDT" }));
  const venues = new Promise<VenueTraits[]>((resolve) => { resolveVenues = resolve; });
  vi.spyOn(marketApi, "getVenues").mockReturnValue(venues);
  vi.spyOn(portfolioApi, "getPortfolio").mockResolvedValue({ server: "srv", connectors: [], total_usd: 0 });
  vi.spyOn(executorsApi, "getExecutors").mockResolvedValue([]);
  vi.spyOn(portfolioApi, "getConsolidatedPositions").mockResolvedValue({ executor_positions: [], bot_positions: [] });
  vi.spyOn(marketApi, "getTradingRules").mockResolvedValue({ connector: "binance_perpetual", rules: [] });
  vi.spyOn(marketApi, "getPrice").mockResolvedValue({
    connector: "binance_perpetual", trading_pair: "SOL-USDT", mid_price: 105, best_bid: 0, best_ask: 0,
  });
  vi.spyOn(marketApi, "getTickers").mockResolvedValue({ connector: "binance_perpetual", tickers: [], updated_at: null });
  vi.spyOn(marketApi, "getRates").mockResolvedValue({ rates: {} });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(() => root.unmount());
  client.clear();
  container.remove();
  vi.restoreAllMocks();
});

describe("CreateExecutor URL selection", () => {
  it("renders a safe Grid form for an invalid type before venues resolve", async () => {
    await render("/trade?type=unknown&ref=watchlist");

    expect(button("Create Grid Executor")).toBeDefined();
    expect(input("Lower Price (grid start)")).toBeDefined();
    expect(params().get("type")).toBe("unknown");

    await act(async () => resolveVenues(VENUES));
    await settle();
    expect(button("Create Grid Executor")).toBeDefined();
    expect(params().get("type")).toBe("grid");
    expect(params().get("ref")).toBe("watchlist");
  });

  it("normalizes an unsupported type after discovering the venue's capabilities", async () => {
    await render("/trade?type=lp&ref=watchlist");
    expect(params().get("type")).toBe("lp");

    await act(async () => resolveVenues(VENUES));
    await settle();
    expect(button("Create Order Executor")).toBeDefined();
    expect(button("Create LP Executor")).toBeUndefined();
    expect(params().get("type")).toBe("order");
    expect(params().get("ref")).toBe("watchlist");
  });

  it.each([
    { discovery: "cached", type: "" },
    { discovery: "cached", type: "&type=unknown" },
    { discovery: "delayed", type: "" },
    { discovery: "delayed", type: "&type=unknown" },
  ])("preserves the gateway redirect with $discovery venues and type '$type'", async ({ discovery, type }) => {
    const venues: VenueTraits[] = [...VENUES, {
      name: "solana-mainnet-beta",
      hummingbotMarketData: false,
      clmmLp: false,
      credentialed: true,
    }];
    if (discovery === "cached") client.setQueryData(["venues", "srv"], venues);

    await render(`/trade?connector=solana-mainnet-beta&pair=SOL-USDC${type}`);

    if (discovery === "delayed") {
      expect(container.querySelector("h1")).toBeNull();
      await act(async () => resolveVenues(venues));
      await settle();
    }

    expect(container.querySelector('[aria-label="Current location"]')?.textContent).toBe("/dex");
    expect(container.querySelector("h1")?.textContent).toBe("DEX markets");
    expect(button("Create Grid Executor")).toBeUndefined();
    expect(button("Create Order Executor")).toBeUndefined();
  });

  it("follows URL navigation on the mounted page and keeps other parameters when switching tabs", async () => {
    resolveVenues(VENUES);
    await render("/trade?type=grid&ref=watchlist");
    const amount = input("Total Amount (USDT)");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(amount, "127");
      amount.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await click("Navigate to DCA");
    expect(button("Create DCA Executor")).toBeDefined();
    expect(container.textContent).toContain("DCA Levels");
    expect(button("Create Grid Executor")).toBeUndefined();

    await click("Grid");
    expect(button("Create Grid Executor")).toBeDefined();
    expect(input("Total Amount (USDT)").value).toBe("127");
    expect(params().get("type")).toBe("grid");
    expect(params().get("ref")).toBe("chart-link");
  });
});

it("preserves the grid draft while mobile results and range controls change", async () => {
  await render("/trade?connector=binance_perpetual&pair=SOL-USDT&type=grid");
  await act(async () => resolveVenues(VENUES));
  await settle();
  const lower = input("Lower Price (grid start)");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(lower, "99");
    lower.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const results = [...container.querySelectorAll("button")].find((b) => b.textContent?.includes("Show details"))!;
  await act(async () => results.click());
  expect(results.getAttribute("aria-expanded")).toBe("true");
  const menu = (label: string) => container.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;
  const choose = async (label: string, option: string) => {
    await act(async () => menu(label).click());
    const item = [...document.querySelectorAll(`[role="listbox"][aria-label="${label}"] [role="option"]`)]
      .find((o) => o.textContent === option) as HTMLElement;
    await act(async () => item.click());
  };
  await choose("Candle interval", "1h");
  await choose("Chart range", "1d");
  await settle();
  await act(async () => results.click());
  expect(results.getAttribute("aria-expanded")).toBe("false");
  expect(menu("Candle interval").textContent).toBe("1h");
  expect(menu("Chart range").textContent).toBe("1d");
  expect(input("Lower Price (grid start)").value).toBe("99");
});
