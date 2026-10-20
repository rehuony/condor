/**
 * The Settings → Hummingbot API panel (FEAT-121).
 *
 * Four things are worth pinning, and none of them is "does it render the fields":
 *
 * * **The navbar is the only picker.** Switching servers must re-ask, which it does by
 *   carrying `server` in every query key. A panel that kept the previous server's answers
 *   would report one server's image and write another server's file.
 * * **A trader sees but cannot save.** The backend enforces it; the client's job is to
 *   predict that refusal, never to invent one.
 * * **A save sends only what changed.** Restating the whole form would write back
 *   whatever it happened to be holding — including a field another operator had just
 *   changed.
 * * **A 501 is not a red error.** The server is healthy and older than this panel, so
 *   the operator's action is "deploy a newer build", not "find the outage".
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getApiServerInfo = vi.fn();
const getApiClientConfig = vi.fn();
const updateApiClientConfig = vi.fn();
const serverHolder = { current: "alpha" as string | null };
const ownerHolder = { current: true };

vi.mock("@/lib/api", () => ({
  errorStatus: (e: unknown) => (e as { status?: number } | null)?.status,
}));
vi.mock("@/lib/api/settings", () => ({
  settingsApi: {
    getApiServerInfo: (s: string) => getApiServerInfo(s),
    getApiClientConfig: (s: string) => getApiClientConfig(s),
    updateApiClientConfig: (s: string, c: unknown) => updateApiClientConfig(s, c),
  },
}));
vi.mock("@/hooks/useServer", () => ({
  useServer: () => ({ server: serverHolder.current }),
}));
vi.mock("@/hooks/useServerPermission", () => ({
  OWNER_ONLY_HINT: "Only the server's owner can change this.",
  useServerPermission: () => ({ isOwner: ownerHolder.current }),
}));

const { ApiServerSettings } = await import("@/components/settings/ApiServerSettings");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const REVISION = "768e7c1f96fb1f21d91ff2313c0de0f8afa1cd3e";

const INFO = {
  api_version: "1.0.1",
  hummingbot_version: "20260616",
  market_data: { ticker_update_interval: 30, ticker_max_age: 60 },
  docker_available: true,
  container: {
    id: "c".repeat(64),
    name: "hummingbot-api",
    image: "rehuony/hummingbot-api:main",
    image_id: `sha256:${"ab12".repeat(16)}`,
    digest: null as string | null,
    image_created: "2026-10-19T23:59:58.123456789Z",
    build: {
      source: "https://github.com/rehuony/hummingbot-api",
      revision: REVISION,
      branch: "main",
      created: "2026-10-20T07:00:00Z",
      dirty: false as boolean | null,
    },
    compose_project: "hummingbot-api",
    compose_working_dir: "/srv/hummingbot-api",
    compose_config_files: "/srv/hummingbot-api/docker-compose.yml",
  },
};

const CONFIG = {
  account_name: "master_account",
  rate_oracle_source: { name: "gate_io" },
  global_token: { global_token_name: "USDT", global_token_symbol: "$" },
  rate_limits_share_pct: 100,
  available_sources: ["binance", "gate_io", "kucoin"],
};

let container: HTMLDivElement;
let root: Root;

async function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ApiServerSettings />
      </QueryClientProvider>,
    );
  });
  for (let i = 0; i < 20; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
    });
  }
}

function field(label: string): HTMLInputElement | HTMLButtonElement {
  const el = container.querySelector(`[aria-label="${label}"]`);
  if (!el) throw new Error(`no field labelled ${label}`);
  return el as HTMLInputElement | HTMLButtonElement;
}

/** The values a themed menu offers, read by opening it. */
async function menuValues(label: string): Promise<string[]> {
  await act(async () => (field(label) as HTMLButtonElement).click());
  const values = [...document.querySelectorAll(`[role="listbox"][aria-label="${label}"] [role="option"]`)]
    .map((option) => option.getAttribute("data-value") ?? "");
  await act(async () => (field(label) as HTMLButtonElement).click());
  return values;
}

function saveButton(): HTMLButtonElement {
  const button = [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes("Save"),
  );
  if (!button) throw new Error("no Save button");
  return button as HTMLButtonElement;
}

