/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api, type PortfolioResponse } from "@/lib/api";
import { Portfolio } from "./Portfolio";

vi.mock("@/hooks/useServer", () => ({ useServer: () => ({ server: "test" }) }));
vi.mock("@/hooks/useWebSocket", () => ({ useCondorWebSocket: () => {} }));
vi.mock("@/hooks/useLpPositions", () => ({ useLpPositions: () => ({ positions: [], label: () => "", isLoading: false }) }));
vi.mock("@/hooks/useRates", () => {
  const convert = (value: number) => ({ value, converted: true });
  return { useRates: () => ({ convert, formatValueDetailed: String, formatPnlValue: String, resolvedSymbol: "$" }) };
});
vi.mock("@/lib/api", () => ({ api: {
  getPortfolio: vi.fn(),
  getPortfolioHistory: vi.fn(),
  getBots: vi.fn(async () => ({ bots: [], controllers: [] })),
  getExecutors: vi.fn(async () => []),
  getExecutorsSummary: vi.fn(async () => ({ count: 0, pnl: 0, volume: 0, converted: true })),
  getAgents: vi.fn(async () => []),
  getConsolidatedPositions: vi.fn(async () => ({ executor_positions: [], bot_positions: [] })),
} }));

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  vi.mocked(api.getPortfolioHistory).mockResolvedValue({
    server: "test", interval: "1h", valuation: "wallet",
    points: [{ timestamp: 1000, total_usd: 1000 }, { timestamp: 1300, total_usd: 1500 }],
  });
});

afterEach(() => {
  act(() => root.unmount());
  client.clear();
  container.remove();
});

async function render(overrides: Partial<PortfolioResponse> = {}, failRefresh = false) {
  const response: PortfolioResponse = {
    server: "test", total_usd: 1000, equity_usd: 800, unrealized_pnl_usd: -200,
    connectors: [{ connector: "binance_perpetual", account_name: "master", total_usd: 1000,
      equity_usd: 800, unrealized_pnl_usd: -200,
      balances: [{ token: "USDT", total: 1000, available: 600, usd_value: 1000 }] }],
    ...overrides,
  };
  vi.mocked(api.getPortfolio).mockImplementation(async (_server, refresh) => {
    if (refresh && failRefresh) throw new Error("exchange unavailable");
    return response;
  });
  await act(async () => {
    root.render(<QueryClientProvider client={client}><MemoryRouter><Portfolio /></MemoryRouter></QueryClientProvider>);
  });
  for (let i = 0; i < 5; i++) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  }
}

it("shows exchange equity and floating loss while retaining the wallet's token price", async () => {
  await render();
  expect(container.textContent).toContain("Portfolio equity");
  expect(container.textContent).toContain("$800");
  expect(container.textContent).toContain("Wallet $1,000");
  expect(container.textContent).toMatch(/Unrealized.*200/);
  const tokenRow = [...container.querySelectorAll("tr")].find((r) => r.textContent?.startsWith("USDT"))!;
  expect(tokenRow.textContent).toContain("$1.00");
  expect(tokenRow.textContent).not.toContain("$0.80");
});

it("labels a deposit-driven increase as a balance change including transfers", async () => {
  await render();
  expect(container.textContent).toContain("1W balance change · includes transfers");
  expect(container.textContent).toContain("Wallet balance · excludes unrealized PnL");
  expect(container.textContent).not.toContain("Portfolio PnL");
});

it("does not invent zero unrealized PnL when the upstream does not provide equity", async () => {
  await render({ equity_usd: null, unrealized_pnl_usd: null });
  expect(container.textContent).toContain("Portfolio balance");
  expect(container.textContent).toContain("Unrealized unavailable");
  expect(container.textContent).not.toContain("Portfolio equity");
});

it("marks a failed exchange refresh while keeping the last known account value", async () => {
  await render({}, true);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 550)); });
  expect(container.textContent).toContain("Could not refresh balances. Showing the last available snapshot.");
  expect(container.textContent).toContain("$800");
});
