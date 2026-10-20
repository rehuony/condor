/**
 * The routine library, hosted in the workspace pane (FEAT-077).
 *
 * Full screen the browser *is* the window, and everything in it may assume so.
 * Beside a live conversation none of that holds: the pane sizes it, the sheet
 * closes it, and a keystroke meant for the composer must never reach it. These
 * cases pin the three properties that make hosting safe — no viewport-owning
 * root, no window key listener, no second Close button — and the one that makes
 * it useful: a run launched from the pane goes to the conversation's server,
 * carrying the session that asked for it.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";
import type { RoutineHooks, RoutineInfo } from "@/lib/api/routines";
import { formatSlug, saveConfig } from "@/lib/routineUtils";

const getRoutines = vi.fn();
const getRoutineReports = vi.fn();
const runRoutine = vi.fn();
const scheduleRoutine = vi.fn();
const deleteReport = vi.fn();

vi.mock("@/lib/api/reports", () => ({
  reportsApi: {
    getReportsGrouped: () => Promise.resolve([]),
    deleteReport: (...a: unknown[]) => deleteReport(...a),
    getReportHtml: () => Promise.resolve(""),
  },
}));
vi.mock("@/lib/api/routines", () => ({
  routinesApi: {
    getRoutines: (...a: unknown[]) => getRoutines(...a),
    getRoutineReports: (...a: unknown[]) => getRoutineReports(...a),
    getRoutineFieldOptions: () => Promise.resolve({ options: [] }),
    getRoutineSource: () => Promise.resolve({ filename: "x.py", source: "" }),
    getRoutineInstance: () => Promise.resolve(null),
    runRoutine: (...a: unknown[]) => runRoutine(...a),
    scheduleRoutine: (...a: unknown[]) => scheduleRoutine(...a),
    getRoutineHooks: (): Promise<RoutineHooks> => Promise.resolve({
      telegram: { enabled: false, chat_ids: [] },
      trigger: "success",
    }),
  },
}));

// Read at module scope by the theme hook the report frame pulls in; jsdom has
// no implementation, so it has to exist before the import below.
window.matchMedia = ((media: string) => ({
  matches: false,
  media,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

// jsdom implements neither, and the browser calls both on mount.
Element.prototype.scrollIntoView = () => {};

const { ReportBrowser } = await import("@/components/routines/ReportBrowser");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function routine(name: string, owner?: string): RoutineInfo {
  return {
    name,
    description: name,
    is_continuous: false,
    category: "general",
    source: owner ? `agent:${owner}` : "routine",
    fields: {},
    last_modified: null,
    report_count: 0,
  };
}

let container: HTMLDivElement;
let root: Root;
let closes: number;

const RUN_CONTEXT = {
  serverName: "chat-server",
  sessionKey: "web:42:main",
  agentSlug: "scout",
};

async function render(props: {
  hosted?: boolean;
  page?: boolean;
  externalPicker?: boolean;
  runContext?: typeof RUN_CONTEXT;
  initialSource?: string;
  initialSourceTypeFilter?: string;
} = {}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ServerContext.Provider
          value={{ server: "dashboard-server", setServer: () => {} }}
        >
          <ReportBrowser
            instances={[]}
            onClose={() => {
              closes += 1;
            }}
            {...props}
          />
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  // The routine list lands a tick later, and the first source is chosen in an
  // effect off the back of it.
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
  return qc;
}

/** The browser's own root — the only child the tree renders. */
const browser = () => container.firstElementChild as HTMLElement;
const heading = () => container.querySelector("h2")?.textContent?.trim();
const button = (title: string) =>
  container.querySelector<HTMLButtonElement>(`button[title="${title}"]`);
const textButton = (text: string) =>
  Array.from(container.querySelectorAll("button")).find(
    (item) => item.textContent?.trim() === text,
  )!;
const library = () =>
  container.querySelector<HTMLElement>('[aria-label="Routine library"]')!;
const agentSelect = () =>
  container.querySelector<HTMLButtonElement>('button[aria-label="Routine agent"]')!;
