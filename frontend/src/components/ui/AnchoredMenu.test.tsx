/**
 * The focus contract of the portalled menu (CORR-216).
 *
 * Portalling the panel to `document.body` (ARCH-204) severed tab order from
 * visual order: Tab from an open menu walked into the page behind it and the
 * options were unreachable from the keyboard. The fix lives in the primitive,
 * so every consumer inherits it — and so a regression here would break nine
 * dropdowns at once, which is exactly what these cases are here to catch.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ScheduleDropdown } from "@/components/routines/ScheduleDropdown";
import { SelectField } from "@/components/ui/select-field";
import { AnchoredMenu } from "./AnchoredMenu";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

/** A trigger plus the menu that hangs off it, exactly as consumers wire it. */
function Harness({ open, children }: { open: boolean; children?: React.ReactNode }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  return (
    <>
      <button ref={setAnchor} id="trigger">
        Open
      </button>
      <button id="next">Next control on the page</button>
      <AnchoredMenu anchor={anchor} open={open} onClose={() => {}} role="listbox">
        {children ?? (
          <button id="option" role="option" aria-selected={false}>
            Option
          </button>
        )}
      </AnchoredMenu>
    </>
  );
}

let container: HTMLDivElement;
let root: Root;

function render(open: boolean, children?: React.ReactNode) {
  act(() => {
    root.render(<Harness open={open}>{children}</Harness>);
  });
}

const panel = () => document.querySelector<HTMLElement>('[role="listbox"]');
const el = (id: string) => document.querySelector<HTMLElement>(`#${id}`)!;

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

describe("AnchoredMenu focus management", () => {
  it("moves focus into the panel on open, so Tab walks the options", () => {
    render(false);
    expect(panel()).toBeNull();

    render(true);
    const menu = panel()!;
    expect(document.activeElement).toBe(menu);
    // -1 keeps the panel out of the tab order itself while still letting Tab
    // step from it into the options it contains.
    expect(menu.tabIndex).toBe(-1);
    expect(menu.contains(el("option"))).toBe(true);
  });

  it("returns focus to the trigger on close", () => {
    render(true);
    expect(document.activeElement).toBe(panel());

    render(false);
    expect(panel()).toBeNull();
    expect(document.activeElement).toBe(el("trigger"));
  });

  it("keeps search unfocused until the user enters it", () => {
    render(true, <input id="search" placeholder="Filter…" />);
    expect(document.activeElement).toBe(panel());
    act(() => el("search").focus());
    expect(document.activeElement).toBe(el("search"));
  });

  it("closes a schedule portal without sending Escape to the containing dialog", () => {
    const schedule = vi.fn();
    const outer = vi.fn();
    window.addEventListener("keydown", outer);
    act(() => root.render(<ScheduleDropdown onSchedule={schedule} />));
    const trigger = container.querySelector("button")!;
    act(() => trigger.click());
    const menu = document.querySelector<HTMLElement>('[role="menu"]')!;
    expect(container.contains(menu)).toBe(false);
    expect(document.activeElement).toBe(menu);
    act(() => menu.dispatchEvent(new KeyboardEvent("keydown", {key:"ArrowDown", bubbles:true})));
    expect(document.activeElement?.textContent).toBe("Every 1m");
    act(() => menu.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape", bubbles:true})));
    expect(document.querySelector('[role="menu"]')).toBeNull();
    expect(outer).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(trigger);
    act(() => trigger.click());
    const option = Array.from(document.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')).find(el => el.textContent === "Every 1w")!;
    act(() => option.click());
    expect(schedule).toHaveBeenCalledExactlyOnceWith(604800);
    expect(document.querySelector('[role="menu"]')).toBeNull();
    window.removeEventListener("keydown", outer);
  });

  it("does not yank focus back from a control the user clicked outside", () => {
    render(true);
    el("next").focus();

    render(false);
    expect(document.activeElement).toBe(el("next"));
  });
});


describe("SelectField interaction", () => {
  it("focuses an enabled alternative when the selected server is offline", () => {
    const onChange = vi.fn();
    act(() => root.render(<SelectField label="Server" value="offline" onChange={onChange} options={[
      { value: "", label: "Auto" },
      { value: "offline", label: "Offline server", disabled: true },
      { value: "online", label: "Online server" },
    ]} />));
    act(() => container.querySelector<HTMLButtonElement>("button")!.click());
    expect(document.activeElement?.textContent).toBe("Auto");
    act(() => document.activeElement!.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })));
    expect(document.activeElement?.textContent).toBe("Online server");
    act(() => (document.activeElement as HTMLButtonElement).click());
    expect(onChange).toHaveBeenCalledExactlyOnceWith("online");
  });

  it("opens on the selected option, moves with keys, commits and restores focus", () => {
    function Field() {
      const [value, setValue] = useState("a");
      return <SelectField label="Section" value={value} onChange={setValue} options={[
        { value: "a", label: "First", group: "Group" }, { value: "b", label: "Second", group: "Group" },
      ]} />;
    }
    act(() => root.render(<Field />));
    const trigger = container.querySelector("button")!;
    act(() => trigger.click());
    expect(document.activeElement?.textContent).toBe("First");
    act(() => document.activeElement!.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })));
    expect(document.activeElement?.textContent).toBe("Second");
    act(() => (document.activeElement as HTMLButtonElement).click());
    expect(trigger.textContent).toBe("Second");
    expect(document.activeElement).toBe(trigger);
    expect(panel()).toBeNull();
    act(() => trigger.click());
    expect(document.activeElement?.textContent).toBe("Second");
    act(() => document.activeElement!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(panel()).toBeNull();
    expect(trigger.textContent).toBe("Second");
  });
});


