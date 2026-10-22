/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TOKEN_KEY } from "@/lib/auth-token";
import { ResearchIndexPage } from "@/pages/research-index-page";

const study = (topic: string, id: string, title: string, created_at: string) => ({
  path: `${topic}/2026-10-21/${id}/report.md`, topic, date: "2026-10-21", study: id,
  title, summary: `${title} summary`, created_at, modified_at: created_at,
});
const studies = [
  study("trade-cards", "090000-bbbbbb", "Newest card study", "2026-10-21T09:00:00Z"),
  study("复盘", "080000-cccccc", "调研结论", "2026-10-21T08:00:00Z"),
  study("trade-cards", "070000-aaaaaa", "Older card study", "2026-10-21T07:00:00Z"),
];
let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
const fetchMock = vi.fn();

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  localStorage.setItem(TOKEN_KEY, "test-session");
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  act(() => root.unmount());
  client.clear();
  container.remove();
  localStorage.clear();
  vi.unstubAllGlobals();
});

async function render() {
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <MemoryRouter><ResearchIndexPage /></MemoryRouter>
    </QueryClientProvider>,
  ));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

it("groups studies by topic, newest topic first, and links each report", async () => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ studies })));
  await render();

  expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/research");
  expect([...container.querySelectorAll("h2")].map((h) => h.textContent)).toEqual(["trade-cards · 2", "复盘 · 1"]);
  const links = [...container.querySelectorAll("li a")];
  expect(links.map((a) => a.getAttribute("href"))).toEqual([
    "/research/trade-cards/2026-10-21/090000-bbbbbb/report.md",
    "/research/trade-cards/2026-10-21/070000-aaaaaa/report.md",
    "/research/%E5%A4%8D%E7%9B%98/2026-10-21/080000-cccccc/report.md",
  ]);
  expect(links[0].textContent).toContain("Newest card study summary");
});

it("explains a refused listing instead of showing an empty library", async () => {
  fetchMock.mockResolvedValue(new Response("", { status: 403 }));
  await render();

  expect(container.querySelector('[role="alert"]')?.textContent).toContain("Only administrators");
  expect(container.textContent).not.toContain("No research yet");
});