async function setValue(label: string, value: string) {
  const el = field(label);
  if (el instanceof HTMLButtonElement) {
    await act(async () => el.click());
    await act(async () => {
      document.querySelector<HTMLButtonElement>(`[role="listbox"][aria-label="${label}"] [data-value="${value}"]`)!.click();
    });
    return;
  }
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  serverHolder.current = "alpha";
  ownerHolder.current = true;
  getApiServerInfo.mockReset().mockResolvedValue(INFO);
  getApiClientConfig.mockReset().mockResolvedValue(CONFIG);
  updateApiClientConfig.mockReset().mockResolvedValue({ success: true, message: "ok" });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("ApiServerSettings", () => {
  it("reports the versions, the image and the market-data tunables", async () => {
    await mount();
    const text = container.textContent ?? "";

    expect(text).toContain("1.0.1");
    expect(text).toContain("20260616");
    expect(text).toContain("rehuony/hummingbot-api:main");
    // Read-only, and the copy has to say where they actually live.
    expect(text).toContain("Ticker update interval");
    expect(text).toContain("MARKET_DATA_*");
  });

  it("keeps the local image ID apart from a registry digest that does not exist", async () => {
    await mount();
    const text = container.textContent ?? "";

    expect(text).toContain("sha256:ab12ab12ab12");
    expect(text).toContain("none (local build)");
  });

  it("shows the revision short, with the full SHA on hover and a copy button", async () => {
    await mount();

    const short = [...container.querySelectorAll(`[title="${REVISION}"]`)];
    expect(short.map((el) => el.textContent)).toContain(REVISION.slice(0, 12));
    expect(container.textContent).not.toContain(REVISION);
    expect(container.querySelector('[aria-label="Copy full value"]')).not.toBeNull();
  });

  it("names the build start and Docker's image creation time separately, in UTC", async () => {
    await mount();
    const text = container.textContent ?? "";

    expect(text).toContain("Built (start)");
    expect(text).toContain("2026-10-20 07:00:00 UTC");
    expect(text).toContain("Image created (Docker)");
    // Nanosecond precision from Docker still parses.
    expect(text).toContain("2026-10-19 23:59:58 UTC");
  });

  it.each([
    [true, "uncommitted changes"],
    [false, "clean"],
    [null, "unknown"],
  ])("reports a dirty flag of %s as %s", async (dirty, label) => {
    getApiServerInfo.mockResolvedValue({
      ...INFO,
      container: { ...INFO.container, build: { ...INFO.container.build, dirty } },
    });
    await mount();

    expect(container.textContent).toContain(`Working tree${label}`);
  });

  it("degrades when Docker could not be reached", async () => {
    getApiServerInfo.mockResolvedValue({ ...INFO, docker_available: false, container: null });
    await mount();

    expect(container.textContent).toContain("Docker is not reachable");
    expect(container.textContent).toContain("1.0.1");
  });

  it("degrades for an API build without provenance labels", async () => {
    const older = Object.fromEntries(
      Object.entries(INFO.container).filter(([key]) => key !== "build" && key !== "image_created"),
    );
    getApiServerInfo.mockResolvedValue({ ...INFO, container: older });
    await mount();

    expect(container.textContent).toContain("not recorded in this image");
    expect(container.textContent).toContain("Image created (Docker)unknown");
  });

  it("asks only about the server the navbar points at", async () => {
    serverHolder.current = "beta";
    await mount();

    expect(getApiServerInfo).toHaveBeenCalledWith("beta");
    expect(getApiClientConfig).toHaveBeenCalledWith("beta");
    expect(getApiClientConfig).not.toHaveBeenCalledWith("alpha");
  });

  it("offers no panel and asks nothing when no server is selected", async () => {
    serverHolder.current = null;
    await mount();

    expect(container.textContent).toContain("Select a server in the top bar");
    expect(getApiServerInfo).not.toHaveBeenCalled();
    expect(getApiClientConfig).not.toHaveBeenCalled();
  });

  it("lets a trader see the defaults but not change them", async () => {
    ownerHolder.current = false;
    await mount();

    expect(field("Rate oracle source").textContent).toBe("gate_io");
    expect(field("Rate oracle source").disabled).toBe(true);
    expect(field("Global token").disabled).toBe(true);
    expect(field("Rate limit share (%)").disabled).toBe(true);
    expect(saveButton().disabled).toBe(true);
    expect(container.textContent).toContain("Only the server's owner");
  });

  it("offers the sources the server reported, not a list of its own", async () => {
    await mount();
    const options = await menuValues("Rate oracle source");

    expect(options).toEqual(["binance", "gate_io", "kucoin"]);
  });

  it("keeps a persisted source that the server no longer offers", async () => {
    // Otherwise the select would silently rewrite it to the first option on save.
    getApiClientConfig.mockResolvedValue({
      ...CONFIG,
      rate_oracle_source: { name: "retired_source" },
    });
    await mount();

    expect(field("Rate oracle source").textContent).toBe("retired_source");
  });

  it("cannot save until something has changed", async () => {
    await mount();
    expect(saveButton().disabled).toBe(true);
  });

  it("sends only the field that changed", async () => {
    await mount();
    await setValue("Global token", "USDC");
    await act(async () => {
      saveButton().click();
    });

    expect(updateApiClientConfig).toHaveBeenCalledWith("alpha", {
      global_token_name: "USDC",
    });
  });

  it("sends every field that changed, together", async () => {
    await mount();
    await setValue("Rate oracle source", "binance");
    await setValue("Rate limit share (%)", "40");
    await act(async () => {
      saveButton().click();
    });

    expect(updateApiClientConfig).toHaveBeenCalledWith("alpha", {
      rate_oracle_source: "binance",
      rate_limits_share_pct: 40,
    });
  });

  it("refuses a share outside hummingbot's own bound before sending it", async () => {
    await mount();
    await setValue("Rate limit share (%)", "150");

    expect(saveButton().disabled).toBe(true);
    expect(container.textContent).toContain("at most 100");
    expect(updateApiClientConfig).not.toHaveBeenCalled();
  });

  it("refuses an emptied global token rather than writing a blank one", async () => {
    await mount();
    await setValue("Global token", "   ");

    expect(saveButton().disabled).toBe(true);
    expect(updateApiClientConfig).not.toHaveBeenCalled();
  });

  it("does not carry a half-typed draft over to another server", async () => {
    // The draft is per-server state; leaking it would show one server's unsaved edit as
    // another server's current value, and save it there on the next click.
    getApiClientConfig.mockImplementation(async (s: string) =>
      s === "beta"
        ? { ...CONFIG, global_token: { global_token_name: "BETA", global_token_symbol: "B" } }
        : CONFIG,
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const render = async () => {
      await act(async () => {
        root.render(
          <QueryClientProvider client={client}>
            <ApiServerSettings />
          </QueryClientProvider>,
        );
      });
      for (let i = 0; i < 20; i++) {
        await act(async () => {
          await new Promise((resolve) => setTimeout(resolve, 5));
        });
      }
    };

    await render();
    await setValue("Global token", "TYPED");
    expect((field("Global token") as HTMLInputElement).value).toBe("TYPED");

    serverHolder.current = "beta";
    await render();

    expect((field("Global token") as HTMLInputElement).value).toBe("BETA");
    expect(saveButton().disabled).toBe(true);
  });

  it("tells the operator to deploy a newer build when the API predates the panel", async () => {
    const tooOld = Object.assign(
      new Error("This server's hummingbot-api is older than this panel."),
      { status: 501 },
    );
    getApiServerInfo.mockRejectedValue(tooOld);
    getApiClientConfig.mockRejectedValue(tooOld);
    await mount();

    expect(container.textContent).toContain("older than this panel");
    // Not styled as a failure: the server is fine, it is just behind.
    expect(container.querySelector(".border-red-500\\/30")).toBeNull();
  });

  it("shows a real upstream failure as a failure", async () => {
    getApiServerInfo.mockRejectedValue(
      Object.assign(new Error("Failed to fetch API server info: timed out"), {
        status: 502,
      }),
    );
    await mount();

    expect(container.textContent).toContain("timed out");
    expect(container.querySelector(".border-red-500\\/30")).not.toBeNull();
  });
});
