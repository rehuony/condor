/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TOKEN_KEY } from "@/lib/auth-token";
import { ResearchFilePage } from "@/pages/research-file-page";

const body = "## 关键发现\n\n| Asset | Value |\n|---|---|\n| BTC | 42 |\n\n[数据](./data.json)\n\n<script>window.PWNED = true</script>";
const content = `---\ntitle: 调研结论\nsummary: 继续观察市场深度。\ncreated_at: '2026-10-10T01:00:00Z'\n---\n\n# 调研结论\n\n${body}`;
const metadata = {
  name: "复盘.md", path: "topic/复盘.md", size: 128,
  modified_at: "2026-10-10T01:00:00Z", created_at: "2026-10-10T01:00:00Z",
  title: "调研结论", summary: "继续观察市场深度。", preview: "markdown", content, body,
};
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
  fetchMock.mockReset().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(metadata))));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  act(() => root.unmount());
  client.clear();
  container.remove();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function render() {
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/research/topic/%E5%A4%8D%E7%9B%98.md"]}><ResearchFilePage /></MemoryRouter>
    </QueryClientProvider>,
  ));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
}

function button(text: string) {
  return [...container.querySelectorAll("button")].find((el) => el.textContent?.trim() === text)!;
}

it("renders authenticated Markdown, resolves attachments, and switches to source", async () => {
  await render();
  expect(container.querySelector("h1")?.textContent).toBe("调研结论");
  expect(container.querySelectorAll("h1")).toHaveLength(1);
  expect(container.querySelector("header")?.textContent).toContain("继续观察市场深度。");
  expect(container.querySelector("article")?.textContent).not.toContain("created_at");
  expect(container.querySelector("details")?.open).toBe(false);
  expect(container.querySelector("table")?.textContent).toContain("BTC42");
  expect(container.querySelector("article a")?.getAttribute("href")).toBe("/research/topic/data.json");
  expect(container.querySelector("script")).toBeNull();
  expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/research/topic/%E5%A4%8D%E7%9B%98.md");
  expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer test-session");
  act(() => button("Source").click());
  expect(container.querySelector("pre")?.textContent).toBe(content);
  act(() => button("Preview").click());
  expect(container.querySelector("article h2")?.textContent).toBe("关键发现");
});

it("downloads original bytes with auth and a filename, never a filesystem URL", async () => {
  await render();
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = vi.fn(() => "blob:download-test");
  URL.revokeObjectURL = vi.fn();
  let downloadedName = "";
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { downloadedName = this.download; });
  fetchMock.mockResolvedValueOnce(new Response(content));
  vi.useFakeTimers();
  try {
    await act(async () => button("Download").click());
    expect(downloadedName).toBe("复盘.md");
    const call = fetchMock.mock.calls.at(-1)!;
    expect(call[0]).toBe("/api/v1/research/topic/%E5%A4%8D%E7%9B%98.md?download=1");
    expect(call[1].headers.Authorization).toBe("Bearer test-session");
    const blob = vi.mocked(URL.createObjectURL).mock.calls[0][0] as Blob;
    expect(await blob.text()).toBe(content);
    await act(async () => vi.advanceTimersByTime(1000));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:download-test");
  } finally {
    vi.useRealTimers();
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  }
});

for (const [status, message] of [[403, "Only administrators"], [404, "missing or no longer available"]] as const) {
  it(`explains a ${status} and does not offer a broken download`, async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status }));
    await render();
    expect(container.querySelector("[role='alert']")?.textContent).toContain(message);
    expect(button("Download").disabled).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
}

it("keeps large and unsupported files available for download", async () => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ ...metadata, preview: "none", content: null })));
  await render();
  expect(container.textContent).toContain("Download the original");
  expect(button("Download").disabled).toBe(false);
});

it("reports download failures without losing the preview", async () => {
  await render();
  fetchMock.mockResolvedValueOnce(new Response("{}", { status: 404 }));
  await act(async () => button("Download").click());
  expect(container.querySelector("[role='alert']")?.textContent).toContain("missing");
  expect(container.querySelector("article h2")?.textContent).toBe("关键发现");
});
