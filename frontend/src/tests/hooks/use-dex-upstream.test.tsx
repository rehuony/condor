/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { useDexUpstream } from "@/hooks/useDexUpstream";

const { getDexUpstream } = vi.hoisted(() => ({ getDexUpstream: vi.fn() }));
vi.mock("@/lib/api/dex", () => ({ dexApi: { getDexUpstream } }));

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;

function Probe() {
  const state = useDexUpstream("primary");
  return <output data-limited={state.limited}>{state.requestsLastMinute}</output>;
}

async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  focusManager.setFocused(true);
  getDexUpstream.mockReset();
  client = new QueryClient({ defaultOptions: { queries: { gcTime: 0 } } });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  client.clear();
  container.remove();
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});

it.each([false, true])("recovers at the healthy cadence after failure (cached limit: %s), then pauses in the background", async (cachedLimit) => {
  const limitedState = {
    rate_limited: true,
    retry_in: 60,
    requests_last_minute: 30,
    budget: 30,
    throttled_calls: 1,
    count_429: 0,
  };
  if (cachedLimit) getDexUpstream.mockResolvedValueOnce(limitedState);
  getDexUpstream
    .mockRejectedValueOnce(new Error("temporarily offline"))
    .mockResolvedValue(limitedState);
  await act(async () => {
    root.render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
  });
  await advance(0);
  expect(getDexUpstream).toHaveBeenCalledTimes(1);
  if (cachedLimit) await advance(5_000);
  const failedCalls = cachedLimit ? 2 : 1;
  expect(getDexUpstream).toHaveBeenCalledTimes(failedCalls);

  await advance(19_999);
  expect(getDexUpstream).toHaveBeenCalledTimes(failedCalls);
  await advance(1);
  expect(getDexUpstream).toHaveBeenCalledTimes(failedCalls + 1);
  expect(container.querySelector("output")?.dataset.limited).toBe("true");
  expect(container.textContent).toBe("30");

  await advance(5_000);
  expect(getDexUpstream).toHaveBeenCalledTimes(failedCalls + 2);

  act(() => focusManager.setFocused(false));
  await advance(20_000);
  expect(getDexUpstream).toHaveBeenCalledTimes(failedCalls + 2);
});
