/**
 * The Hummingbot API section on Settings → Updates.
 *
 * * **The check contacts GitHub only when asked.** Mounting, focus and switching servers
 *   never fire it, and an answer belongs to the server it was asked about.
 * * **A 501 is not a red error.** The server is healthy and older than this card, so the
 *   operator's action is "deploy a newer build", not "find the outage".
 * * **Condor only checks.** The manual update steps are always there to read.
 * * **One line, like Condor's card.** The badge carries the verdict; the API's reason is
 *   shown only when the badge alone does not explain it.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const checkApiUpdate = vi.fn();
const getApiServerInfo = vi.fn();
const serverHolder = { current: "alpha" as string | null };

vi.mock("@/lib/api", () => ({
  errorStatus: (e: unknown) => (e as { status?: number } | null)?.status,
}));
vi.mock("@/lib/api/settings", () => ({
  settingsApi: {
    checkApiUpdate: (s: string) => checkApiUpdate(s),
    getApiServerInfo: (s: string) => getApiServerInfo(s),
  },
}));
vi.mock("@/hooks/useServer", () => ({
  useServer: () => ({ server: serverHolder.current }),
}));

const { ApiUpdateSection } = await import("@/components/settings/ApiUpdateSection");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const REVISION = "768e7c1f96fb1f21d91ff2313c0de0f8afa1cd3e";
const LATEST = "e899139394d27c6bfed099073e5933b721a80664";

function verdict(status: string, reason: string | null = null) {
  return {
    status,
    current_revision: REVISION,
    latest_revision: LATEST,
    checked_at: "2026-10-20T09:00:00Z",
    reason,
    compare_url: `https://github.com/rehuony/hummingbot-api/compare/${REVISION}...${LATEST}`,
  };
}

let container: HTMLDivElement;
let root: Root;

async function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ApiUpdateSection />
      </QueryClientProvider>,
    );
  });
  await click(document.body);
}

/** "Check for updates", "Check again", or "Retry check" after a failure. */
function checkButton(): HTMLButtonElement {
  const button = [...container.querySelectorAll("button")].find((b) => /check/i.test(b.textContent ?? ""));
  if (!button) throw new Error("no update-check button");
  return button as HTMLButtonElement;
}

async function click(el: Element) {
  await act(async () => {
    (el as HTMLElement).click();
  });
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
    });
  }
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  serverHolder.current = "alpha";
  checkApiUpdate.mockReset().mockResolvedValue(verdict("up_to_date", "The running source matches main."));
  getApiServerInfo.mockReset().mockResolvedValue({ container: { build: { branch: "main" } } });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("ApiUpdateSection", () => {
  it("never checks for updates on its own", async () => {
    await mount();
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(checkApiUpdate).not.toHaveBeenCalled();
    expect(container.textContent).toContain("make check-update");
    expect(container.textContent).toContain("make deploy");
  });

  it("shows the bare commit when the image does not record its branch", async () => {
    getApiServerInfo.mockResolvedValue({ container: { build: null } });
    await mount();
    await click(checkButton());

    expect(container.textContent).toContain("repo768e7c1f");
    expect(container.textContent).not.toContain("@768e7c1f");
  });

  it("checks once per press, for the selected server", async () => {
    let answer: (value: unknown) => void = () => {};
    checkApiUpdate.mockImplementation(() => new Promise((resolve) => (answer = resolve)));
    await mount();

    await click(checkButton());
    expect(checkButton().disabled).toBe(true);
    await click(checkButton());
    expect(checkApiUpdate).toHaveBeenCalledTimes(1);
    expect(checkApiUpdate).toHaveBeenCalledWith("alpha");

    await act(async () => answer(verdict("update_available", "main has newer changes.")));
    await click(document.body);
    expect(container.textContent).toContain("Update available");
    // branch@commit on both sides, eight digits each, like Condor's own card,
    // with the comparison one click away.
    expect(container.textContent).toContain("main@768e7c1f→main@e8991393");
    expect(container.querySelector(`a[href*="/compare/"]`)).not.toBeNull();
  });

  it.each([
    ["up_to_date", "Up to date"],
    ["update_available", "Update available"],
    ["ahead", "Ahead of main"],
    ["diverged", "Diverged from main"],
    ["unknown", "Cannot compare"],
    ["error", "Check failed"],
  ])("labels %s distinctly", async (status, label) => {
    checkApiUpdate.mockResolvedValue(verdict(status, `because ${status}`));
    const explained = status !== "up_to_date" && status !== "update_available";
    await mount();
    await click(checkButton());

    expect(container.textContent).toContain(label);
    if (explained) expect(container.textContent).toContain(`because ${status}`);
    else expect(container.textContent).not.toContain(`because ${status}`);
  });

  it("offers a retry when the API reports a failed check", async () => {
    checkApiUpdate.mockResolvedValue({
      ...verdict("error", "GitHub rate limit reached; try the update check later."),
      latest_revision: null,
      compare_url: null,
    });
    await mount();
    await click(checkButton());

    expect(container.textContent).toContain("GitHub rate limit reached");
    expect(checkButton().textContent).toContain("Retry check");
    await click(checkButton());
    expect(checkApiUpdate).toHaveBeenCalledTimes(2);
  });

  it("says a server without the route cannot check yet, not that it is down", async () => {
    checkApiUpdate.mockRejectedValue(
      Object.assign(new Error("This server's hummingbot-api cannot check its source for updates yet."), {
        status: 501,
      }),
    );
    await mount();
    await click(checkButton());

    expect(container.textContent).toContain("cannot check its source for updates yet");
    expect(container.querySelector(".border-red-500\\/30")).toBeNull();
  });

  it("shows an unreachable server as a failure with a retry", async () => {
    checkApiUpdate.mockRejectedValue(
      Object.assign(new Error("Failed to check for updates: connection refused"), { status: 502 }),
    );
    await mount();
    await click(checkButton());

    expect(container.textContent).toContain("connection refused");
    expect(container.querySelector(".border-red-500\\/30")).not.toBeNull();
    expect(checkButton().textContent).toContain("Retry check");
  });

  it("keeps each server's result to itself, even when it arrives late", async () => {
    let answerAlpha: (value: unknown) => void = () => {};
    checkApiUpdate.mockImplementation((s: string) =>
      s === "alpha"
        ? new Promise((resolve) => (answerAlpha = resolve))
        : Promise.resolve(verdict("up_to_date")),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const render = async () => {
      await act(async () => {
        root.render(
          <QueryClientProvider client={client}>
            <ApiUpdateSection />
          </QueryClientProvider>,
        );
      });
      await click(document.body);
    };

    await render();
    await click(checkButton());

    serverHolder.current = "beta";
    await render();
    // Switching does not check beta on its own, and alpha's pending check is not beta's.
    expect(checkApiUpdate).toHaveBeenCalledTimes(1);
    expect(checkButton().disabled).toBe(false);

    await act(async () => answerAlpha(verdict("update_available", "alpha is behind")));
    await click(document.body);
    expect(container.textContent).not.toContain("Update available");
    expect(container.textContent).toContain("Not checked");

    serverHolder.current = "alpha";
    await render();
    expect(container.textContent).toContain("Update available");
  });

  it("asks for a server instead of offering a check without one", async () => {
    serverHolder.current = null;
    await mount();

    expect(container.textContent).toContain("Select a server");
    expect([...container.querySelectorAll("button")].some((b) => /check/i.test(b.textContent ?? ""))).toBe(false);
    expect(container.textContent).toContain("make deploy");
  });
});