const bulkRun = () =>
  button("Run all filtered routines with saved configs and defaults")!;
/** Run All asks first; this answers the dialog. */
async function confirmRunAll() {
  const dialog = document.querySelector('[role="dialog"]')!;
  expect(dialog.textContent).toMatch(/^Run \d+ routines\?/);
  await act(async () => {
    [...dialog.querySelectorAll("button")].find((b) => b.textContent === "Run all")!.click();
  });
}
const routineActions = () =>
  container.querySelector<HTMLElement>('[role="group"][aria-label="Routine actions"]')!;
const marketField = () => {
  const label = Array.from(container.querySelectorAll("label")).find(
    (item) => item.textContent?.trim() === "Market Symbol",
  );
  return label ? document.getElementById(label.htmlFor) as HTMLInputElement : null;
};
/** The toolbar's own theme toggle, removed in READ-274. */
const themeButton = () =>
  container.querySelector<HTMLButtonElement>('button[title^="Switch to "]');
/** The header row's agent badge — the sidebar rows carry one of their own. */
const agentBadge = () =>
  container
    .querySelector("h2")
    ?.parentElement?.querySelector<HTMLElement>("span.uppercase")
    ?.textContent?.trim() ?? null;
/** What the scope select is showing — the collapsed rail's fallback control. */
const scopeSelect = () =>
  container.querySelector<HTMLButtonElement>('button[aria-label="Routine scope"]');
/** Which of the sidebar's scope bubbles is lit, expanded (FEAT-091). */
const scopeBubble = () =>
  container
    .querySelector('[data-scope][aria-pressed="true"]')
    ?.getAttribute("data-scope") ?? null;

async function press(key: string, on: EventTarget) {
  await act(async () => {
    on.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  });
}

