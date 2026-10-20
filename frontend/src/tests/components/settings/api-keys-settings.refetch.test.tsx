/**
 * How much credential/wallet traffic the Keys tab adds while it is open (PERF-351).
 *
 * `["settings-credentials", server]` and `["gateway-wallets", server]` are
 * observed from two places at once: `useCredentials`, which `AppShell` mounts on
 * every route, and this tab. `staleTime` is per-observer and react-query lets the
 * *shortest* one govern refetch-on-mount and refetch-on-focus, so while the tab
 * declared the same keys with no `staleTime` of its own, its 5s client default
 * pulled the shared queries down with it: every window refocus re-fetched both
 * lists, throwing away the 5-minute prefetch `usePrefetchData` had paid for.
 *
 * These tests count requests rather than inspecting hooks, because that is where
 * the defect lived — the component renders identically either way. `fetch` is the
 * only stub, so the real `api` layer builds the URLs and the assertions are about
 * literal paths.
 *
 * `Date.now` is offset rather than faked wholesale: react-query decides staleness
 * from it, and the async flush below still needs real timers to run.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useCredentials } from "@/hooks/useCredentials";
import { ServerContext } from "@/hooks/useServer";
import { credentialsQuery } from "@/lib/queryClient";
import { ApiKeysSettings } from "@/components/settings/ApiKeysSettings";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let requested: string[] = [];
let clockOffset = 0;
let credentialStatus = 200;
let walletStatus = 200;
let credentials = [{ connector_name: "binance", connector_type: "spot" }];
const realNow = Date.now;

function respond(path: string): unknown {
  if (path.endsWith("/api/v1/servers")) {
    return [{ name: "prod", host: "h", port: 8000, online: true, permission: "owner" }];
  }
  if (path.includes("/settings/credentials")) {
    return { credentials };
  }
  if (path.includes("/settings/connectors")) return { connectors: [] };
  if (path.includes("/gateway/wallets")) {
    return { wallets: [{ chain: "solana", walletAddresses: ["So1111"], default_address: "So1111" }] };
  }
  if (path.includes("/gateway/status")) return { running: true };
  return {};
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  requested = [];
  clockOffset = 0;
  credentialStatus = 200;
  walletStatus = 200;
  credentials = [{ connector_name: "binance", connector_type: "spot" }];
  vi.spyOn(Date, "now").mockImplementation(() => realNow() + clockOffset);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      requested.push(path);
      const status = path.includes("/settings/credentials") ? credentialStatus
        : path.includes("/gateway/wallets") ? walletStatus : 200;
      return new Response(JSON.stringify(status === 200 ? respond(path) : { detail: "Hummingbot API is unavailable" }), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const countOf = (fragment: string) => requested.filter((u) => u.includes(fragment)).length;
const credentialCalls = () => countOf("/settings/credentials");
const walletCalls = () => countOf("/gateway/wallets");

async function flush() {
  for (let i = 0; i < 25; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

/** The Keys tab, with the app-wide `useCredentials` observer mounted beside it. */
function Harness() {
  const { hasKeys } = useCredentials();
  return (
    <>
      <output aria-label="Credential availability">
        {hasKeys === undefined ? "unknown" : hasKeys ? "available" : "missing"}
      </output>
      <ApiKeysSettings />
    </>
  );
}

const availability = () => container.querySelector("output")?.textContent;

/** Mount the pair on one cache that carries the real client's 5s default. */
async function mountKeysTab() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 5000 },
      mutations: { retry: false },
    },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ServerContext.Provider value={{ server: "prod", setServer: () => {} }}>
          <Harness />
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  await flush();
  return qc;
}

/** What the browser does when the tab is brought back to the front. */
async function refocus() {
  await act(async () => {
    window.dispatchEvent(new Event("visibilitychange"));
  });
  await flush();
}

function button(label: string): HTMLButtonElement {
  const all = [...container.querySelectorAll("button")];
  const found = all.find(
    (b) => b.getAttribute("aria-label") === label || b.textContent?.trim() === label,
  );
  if (!found) {
    const have = all.map((b) => b.getAttribute("aria-label") ?? b.textContent?.trim()).join(" | ");
    throw new Error(`no button "${label}" — have: ${have}`);
  }
  return found as HTMLButtonElement;
}

describe("ApiKeysSettings credential traffic", () => {
  it("shares one request per list with the app-wide observer on mount", async () => {
    await mountKeysTab();

    expect(credentialCalls()).toBe(1);
    expect(walletCalls()).toBe(1);
  });

  it("issues nothing on refocus, even long past the client's 5s default", async () => {
    await mountKeysTab();
    expect(credentialCalls()).toBe(1);

    // Well past the 5s client default and the old 30s hook value, well inside
    // the 5 minutes the shared factory declares.
    clockOffset = 60_000;
    await refocus();

    expect(credentialCalls()).toBe(1);
    expect(walletCalls()).toBe(1);

    // And still nothing a second time round.
    clockOffset = 120_000;
    await refocus();

    expect(credentialCalls()).toBe(1);
    expect(walletCalls()).toBe(1);
  });

  it("still refreshes the list immediately when a credential is deleted", async () => {
    await mountKeysTab();
    expect(credentialCalls()).toBe(1);

    await act(async () => button("Delete credential").click());
    await act(async () => button("Confirm delete").click());
    await flush();

    // The DELETE plus the invalidate-driven refetch of the shared key.
    expect(requested.some((u) => u.includes("/settings/credentials/binance"))).toBe(true);
    expect(credentialCalls()).toBeGreaterThan(2);
  });
});

describe("ApiKeysSettings unavailable credentials", () => {
  it("reports an unavailable list without treating saved keys as missing, then recovers on retry", async () => {
    credentialStatus = 502;
    walletStatus = 502;
    await mountKeysTab();

    expect(availability()).toBe("unknown");
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("Could not load API keys");
    expect(container.textContent).not.toContain("No API keys configured");
    expect(container.textContent).not.toContain("0 credentials");
    expect(button("Add API Key").disabled).toBe(true);

    credentialStatus = 200;
    await act(async () => button("Retry").click());
    await flush();

    expect(availability()).toBe("available");
    expect(container.textContent).toContain("binance");
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(button("Add API Key").disabled).toBe(false);
  });

  it("keeps cached credentials visible and usable when a background refresh fails", async () => {
    walletStatus = 502;
    const qc = await mountKeysTab();
    credentialStatus = 502;
    await act(async () => { await qc.invalidateQueries({ queryKey: credentialsQuery("prod").queryKey }); });
    await flush();

    expect(availability()).toBe("available");
    expect(container.textContent).toContain("binance");
    expect(container.textContent).toContain("1 credential configured");
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("Showing the last loaded credentials");
    expect(container.textContent).not.toContain("No API keys configured");
  });

  it("only confirms missing keys after a successful empty response", async () => {
    credentials = [];
    walletStatus = 502;
    const qc = await mountKeysTab();
    expect(availability()).toBe("missing");
    expect(container.textContent).toContain("No API keys configured");

    credentialStatus = 502;
    await act(async () => { await qc.invalidateQueries({ queryKey: credentialsQuery("prod").queryKey }); });
    await flush();

    expect(availability()).toBe("unknown");
    expect(container.textContent).not.toContain("No API keys configured");
    expect(container.textContent).not.toContain("0 credentials");
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
  });
});
