// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, QueryObserver, useQuery } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { executorsApi, type ExecutorInfo } from "@/lib/api/executors";
import { DeleteConfirmDialog, ExecutorRows } from "@/components/perf/ExecutorRows";
import { ScopeTree } from "@/components/perf/ScopeTree";
import { buildTree, leafFromExecutor, indexTree } from "@/lib/perf-tree";
import { ExecutorTable } from "@/components/perf/ExecutorTable";
import { terminatedExecutorIds, useExecutorDelete, type ExecutorStop } from "@/components/perf/executorActions";

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

const tree = buildTree(rows.map((ex) => leafFromExecutor(ex)));

function Harness() {
  const deletion = useExecutorDelete("local");
  // Disabled to observe local cache changes independently from the next server poll.
  const { data } = useQuery({ queryKey, queryFn: async () => initial, enabled: false });
  return <>
    <button aria-label="Delete history under All"
      onClick={() => deletion.request(terminatedExecutorIds(tree), "All")}>Delete</button>
    <ExecutorRows executors={data?.pages.flatMap((p) => p.executors) ?? []} stop={stop}
      deletion={deletion} selectedId={null} onSelect={() => {}} />
    {deletion.pendingIds && <DeleteConfirmDialog ids={deletion.pendingIds}
      label={deletion.label} pending={deletion.pending} error={deletion.error}
      onConfirm={deletion.confirm} onCancel={deletion.cancel} />}
  </>;
}

async function render(node = <Harness />) {
  await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>));
}

// Confirmations are portalled to <body>, so buttons are looked up there.
function button(text: string) {
  return Array.from(document.querySelectorAll<HTMLButtonElement>("button"))
    .find((item) => item.textContent?.includes(text))!;
}

/** A button inside the open confirmation, matched exactly. */
function dialogButton(text: string) {
  return [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')]
    .find((item) => item.textContent === text)!;
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
  vi.spyOn(executorsApi, "deleteExecutor").mockResolvedValue({ deleted: true, executor_id: "done" });
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
  expect(executorsApi.deleteExecutor).not.toHaveBeenCalled();
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("Orders and open positions are not affected");
  await click(button("Cancel"));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(executorsApi.deleteExecutor).not.toHaveBeenCalled();
  await click(controls[0]);
  await click(dialogButton("Delete"));
  expect(executorsApi.deleteExecutor).toHaveBeenCalledExactlyOnceWith("local", "done");
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(container.textContent).not.toContain("done");
});

it("deletes a parent, retains rejected rows and retries only failures", async () => {
  vi.mocked(executorsApi.deleteExecutor).mockImplementation(async (_server, id) => {
    if (id === "blocked") throw new Error("Resolve the orphaned LP position first");
    return { deleted: true, executor_id: id };
  });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  client.setQueryData(["executors-infinite", "other"], initial);
  await render();
  await click(container.querySelector('button[aria-label="Delete history under All"]')!);
  await click(dialogButton("Delete"));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(vi.mocked(executorsApi.deleteExecutor).mock.calls.map((args) => args[1])).toEqual(["done", "blocked"]);
  expect(document.querySelector('[role="alert"]')?.textContent).toContain("1 deleted; failed to delete 1 of 2 records");
  expect(document.querySelector('[role="alert"]')?.textContent).toContain("Resolve the orphaned");
  expect(client.getQueryData<typeof initial>(queryKey)?.pages[0].executors.map((ex) => ex.id))
    .toEqual(["blocked", "live", "starting"]);
  expect(client.getQueryData(["executors-infinite", "other"])).toEqual(initial);
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["executors-summary", "local"] });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["perf-history", "local"] });
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("terminated record");
  vi.mocked(executorsApi.deleteExecutor).mockResolvedValue({ deleted: true, executor_id: "blocked" });
  await click(button("Retry failed"));
  expect(vi.mocked(executorsApi.deleteExecutor).mock.calls.map((args) => args[1])).toEqual(["done", "blocked", "blocked"]);

});

