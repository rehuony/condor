/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api, type PortfolioHistoryResponse, type PortfolioResponse } from "@/lib/api";
import { Portfolio } from "@/pages/Portfolio";

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

const history: PortfolioHistoryResponse = {
  server: "test", interval: "1h", valuation: "wallet",
  points: [{ timestamp: 1000, total_usd: 1000 }, { timestamp: 1300, total_usd: 1500 }],
};

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  vi.mocked(api.getPortfolioHistory).mockResolvedValue(history);
});

afterEach(() => {
  act(() => root.unmount());
  client.clear();
  container.remove();
});

async function settleQueries() {
  for (let i = 0; i < 5; i++) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  }
}

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
  await settleQueries();
}

function evolutionCard() {
  return [...container.querySelectorAll("h3")].find((heading) => heading.textContent === "Portfolio Evolution")!
    .parentElement!.parentElement!.parentElement!;
}

function historyChart() {
  return evolutionCard().querySelector<SVGSVGElement>('svg[role="img"][aria-label="Portfolio balance history"]');
}

function balanceLine() {
  return [...(historyChart()?.querySelectorAll("path") ?? [])]
    .find((path) => path.getAttribute("fill") === "none" && path.getAttribute("d")?.includes("L"))
    ?.getAttribute("d");
}

async function retryHistory() {
  const button = [...evolutionCard().querySelectorAll("button")].find((candidate) => candidate.textContent === "Retry");
  expect(button).toBeDefined();
  await act(async () => button!.click());
  await settleQueries();
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

it("shows a liability's token price and signed allocation without a negative bar width", async () => {
  await render({ total_usd: 80, equity_usd: 80, unrealized_pnl_usd: 0,
    connectors: [{ account_name: "master", connector: "binance", total_usd: 80,
      balances: [{ token: "USDT", total: 100, available: 100, usd_value: 100 },
        { token: "USDC", total: -20, available: -20, usd_value: -20 }] }],
  });
  const liability = [...container.querySelectorAll("tr")].find((row) => row.textContent?.startsWith("USDC"))!;
  expect(liability.children[2].textContent).toBe("$1.00");
  expect(liability.children[4].textContent).toBe("-25.0%");
  expect(liability.children[4].querySelector<HTMLElement>("[style]")!.style.width).toBe("0%");
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

it("keeps the last account value on refresh failure and clears the notice when ordinary fetching recovers", async () => {
  await render({}, true);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 550)); });
  expect(container.textContent).toContain("Could not refresh balances. Showing the last available snapshot.");
  expect(container.textContent).toContain("$800");

  // Exercise the same query used by the ordinary 15-second poll. Its recovery
  // must clear the notice even if the account value itself has not changed.
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["portfolio", "test"], exact: true });
  });
  await settleQueries();
  expect(api.getPortfolio).toHaveBeenLastCalledWith("test");
  expect(container.textContent).not.toContain("Could not refresh balances.");
  expect(container.textContent).toContain("$800");
});

it("shows a single snapshot as a visible value and timestamp without inventing a balance change", async () => {
  vi.mocked(api.getPortfolioHistory).mockResolvedValue({
    ...history,
    points: [{ timestamp: new Date(2026, 9, 16, 10, 15).getTime() / 1000, total_usd: 128.45 }],
  });
  await render({ total_usd: 128.45, equity_usd: null, unrealized_pnl_usd: null });

  const chart = historyChart();
  expect(chart).not.toBeNull();
  expect(chart!.querySelectorAll("circle")).toHaveLength(1);
  expect(chart!.textContent).toContain("$128.45");
  const timestamps = [...chart!.querySelectorAll("text")].filter((label) => label.textContent?.includes("Oct 16"));
  expect(timestamps).toHaveLength(1);
  expect(timestamps[0].textContent).toMatch(/\d{1,2}:\d{2}/);
  expect(evolutionCard().textContent).toContain("Only one snapshot in this period. More history is needed to show a trend.");
  expect(evolutionCard().querySelector<HTMLButtonElement>('button[aria-label="Stacked area chart"]')!.disabled).toBe(true);

  const balanceCell = [...container.querySelectorAll("span")]
    .find((label) => label.textContent === "Portfolio balance")!.parentElement!;
  expect(balanceCell.textContent).toContain("1W balance change · includes transfers");
  expect(balanceCell.textContent).not.toContain("+$0.00");
});