async function enterValue(input: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function chooseAgent(value: string) {
  await act(async () => agentSelect().click());
  const option = [...document.querySelectorAll<HTMLButtonElement>('[role="listbox"][aria-label="Routine agent"] [role="option"]')]
    .find((item) => item.textContent === formatSlug(value))!;
  await act(async () => option.click());
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  closes = 0;
  localStorage.clear();
  getRoutines.mockResolvedValue([routine("alpha_check"), routine("beta_check")]);
  getRoutineReports.mockResolvedValue({ reports: [] });
  runRoutine.mockResolvedValue({ instance_id: "i1" });
  deleteReport.mockResolvedValue({ deleted: true });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("ReportBrowser hosted in the pane", () => {
  it("fills its container instead of owning the viewport", async () => {
    await render({ hosted: true });

    expect(browser().className).not.toContain("fixed");
    expect(browser().className).not.toContain("inset-0");
    // The sidebar opens on its rail, which is what makes it fit at pane width.
    expect(container.querySelector('[class~="md:w-12"]')).toBeTruthy();
    expect(container.querySelector('[class~="md:w-64"]')).toBeNull();
  });

  it("leaves the window's keys to the conversation", async () => {
    await render({ hosted: true });
    expect(heading()).toBe("Alpha Check");

    // What the composer sees never reaches the report list…
    await press("ArrowDown", window);
    expect(heading()).toBe("Alpha Check");
    await press("Escape", window);
    expect(closes).toBe(0);

    // …but the browser still answers to keys aimed at itself.
    await press("ArrowDown", browser());
    expect(heading()).toBe("Beta Check");
  });

  it("leaves closing to the sheet", async () => {
    await render({ hosted: true });

    expect(button("Close (Esc)")).toBeNull();
    // Escape inside it is for its own panels, never for the pane.
    await press("Escape", browser());
    expect(closes).toBe(0);
  });

  it("leaves the theme to the dashboard", async () => {
    await render({ hosted: true });

    // The report is a document inside the app, not a surface with a mode of its
    // own: there is no second, differently-shaped theme control in the toolbar.
    expect(themeButton()).toBeNull();
  });

  it("runs on the conversation's server, as the conversation", async () => {
    await render({ hosted: true, runContext: RUN_CONTEXT });

    await act(async () => {
      textButton("Run for the first time").click();
    });

    expect(runRoutine).toHaveBeenCalledWith(
      "chat-server",
      "alpha_check",
      {},
      { sessionKey: "web:42:main", attributeTo: "scout" },
    );
  });
});

describe("ReportBrowser on its own page", () => {
  it("still owns the viewport, its keys and its Close button", async () => {
    await render();

    expect(browser().className).toContain("fixed inset-0");
    expect(container.querySelector('[class~="md:w-64"]')).toBeTruthy();

    await press("ArrowDown", window);
    expect(heading()).toBe("Beta Check");
    await press("Escape", window);
    expect(closes).toBe(1);
    expect(button("Close (Esc)")).toBeTruthy();
  });

  it("leaves the theme to the dashboard here too", async () => {
    await render();

    expect(themeButton()).toBeNull();
  });

  it("runs on the dashboard's server, with no conversation behind it", async () => {
    await render();

    await act(async () => {
      textButton("Run for the first time").click();
    });

    expect(runRoutine).toHaveBeenCalledWith(
      "dashboard-server",
      "alpha_check",
      {},
      { sessionKey: undefined, attributeTo: undefined },
    );
  });
});

/**
 * READ-276: the header used to name the owning agent three times in one row —
 * the scope select, the `{agent}/` prefix the raw key carries into the `<h2>`,
 * and the badge. The prefix is gone from the title, and the badge appears only
 * where the select is not already naming that agent.
 */
describe("ReportBrowser header, for an agent-owned routine", () => {
  const AGENT_LIST = [
    routine("alpha_check"),
    routine("brigado/mm_regime_detector", "brigado"),
    routine("scout/depth_watch", "scout"),
  ];

  beforeEach(() => {
    getRoutines.mockResolvedValue(AGENT_LIST);
  });

  it("titles the routine without its owner's slug", async () => {
    await render({
      initialSource: "brigado/mm_regime_detector",
      initialSourceTypeFilter: "brigado",
    });

    // The sheet above this pane titles it the same way (DockRoutines).
    expect(heading()).toBe("Mm Regime Detector");
  });

  it("drops the badge when the scope already names that agent", async () => {
    await render({
      initialSource: "brigado/mm_regime_detector",
      initialSourceTypeFilter: "brigado",
    });

    // The primary scope stays Agents while its second-level select names the
    // owner. The collapsed rail keeps the header's combined scope picker.
    expect(scopeSelect()).toBeNull();
    expect(scopeBubble()).toBe("agent");
    expect(agentSelect().textContent).toBe(formatSlug("brigado"));
    expect(agentBadge()).toBeNull();
  });

  it("keeps the badge when the list is mixed", async () => {
    await render({
      initialSource: "brigado/mm_regime_detector",
      initialSourceTypeFilter: "all",
    });

    expect(agentBadge()).toBe("Brigado");
  });

  it("keeps the badge when the pane widens itself past the picked scope", async () => {
    // Focused on brigado's routine while the picker says scout: the pane shows
    // "All routines" rather than hide what the reader clicked, so the badge is
    // the only thing naming the owner.
    await render({
      initialSource: "brigado/mm_regime_detector",
      initialSourceTypeFilter: "scout",
    });

    expect(scopeBubble()).toBe("all");
    expect(agentBadge()).toBe("Brigado");
  });

  it("shows no badge for a library routine", async () => {
    await render({ initialSource: "alpha_check", initialSourceTypeFilter: "all" });

    expect(heading()).toBe("Alpha Check");
    expect(agentBadge()).toBeNull();
  });

  it("names the routine and its owner separately in sidebar rows", async () => {
    await render({ initialSourceTypeFilter: "all" });

    const row = Array.from(library().querySelectorAll("button")).find(
      (item) => Array.from(item.querySelectorAll("span")).some(
        (span) => span.textContent === "Mm Regime Detector",
      ),
    )!;
    expect(row).toBeTruthy();
    expect(Array.from(row.querySelectorAll("span")).some(
      (span) => span.textContent === "Brigado",
    )).toBe(true);
    expect(row.textContent).not.toContain("brigado/mm regime detector");
  });
});

describe("ReportBrowser library actions", () => {
  it("uses the same scope and readable-name search for rows, keyboard navigation and Run All", async () => {
    const routines = [
      routine("price_watch"),
      routine("scout/price_watch", "scout"),
      routine("scout/price_watch_daily", "scout"),
      routine("scout/depth_scan", "scout"),
      routine("brigado/price_watch", "brigado"),
    ].map((item) => ({
      ...item,
      description: "Monitors current market conditions.",
      fields: {
        market_symbol: { type: "str", default: "BTC-USDT", description: "Market to inspect" },
      },
    }));
    getRoutines.mockResolvedValue(routines);
    saveConfig("scout/price_watch", { market_symbol: "ETH-USDT" });
    await render({ page: true });

    await act(async () => {
      library().querySelector<HTMLButtonElement>('[data-scope="agent"]')!.click();
    });
    await chooseAgent("scout");
    const search = library().querySelector<HTMLInputElement>('input[placeholder="Search routines…"]')!;
    await enterValue(search, "Price Watch");

    expect(scopeBubble()).toBe("agent");
    expect(heading()).toBe("Price Watch");
    expect(library().textContent).toContain("Price Watch Daily");
    expect(library().textContent).not.toContain("Depth Scan");
    expect(library().textContent).not.toContain("No reports yet");
    await press("ArrowDown", window);
    expect(heading()).toBe("Price Watch Daily");
    await press("ArrowDown", window);
    expect(heading()).toBe("Price Watch Daily");

    // The page draws Run All in the library header; the toolbar copy is for phones.
    expect(library().contains(bulkRun())).toBe(true);
    await act(async () => bulkRun().click());
    expect(runRoutine).not.toHaveBeenCalled();
    await confirmRunAll();
    expect(runRoutine.mock.calls.map((call) => [call[1], call[2]])).toEqual([
      ["scout/price_watch", { market_symbol: "ETH-USDT" }],
      ["scout/price_watch_daily", { market_symbol: "BTC-USDT" }],
    ]);

    // Existing raw names still find the same rows after formatting the labels.
    await enterValue(search, "scout/price_watch");
    await press("ArrowUp", window);
    expect(heading()).toBe("Price Watch");
  });

  it.each([
    { name: "standalone view", props: {}, toolbarRunAll: false },
    { name: "routines page", props: { page: true }, toolbarRunAll: true },
    { name: "hosted library", props: { hosted: true }, toolbarRunAll: false },
    { name: "hosted picker", props: { hosted: true, externalPicker: true }, toolbarRunAll: true },
  ])("keeps the same command order in the $name", async ({ props, toolbarRunAll }) => {
    getRoutineReports.mockResolvedValue({
      reports: [{ id: "r1", title: "Latest analysis", filename: "r1.html", created_at: "2026-10-17T04:00:00Z" }],
    });
    await render(props);

    const labels = ["Run", "Config", "Schedule", "Source", "Notify", ...(toolbarRunAll ? ["Run All"] : [])];
    const actions = [...routineActions().querySelectorAll("button")].filter(
      (action) => action.getAttribute("aria-label") !== "More routine actions",
    );
    expect(actions.map((action) => action.textContent?.trim())).toEqual(labels);
    expect([...container.querySelectorAll("button")].filter(
      (action) => labels.includes(action.textContent?.trim() ?? "") && !library()?.contains(action),
    )).toEqual(actions);
    // Wherever the library is drawn, Run All lives in its header.
    if (library()) expect(library().contains(bulkRun())).toBe(true);
  });

  it("keeps secondary actions ordered and runs the scoped routines from More", async () => {
    getRoutines.mockResolvedValue([
      routine("alpha_check"),
      routine("scout/price_watch", "scout"),
      routine("scout/depth_scan", "scout"),
    ]);
    await render({ hosted: true, externalPicker: true, initialSourceTypeFilter: "scout", runContext: RUN_CONTEXT });
    const more = routineActions().querySelector<HTMLButtonElement>('[aria-label="More routine actions"]')!;
    await act(async () => more.click());
    const menu = document.querySelector("[data-routine-more-panel]")!;
    const actions = [...menu.querySelectorAll("button")];
    expect(actions.map((action) => action.textContent?.trim())).toEqual(["Source", "Notify", "Run All"]);
    await act(async () => actions[2].click());
    expect(document.querySelector("[data-routine-more-panel]")).toBeNull();
    await confirmRunAll();
    expect(runRoutine.mock.calls.map((call) => call[1])).toEqual(["scout/price_watch", "scout/depth_scan"]);
    expect(runRoutine.mock.calls.every((call) => call[0] === "chat-server")).toBe(true);
  });

  it("keeps Run All reachable when the host owns the picker", async () => {
    await render({ hosted: true, externalPicker: true, runContext: RUN_CONTEXT });

    expect(library()).toBeNull();
    expect(bulkRun()).toBeTruthy();
    await act(async () => bulkRun().click());
    await confirmRunAll();
    expect(runRoutine.mock.calls.map((call) => call[1])).toEqual(["alpha_check", "beta_check"]);
    for (const call of runRoutine.mock.calls) {
      expect(call[0]).toBe("chat-server");
      expect(call[3]).toEqual({ sessionKey: "web:42:main", attributeTo: "scout" });
    }
  });
});

describe("ReportBrowser report deletion", () => {
  it("keeps the confirmed report when a refresh prepends a newer report", async () => {
    const report = (id: string) => ({ id, title: id, filename: `${id}.html`, created_at: "2026-10-17T04:00:00Z" });
    getRoutineReports.mockResolvedValue({ reports: [report("original")], total: 1 });
    const qc = await render({ page: true, initialSource: "alpha_check" });
    await act(async () => button("Delete report")!.click());
    await act(async () => {
      qc.setQueryData(["routine-reports", "alpha_check"], { reports: [report("newest"), report("original")], total: 2 });
    });
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).toContain("original");
    expect(dialog.textContent).not.toContain("newest");
    await act(async () => {
      [...dialog.querySelectorAll("button")].find((item) => item.textContent === "Delete")!.click();
    });
    expect(deleteReport).toHaveBeenCalledExactlyOnceWith("original");
  });

  it.each([
    { mode: "page", all: false },
    { mode: "page", all: true },
    { mode: "hosted", all: false },
    { mode: "hosted", all: true },
  ])("keeps the deletion target while a $mode confirmation is open (all=$all)", async ({ mode, all }) => {
    const stored: Record<string, string[]> = {
      alpha_check: ["alpha-first", "alpha-second"],
      beta_check: ["beta-first"],
    };
    getRoutineReports.mockImplementation(async (source: string) => ({
      reports: stored[source].map((id) => ({ id, title: id, filename: `${id}.html`, created_at: "2026-10-17T04:00:00Z" })),
      total: stored[source].length,
    }));
    deleteReport.mockImplementation(async (id: string) => {
      for (const source of Object.keys(stored)) stored[source] = stored[source].filter((item) => item !== id);
      return { deleted: true };
    });
    await render({ page: mode === "page", hosted: mode === "hosted", initialSource: "alpha_check" });
    await act(async () => button(all ? "Delete all 2 reports from this routine" : "Delete report")!.click());
    const dialog = document.querySelector('[role="dialog"]')!;
    const cancel = dialog.querySelector("button")!;

    await press("ArrowRight", cancel);
    await press("ArrowDown", cancel);
    expect(heading()).toBe("Alpha Check");
    expect(container.querySelector('iframe')?.title).toBe("alpha-first");
    await act(async () => {
      [...dialog.querySelectorAll("button")].find((item) => item.textContent === (all ? "Delete all" : "Delete"))!.click();
    });
    expect(deleteReport.mock.calls.map(([id]) => id)).toEqual(all ? ["alpha-first", "alpha-second"] : ["alpha-first"]);
    expect(stored.beta_check).toEqual(["beta-first"]);
  });

  it("clears a routine's library count once all its reports are deleted", async () => {
    let stored = ["r1", "r2"];
    const report = (id: string) => ({ id, title: id, filename: `${id}.html`, created_at: "2026-10-17T04:00:00Z" });
    // The grouped summary is empty here, so the card reads the routine list's count.
    getRoutines.mockImplementation(async () => [{ ...routine("alpha_check"), report_count: stored.length }, routine("beta_check")]);
    getRoutineReports.mockImplementation(async () => ({ reports: stored.map(report), total: stored.length }));
    deleteReport.mockImplementation(async (id: string) => {
      stored = stored.filter((item) => item !== id);
      return { deleted: true };
    });
    await render({ page: true, initialSource: "alpha_check" });
    expect(library().querySelector('[title="2 reports"]')).not.toBeNull();

    await act(async () => button("Delete all 2 reports from this routine")!.click());
    const dialog = document.querySelector('[role="dialog"]')!;
    await act(async () => {
      [...dialog.querySelectorAll("button")].find((b) => b.textContent === "Delete all")!.click();
    });
    for (let i = 0; i < 4; i++) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    }
    expect(stored).toEqual([]);
    expect(library().querySelector('[title$=" reports"]')).toBeNull();
  });
});

