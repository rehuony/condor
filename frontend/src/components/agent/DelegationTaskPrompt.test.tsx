/**
 * The task's ask yields height to its result: two lines until expanded.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DelegationTaskPrompt } from "./DelegationTaskPrompt";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let notify: (() => void) | null = null;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: () => void) { notify = callback; }
      observe() {}
      disconnect() {}
    },
  );
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  notify = null;
});

function show(text: string, scrollHeight: number): HTMLParagraphElement {
  act(() => root.render(<DelegationTaskPrompt text={text} />));
  const paragraph = container.querySelector("p")!;
  Object.defineProperty(paragraph, "scrollHeight", { configurable: true, value: scrollHeight });
  Object.defineProperty(paragraph, "clientHeight", { configurable: true, value: 40 });
  act(() => notify?.());
  return paragraph;
}

const toggle = () => container.querySelector("button");

describe("DelegationTaskPrompt", () => {
  it("keeps a short ask at full width without a toggle", () => {
    const paragraph = show("count the open positions", 40);
    expect(paragraph.className).toContain("line-clamp-2");
    expect(toggle()).toBeNull();
  });

  it("expands and collapses a long ask", () => {
    const paragraph = show("long ask ".repeat(80), 200);
    expect(toggle()?.textContent).toBe("…\u00a0Show more");

    act(() => toggle()!.click());
    expect(paragraph.className).not.toContain("line-clamp-2");
    expect(paragraph.className).toContain("max-h-[35dvh]");
    expect(toggle()?.textContent).toBe("Show less");
    expect(toggle()?.getAttribute("aria-expanded")).toBe("true");

    act(() => toggle()!.click());
    expect(paragraph.className).toContain("line-clamp-2");
    expect(toggle()?.textContent).toBe("…\u00a0Show more");
  });
});