it("distinguishes an empty history from a failed request", async () => {
  vi.mocked(api.getPortfolioHistory).mockResolvedValue({ ...history, points: [] });
  await render();

  expect(evolutionCard().textContent).toContain("No snapshots in this period yet.");
  expect(evolutionCard().textContent).not.toContain("Could not load portfolio history.");
  expect(historyChart()).toBeNull();
});

it("offers a retry after the initial history request fails and renders the recovered history", async () => {
  vi.mocked(api.getPortfolioHistory).mockRejectedValue(new Error("history unavailable"));
  await render();

  expect(evolutionCard().textContent).toContain("Could not load portfolio history.");
  expect(evolutionCard().textContent).not.toContain("No snapshots in this period yet.");
  expect(historyChart()).toBeNull();

  vi.mocked(api.getPortfolioHistory).mockResolvedValue(history);
  await retryHistory();

  expect(evolutionCard().textContent).not.toContain("Could not load portfolio history.");
  expect(balanceLine()).toBeTruthy();
});

it("preserves the balance line while asset breakdown loads or fails, then retries the breakdown", async () => {
  let rejectBreakdown!: (error: Error) => void;
  const pendingBreakdown = new Promise<PortfolioHistoryResponse>((_resolve, reject) => { rejectBreakdown = reject; });
  vi.mocked(api.getPortfolioHistory).mockImplementation(async (_server, _range, breakdown) =>
    breakdown ? pendingBreakdown : history,
  );
  await render();
  const originalLine = balanceLine();
  expect(originalLine).toBeTruthy();

  await act(async () => evolutionCard().querySelector<HTMLButtonElement>('button[title="Stacked area chart"]')!.click());
  await settleQueries();
  expect(evolutionCard().textContent).toContain("Loading asset breakdown…");
  expect(balanceLine()).toBe(originalLine);

  await act(async () => rejectBreakdown(new Error("breakdown unavailable")));
  await settleQueries();
  expect(evolutionCard().textContent).toContain("Could not load asset breakdown. Showing the balance line.");
  expect(balanceLine()).toBe(originalLine);

  vi.mocked(api.getPortfolioHistory).mockImplementation(async (_server, _range, breakdown) =>
    breakdown ? {
      ...history,
      top_tokens: ["USDT"],
      points: history.points.map((point) => ({ ...point, tokens: { USDT: point.total_usd } })),
    } : history,
  );
  await retryHistory();
  expect(evolutionCard().textContent).not.toContain("Could not load asset breakdown.");
  expect(evolutionCard().textContent).not.toContain("Loading asset breakdown…");
  expect(evolutionCard().textContent).toContain("USDT");
  expect(historyChart()!.querySelector('path[d*="L"]')).not.toBeNull();
});

it("keeps the last history visible when a background refresh fails and clears the warning after retry", async () => {
  await render();
  const originalLine = balanceLine();
  expect(originalLine).toBeTruthy();

  vi.mocked(api.getPortfolioHistory).mockRejectedValue(new Error("history unavailable"));
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["portfolio-history", "test", "1W"], exact: true });
  });
  await settleQueries();
  expect(evolutionCard().textContent).toContain("Could not refresh history. Showing the last available snapshot.");
  expect(balanceLine()).toBe(originalLine);

  vi.mocked(api.getPortfolioHistory).mockResolvedValue(history);
  await retryHistory();
  expect(evolutionCard().textContent).not.toContain("Could not refresh history.");
  expect(balanceLine()).toBe(originalLine);
});