describe("ReportBrowser configuration", () => {
  beforeEach(() => {
    getRoutines.mockResolvedValue([
      {
        ...routine("alpha_check"),
        fields: {
          market_symbol: { type: "str", default: "BTC-USDT", description: "Market to inspect" },
        },
      },
      routine("beta_check"),
    ]);
  });

  it("keeps one initial configuration and preserves edits through panels and routine navigation", async () => {
    await render({ page: true });
    const fields = () => Array.from(container.querySelectorAll("label")).filter(
      (label) => label.textContent?.trim() === "Market Symbol",
    );
    expect(fields()).toHaveLength(1);
    // Run is always offered, even before the first report.
    expect(button("Run with current config")).toBeTruthy();
    await enterValue(marketField()!, "ETH-USDT");

    await act(async () => button("Configure & Run")!.click());
    expect(fields()).toHaveLength(1);
    expect(marketField()?.value).toBe("ETH-USDT");
    await act(async () => button("Post-run notifications")!.click());
    await act(async () => button("Configure & Run")!.click());
    expect(fields()).toHaveLength(1);
    expect(marketField()?.value).toBe("ETH-USDT");

    await press("ArrowDown", window);
    expect(heading()).toBe("Beta Check");
    await press("ArrowUp", window);
    expect(marketField()?.value).toBe("ETH-USDT");
    await act(async () => textButton("Run for the first time").click());
    expect(runRoutine).toHaveBeenCalledWith(
      "dashboard-server",
      "alpha_check",
      { market_symbol: "ETH-USDT" },
      { sessionKey: undefined, attributeTo: undefined },
    );
  });

  it("restores saved configuration when reopening the report's Config panel", async () => {
    saveConfig("alpha_check", { market_symbol: "SOL-USDT" });
    getRoutineReports.mockResolvedValue({
      reports: [{ id: "r1", title: "Latest analysis", filename: "r1.html", created_at: "2026-10-17T04:00:00Z" }],
    });
    await render({ page: true });

    expect(marketField()).toBeNull();
    expect(button("Run with current config")).toBeTruthy();
    await act(async () => button("Configure & Run")!.click());
    expect(marketField()?.value).toBe("SOL-USDT");
    await enterValue(marketField()!, "ETH-USDT");
    await act(async () => button("Post-run notifications")!.click());
    expect(marketField()).toBeNull();
    await act(async () => button("Configure & Run")!.click());
    expect(marketField()?.value).toBe("ETH-USDT");
    await act(async () => button("Run with current config")!.click());
    expect(runRoutine).toHaveBeenCalledWith(
      "dashboard-server",
      "alpha_check",
      { market_symbol: "ETH-USDT" },
      { sessionKey: undefined, attributeTo: undefined },
    );
  });
});