it("keeps an exact-width menu inside the visual viewport as the keyboard resizes it", () => {
  const anchor = document.createElement("button");
  document.body.append(anchor);
  vi.spyOn(anchor, "getBoundingClientRect").mockReturnValue({ left: 250, right: 450, top: 100, bottom: 140, width: 200, height: 40 } as DOMRect);
  const viewport = Object.assign(new EventTarget(), { width: 320, height: 600, offsetTop: 0, offsetLeft: 0 });
  vi.stubGlobal("visualViewport", viewport);
  try {
    act(() => root.render(<AnchoredMenu anchor={anchor} open onClose={() => {}} role="listbox" matchAnchorWidth="exact"><button>Option</button></AnchoredMenu>));
    expect(panel()!.style.width).toBe("200px");
    expect(panel()!.style.left).toBe("112px");
    expect(panel()!.style.maxHeight).toBe("446px");
    act(() => { viewport.height = 300; viewport.dispatchEvent(new Event("resize")); });
    expect(panel()!.style.maxHeight).toBe("146px");
  } finally {
    act(() => root.render(null));
    anchor.remove();
    vi.unstubAllGlobals();
  }
});


it("dismisses when a responsive ancestor hides the trigger and can reopen", () => {
  act(() => root.render(<SelectField label="Section" value="a" onChange={() => {}}
    options={[{ value: "a", label: "First" }]} />));
  const trigger = container.querySelector("button")!;
  act(() => trigger.click());
  expect(panel()).not.toBeNull();
  const focus = vi.spyOn(trigger, "focus");
  act(() => {
    container.style.display = "none";
    window.dispatchEvent(new Event("resize"));
  });
  expect(panel()).toBeNull();
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(focus).not.toHaveBeenCalled();
  act(() => {
    container.style.display = "block";
    window.dispatchEvent(new Event("resize"));
    trigger.click();
  });
  expect(panel()).not.toBeNull();
  expect(document.activeElement?.textContent).toBe("First");
});

it("keeps a readable width for a compressed trigger without overflowing the viewport", () => {
  const anchor = document.createElement("button");
  document.body.append(anchor);
  vi.spyOn(anchor, "getBoundingClientRect").mockReturnValue({ left: 290, right: 310, top: 80, bottom: 120, width: 20, height: 40 } as DOMRect);
  const viewport = Object.assign(new EventTarget(), { width: 320, height: 600, offsetTop: 0, offsetLeft: 0 });
  vi.stubGlobal("visualViewport", viewport);
  try {
    act(() => root.render(<AnchoredMenu anchor={anchor} open onClose={() => {}} role="listbox" matchAnchorWidth="exact" minWidth={280}><button>Routine</button></AnchoredMenu>));
    expect(panel()!.style.width).toBe("280px");
    expect(panel()!.style.left).toBe("32px");
    act(() => { viewport.width = 240; viewport.dispatchEvent(new Event("resize")); });
    expect(panel()!.style.width).toBe("224px");
    expect(panel()!.style.left).toBe("8px");
  } finally {
    act(() => root.render(null));
    anchor.remove();
    vi.unstubAllGlobals();
  }
});

it("uses the sheet's available width and follows boundary and viewport resizing", () => {
  const boundary = document.createElement("div");
  const anchor = document.createElement("button");
  boundary.append(anchor);
  document.body.append(boundary);
  vi.spyOn(anchor, "getBoundingClientRect").mockReturnValue({ left: 28, right: 168, top: 80, bottom: 120, width: 140, height: 40 } as DOMRect);
  const boundaryRect = vi.spyOn(boundary, "getBoundingClientRect").mockReturnValue({ left: 16, right: 374, width: 358 } as DOMRect);
  const viewport = Object.assign(new EventTarget(), { width: 390, height: 600, offsetTop: 0, offsetLeft: 0 });
  let resizeBoundary: (() => void) | undefined;
  vi.stubGlobal("visualViewport", viewport);
  vi.stubGlobal("ResizeObserver", class {
    callback: () => void;
    constructor(callback: () => void) { this.callback = callback; }
    observe(target: Element) { if (target === boundary) resizeBoundary = this.callback; }
    disconnect() {}
  });
  try {
    act(() => root.render(
      <AnchoredMenu anchor={anchor} horizontalBoundary={boundary} open onClose={() => {}} role="listbox" matchAnchorWidth="exact" minWidth={400}>
        <button>Routine</button>
      </AnchoredMenu>,
    ));
    expect(panel()!.style.width).toBe("342px");
    expect(panel()!.style.left).toBe("24px");

    boundaryRect.mockReturnValue({ left: 16, right: 314, width: 298 } as DOMRect);
    act(() => resizeBoundary?.());
    expect(panel()!.style.width).toBe("282px");
    expect(panel()!.style.left).toBe("24px");

    act(() => {
      viewport.width = 240;
      viewport.offsetLeft = 40;
      viewport.dispatchEvent(new Event("resize"));
    });
    expect(panel()!.style.width).toBe("224px");
    expect(panel()!.style.left).toBe("48px");
  } finally {
    act(() => root.render(null));
    boundary.remove();
    vi.unstubAllGlobals();
  }
});
