// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api, type ExecutorInfo } from "@/lib/api";
import { DeleteConfirmDialog, ExecutorRows } from "./ExecutorRows";
import { ExecutorTable } from "./ExecutorTable";
import { useExecutorDelete, type ExecutorStop } from "./executorActions";

vi.mock("@/components/charts/ExecutorChart", () => ({ ExecutorChart: () => null }));
vi.mock("@/components/executor/PairLabel", () => ({ PairLabel: () => <span>BTC-USDT</span> }));

const makeExecutor = (id: string, status: string): ExecutorInfo => ({
  id, status, type: "order", connector: "binance_perpetual", trading_pair: "BTC-USDT",
  side: "BUY", close_type: "FAILED", pnl: 0, volume: 0, timestamp: 1_700_000_000,
  controller_id: "main", cum_fees_quote: 0, net_pnl_pct: 0, entry_price: 0,
  current_price: 0, close_timestamp: 0, custom_info: {}, config: {},
});
const rows = [makeExecutor("done", "terminated"), makeExecutor("blocked", "TERMINATED"),
  makeExecutor("live", "running"), makeExecutor("starting", "not_started")];
const initial = { pages: [{ executors: rows, next_cursor: null }], pageParams: [""] };
const queryKey = ["executors-infinite", "local"];
const stop: ExecutorStop = {
  stoppingIds: new Set(), pendingIds: null, error: null, pending: false,
  request: vi.fn(), confirm: vi.fn(), cancel: vi.fn(),
};

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

function Harness() {
  const deletion = useExecutorDelete("local");
  // Disabled to observe local cache changes independently from the next server poll.
  const { data } = useQuery({ queryKey, queryFn: async () => initial, enabled: false });
  return <>
    <ExecutorRows executors={data?.pages.flatMap((p) => p.executors) ?? []} stop={stop}
      deletion={deletion} selectedId={null} onSelect={() => {}} />
    {deletion.pendingIds && <DeleteConfirmDialog ids={deletion.pendingIds}
      onConfirm={deletion.confirm} onCancel={deletion.cancel} />}
    {deletion.error && <p role="alert">{deletion.error}</p>}
  </>;
}

async function render(node = <Harness />) {
  await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>));
}

function button(text: string) {
  return Array.from(container.querySelectorAll<HTMLButtonElement>("button"))
    .find((item) => item.textContent?.includes(text))!;
}

async function click(target: HTMLElement) {
  await act(async () => { target.click(); });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(queryKey, initial);
  vi.spyOn(api, "deleteExecutor").mockResolvedValue({ deleted: true, executor_id: "done" });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  client.clear();
  vi.restoreAllMocks();
});

it("only offers deletion for terminated rows and requires explicit confirmation", async () => {
  await render();
  const controls = container.querySelectorAll<HTMLButtonElement>('button[title="Delete executor history"]');
  expect(controls).toHaveLength(2);
  await click(controls[0]);
  expect(api.deleteExecutor).not.toHaveBeenCalled();
  expect(container.querySelector('[role="dialog"]')?.textContent).toContain("does not cancel orders or close positions");
  await click(button("Cancel"));
  expect(container.querySelector('[role="dialog"]')).toBeNull();
  expect(api.deleteExecutor).not.toHaveBeenCalled();
  await click(controls[0]);
  await click(button("Confirm Delete"));
  expect(api.deleteExecutor).toHaveBeenCalledExactlyOnceWith("local", "done");
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(container.textContent).not.toContain("done");
});

it("filters mixed selections, retains rejected rows and reports partial failure", async () => {
  vi.mocked(api.deleteExecutor).mockImplementation(async (_server, id) => {
    if (id === "blocked") throw new Error("Resolve the orphaned LP position first");
    return { deleted: true, executor_id: id };
  });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  client.setQueryData(["executors-infinite", "other"], initial);
  await render();
  await click(container.querySelector('thead input[type="checkbox"]')!);
  await click(button("Delete Terminated (2)"));
  await click(button("Confirm Delete"));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(vi.mocked(api.deleteExecutor).mock.calls.map((args) => args[1])).toEqual(["done", "blocked"]);
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("Failed to delete 1 of 2 records");
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("Resolve the orphaned");
  expect(client.getQueryData<typeof initial>(queryKey)?.pages[0].executors.map((ex) => ex.id))
    .toEqual(["blocked", "live", "starting"]);
  expect(client.getQueryData(["executors-infinite", "other"])).toEqual(initial);
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["executors-summary", "local"] });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["perf-history", "local"] });
});

it("keeps read-only tables free of deletion actions", async () => {
  await render(<ExecutorTable executors={rows} sortKey="timestamp" sortDir="desc"
    onSort={() => {}} onRowClick={() => {}} selectedExecutorId={null} />);
  expect(container.querySelector('[title="Delete executor history"]')).toBeNull();
});

it("disables a record while deletion is in flight", async () => {
  let complete!: (value: { deleted: boolean; executor_id: string }) => void;
  vi.mocked(api.deleteExecutor).mockReturnValue(new Promise((resolve) => { complete = resolve; }));
  await render();
  const control = container.querySelector<HTMLButtonElement>('[title="Delete executor history"]')!;
  await click(control);
  await click(button("Confirm Delete"));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(control.disabled).toBe(true);
  await act(async () => { complete({ deleted: true, executor_id: "done" }); });
});