it("picks the scope inside the narrow routine menu without running anything", async () => {
  getRoutines.mockResolvedValue([
    routine("price_watch"),
    routine("scout/depth_scan", "scout"),
  ]);
  await render({ page: true, initialSource: "price_watch" });
  const bar = container.querySelector("[data-routine-picker-bar]")!;
  const trigger = bar.querySelector<HTMLButtonElement>('button[aria-label="Routine"]')!;
  await act(async () => trigger.click());
  const scopes = document.querySelector('[role="group"][aria-label="Routine scope"]')!;
  const chip = (label: string) =>
    [...scopes.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === label)!;
  expect(chip("All routines").getAttribute("aria-pressed")).toBe("true");
  expect(chip("Scout")).toBeUndefined();
  await act(async () => chip("Agents").click());
  expect(chip("Agents").getAttribute("aria-pressed")).toBe("true");
  await act(async () => chip("Scout").click());
  expect(chip("Agents").getAttribute("aria-pressed")).toBe("true");
  expect(chip("Scout").getAttribute("aria-pressed")).toBe("true");
  const rows = () => [...document.querySelectorAll("[data-routine-row]")].map((r) => r.textContent ?? "");
  expect(rows().some((r) => r.includes("Depth Scan"))).toBe(true);
  expect(rows().some((r) => r.includes("Price Watch"))).toBe(false);
  expect(runRoutine).not.toHaveBeenCalled();
  expect(scheduleRoutine).not.toHaveBeenCalled();
});