it("discards a cold summary response that arrives after deletion and fetches current totals", async () => {
  const summaryKey = ["executors-summary", "local", "1W"];
  const stale = { period: "1W", count: 2, pnl: 100, volume: 200, converted: true };
  const fresh = { ...stale, count: 1, pnl: 25, volume: 50 };
  let resolveInitial!: (value: typeof stale) => void;
  const fetchSummary = vi.fn()
    // Deliberately ignores cancellation like an already computed HTTP response.
    .mockImplementationOnce(() => new Promise<typeof stale>((resolve) => { resolveInitial = resolve; }))
    .mockResolvedValue(fresh);
  const observer = new QueryObserver(client, { queryKey: summaryKey, queryFn: fetchSummary });
  const observed: unknown[] = [];
  const unsubscribe = observer.subscribe((result) => { if (result.data) observed.push(result.data); });
  try {
    expect(fetchSummary).toHaveBeenCalledOnce();
    expect(client.getQueryData(summaryKey)).toBeUndefined();
    await render();
    await click(container.querySelector<HTMLButtonElement>('[title="Delete executor history"]')!);
    await click(dialogButton("Delete"));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(executorsApi.deleteExecutor).toHaveBeenCalledExactlyOnceWith("local", "done");

    await act(async () => {
      resolveInitial(stale);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(fetchSummary).toHaveBeenCalledTimes(2);
    expect(client.getQueryData(summaryKey)).toEqual(fresh);
    expect(observed).not.toContainEqual(stale);
  } finally {
    resolveInitial(stale);
    unsubscribe();
  }
});

it("keeps read-only tables free of deletion actions", async () => {
  await render(<ExecutorTable executors={rows} sortKey="timestamp" sortDir="desc"
    onSort={() => {}} onRowClick={() => {}} selectedExecutorId={null} />);
  expect(container.querySelector('[title="Delete executor history"]')).toBeNull();
});

it("disables a record while deletion is in flight", async () => {
  let complete!: (value: { deleted: boolean; executor_id: string }) => void;
  vi.mocked(executorsApi.deleteExecutor).mockReturnValue(new Promise((resolve) => { complete = resolve; }));
  await render();
  const control = container.querySelector<HTMLButtonElement>('[title="Delete executor history"]')!;
  await click(control);
  await click(dialogButton("Delete"));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(control.disabled).toBe(true);
  await act(async () => { complete({ deleted: true, executor_id: "done" }); });
});

it("targets only the terminated executors loaded under a scope", () => {
  const grouped = buildTree(rows.map((ex) => leafFromExecutor(ex)), "All", { grouping: ["agent"] });
  expect(terminatedExecutorIds(grouped)).toEqual(["done", "blocked"]);
  const running = buildTree(rows.filter((ex) => !ex.status.toLowerCase().startsWith("term")).map((ex) => leafFromExecutor(ex)));
  expect(terminatedExecutorIds(running)).toEqual([]);
});

it("leaves the scope tree free of deletion actions", async () => {
  const nodes = indexTree(tree);
  await render(<ScopeTree root={tree} activeId={tree.id} open={new Set(nodes.keys())}
    showBot={true} onSelect={() => {}} onToggleOpen={() => {}}
    cv={(value) => value} currencySymbol="$" now={Date.now()} />);
  expect(container.querySelector('button[aria-label^="Delete history"]')).toBeNull();
});

it("freezes and deduplicates the loaded targets before confirmation", async () => {
  function Frozen() {
    const deletion = useExecutorDelete("local");
    return <><button onClick={() => deletion.request(["done", "done"], "Loaded records")}>Request</button>
      {deletion.pendingIds && <DeleteConfirmDialog ids={deletion.pendingIds}
      pending={deletion.pending} onConfirm={deletion.confirm} onCancel={deletion.cancel} />}</>;
  }
  await render(<Frozen />);
  await click(button("Request"));
  await click(dialogButton("Delete"));
  expect(executorsApi.deleteExecutor).toHaveBeenCalledExactlyOnceWith("local", "done");
});
