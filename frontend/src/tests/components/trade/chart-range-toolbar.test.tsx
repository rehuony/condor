/**
 * @vitest-environment jsdom
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChartRangeToolbar } from "@/components/trade/ChartRangeToolbar";

const RANGES = [
  { label: "1d", seconds: 86400 },
  { label: "3d", seconds: 3 * 86400 },
];

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(props: Partial<Parameters<typeof ChartRangeToolbar>[0]> = {}) {
  const handlers = { onIntervalChange: vi.fn(), onRangeChange: vi.fn() };
  act(() =>
    root.render(
      <ChartRangeToolbar
        intervals={["1m", "5m"]}
        interval="5m"
        ranges={RANGES}
        rangeSeconds={86400}
        isCustomRange={false}
        {...handlers}
        {...props}
      />,
    ),
  );
  return handlers;
}

const pill = (group: string, label: string) =>
  [...container.querySelectorAll(`[role="group"][aria-label="${group}"] button`)].find(
    (b) => b.textContent === label,
  ) as HTMLButtonElement;

async function choose(menu: string, option: string) {
  await act(async () => container.querySelector<HTMLButtonElement>(`button[aria-label="${menu}"]`)!.click());
  const item = [...document.querySelectorAll(`[role="listbox"][aria-label="${menu}"] [role="option"]`)]
    .find((o) => o.textContent === option) as HTMLElement;
  await act(async () => item.click());
}

describe("ChartRangeToolbar", () => {
  it("marks the active presets and reports picks from the segmented rows", () => {
    const { onIntervalChange, onRangeChange } = render();
    expect(pill("Candle interval", "5m").getAttribute("aria-pressed")).toBe("true");
    expect(pill("Chart range", "1d").getAttribute("aria-pressed")).toBe("true");
    act(() => pill("Candle interval", "1m").click());
    act(() => pill("Chart range", "3d").click());
    expect(onIntervalChange).toHaveBeenCalledWith("1m");
    expect(onRangeChange).toHaveBeenCalledWith(3 * 86400);
  });

  it("reports picks from the compact menus", async () => {
    const { onIntervalChange, onRangeChange } = render();
    await choose("Candle interval", "1m");
    await choose("Chart range", "3d");
    expect(onIntervalChange).toHaveBeenCalledWith("1m");
    expect(onRangeChange).toHaveBeenCalledWith(3 * 86400);
  });

  it("names a custom window without offering it as a preset to commit", async () => {
    const { onRangeChange } = render({ isCustomRange: true });
    expect(container.querySelector('button[aria-label="Chart range"]')!.textContent).toBe("Custom");
    expect(pill("Chart range", "1d").getAttribute("aria-pressed")).toBe("false");
    await choose("Chart range", "Custom");
    expect(onRangeChange).not.toHaveBeenCalled();
  });
});