it("keeps the Condor scope apart from an agent that shares its name", async () => {
  getRoutines.mockResolvedValue([
    routine("price_watch"),
    routine("condor/chat_digest", "condor"),
    routine("scout/depth_scan", "scout"),
  ]);
  await render({ page: true, initialSource: "price_watch" });
  const trigger = container.querySelector<HTMLButtonElement>('[data-routine-picker-bar] button[aria-label="Routine"]')!;
  await act(async () => trigger.click());
  const scopes = document.querySelector('[role="group"][aria-label="Routine scope"]')!;
  const chip = (label: string) =>
    [...scopes.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === label);
  await act(async () => chip("Condor")!.click());
  expect(chip("Condor")!.getAttribute("aria-pressed")).toBe("true");
  expect(chip("Agents")!.getAttribute("aria-pressed")).toBe("false");
  expect(chip("Scout")).toBeUndefined();
  await act(async () => chip("Agents")!.click());
  expect(chip("Condor")!.getAttribute("aria-pressed")).toBe("false");
  expect([...scopes.querySelectorAll("button")].filter((b) => b.textContent === "Condor")).toHaveLength(1);
});


it("selects an older report through the shared listbox", async () => {
  getRoutineReports.mockResolvedValue({ reports: Array.from({ length: 12 }, (_, i) => ({
    id: `report-${i}`, title: `Report ${i}`, created_at: new Date(2026, 9, 19, 12 - i).toISOString(),
  })) });
  await render({ hosted: true, externalPicker: true, initialSource: "alpha_check" });
  const trigger = container.querySelector<HTMLButtonElement>('button[aria-label="+2 older reports"]')!;
  expect(trigger).toBeTruthy();
  await act(async () => { trigger.click(); });
  const options = document.querySelectorAll<HTMLButtonElement>('[role="option"]');
  expect(options).toHaveLength(2);
  await act(async () => { options[1].click(); });
  expect(document.querySelector('[role="listbox"]')).toBeNull();
  expect(trigger.textContent).toContain("Report 11");
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(container.querySelector('iframe')?.getAttribute("title")).toBe("Report 11");
  await act(async () => { trigger.click(); });
  expect(document.querySelector('[aria-selected="true"]')?.textContent).toContain("Report 11");
  await press("Escape", document.activeElement!);
  expect(document.querySelector('[role="listbox"]')).toBeNull();
  expect(closes).toBe(0);
});
