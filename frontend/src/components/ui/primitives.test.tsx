/**
 * The shared primitives that replaced the dashboard's duplicated markup:
 * the card surface, empty state, spinner, icon button, disclosure, field
 * chrome and table classes. Each guarantee here used to be re-made by hand
 * in a dozen files and had started to drift.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Card, CARD_CLASS } from "./Card";
import { Disclosure } from "./Disclosure";
import { EmptyState } from "./EmptyState";
import { Field, INPUT_CLASS, Label, LABEL_CLASS } from "./Field";
import { IconButton } from "./IconButton";
import { Spinner, SpinnerBlock } from "./Spinner";
import { TABLE_HEAD_ROW, TD_NUM, TH, TH_NUM } from "./table";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

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

function render(ui: React.ReactNode) {
  act(() => root.render(ui));
}

function click(el: Element | null) {
  act(() => {
    el?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("Card", () => {
  it("renders the shared surface and appends the caller's classes", () => {
    render(<Card className="space-y-3" data-testid="c">x</Card>);
    const card = container.querySelector("[data-testid=c]")!;
    expect(card.tagName).toBe("DIV");
    expect(card.className).toBe(`${CARD_CLASS} space-y-3`);
  });

  it("can be another element", () => {
    render(<Card as="ul"><li>a</li></Card>);
    expect(container.querySelector("ul")!.className).toBe(CARD_CLASS);
  });
});

describe("EmptyState", () => {
  it("is one muted centred line", () => {
    render(<EmptyState>No positions</EmptyState>);
    const p = container.querySelector("p")!;
    expect(p.textContent).toBe("No positions");
    expect(p.className).toContain("text-center");
    expect(p.className).toContain("text-[var(--color-text-muted)]");
  });
});

describe("Spinner", () => {
  it("is decorative unless given a label, and sizes by name", () => {
    render(<Spinner size="xl" />);
    const svg = container.querySelector("[data-spinner]")!;
    expect(svg.getAttribute("aria-hidden")).toBe("true");
    expect(svg.getAttribute("role")).toBeNull();
    expect(svg.getAttribute("class")).toContain("h-6 w-6");
    expect(svg.getAttribute("class")).toContain("animate-spin");
    expect(svg.getAttribute("class")).not.toContain("--color-text-muted");
  });

  it("takes the muted tone only when asked, so it inherits a button's color by default", () => {
    render(<Spinner tone="muted" className="ml-1" />);
    const cls = container.querySelector("[data-spinner]")!.getAttribute("class")!;
    expect(cls).toContain("text-[var(--color-text-muted)]");
    expect(cls).toContain("h-4 w-4");
    expect(cls).toContain("ml-1");
  });

  it("announces itself when labelled", () => {
    render(<SpinnerBlock label="Loading snapshot" />);
    const svg = container.querySelector("[data-spinner]")!;
    expect(svg.getAttribute("role")).toBe("status");
    expect(svg.getAttribute("aria-label")).toBe("Loading snapshot");
    expect(svg.getAttribute("aria-hidden")).toBeNull();
  });
});

describe("IconButton", () => {
  it("names itself for assistive tech and the pointer, and is never a submit button", () => {
    const onClick = vi.fn();
    render(<IconButton label="Close" onClick={onClick}>x</IconButton>);
    const button = container.querySelector("button")!;
    expect(button.getAttribute("type")).toBe("button");
    expect(button.getAttribute("aria-label")).toBe("Close");
    expect(button.getAttribute("title")).toBe("Close");
    click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("lets a tooltip differ from the name and takes a danger tone", () => {
    render(<IconButton label="Delete report" title="Owner only" tone="danger" disabled>x</IconButton>);
    const button = container.querySelector("button")!;
    expect(button.getAttribute("title")).toBe("Owner only");
    expect(button.className).toContain("--color-red");
    expect(button.hasAttribute("disabled")).toBe(true);
  });
});

describe("Disclosure", () => {
  it("starts closed, exposes aria-expanded and mounts the body only when open", () => {
    render(<Disclosure summary="Audit log"><p>body</p></Disclosure>);
    const button = container.querySelector("button")!;
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector("p")).toBeNull();
    click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector("p")!.textContent).toBe("body");
    expect(button.getAttribute("aria-controls")).toBe(container.querySelector("p")!.parentElement!.id);
  });

  it("follows the owner's state when controlled", () => {
    const onOpenChange = vi.fn();
    render(<Disclosure summary="s" open={false} onOpenChange={onOpenChange}>body</Disclosure>);
    click(container.querySelector("button"));
    expect(onOpenChange).toHaveBeenCalledWith(true);
    expect(container.querySelector("button")!.getAttribute("aria-expanded")).toBe("false");
    render(<Disclosure summary="s" open onOpenChange={onOpenChange}>body</Disclosure>);
    expect(container.querySelector("button")!.getAttribute("aria-expanded")).toBe("true");
  });

  it("turns the chevron rather than swapping icons", () => {
    render(<Disclosure summary="s" defaultOpen>body</Disclosure>);
    expect(container.querySelector("svg")!.getAttribute("class")).toContain("rotate-90");
  });
});

describe("Field", () => {
  it("binds the label to the control and shows the hint", () => {
    render(
      <Field label="Host" htmlFor="host" hint="Without scheme">
        <input id="host" className={INPUT_CLASS} />
      </Field>,
    );
    const label = container.querySelector("label")!;
    expect(label.getAttribute("for")).toBe("host");
    expect(label.className).toBe(LABEL_CLASS);
    expect(container.textContent).toContain("Without scheme");
  });

  it("Label merges extra classes", () => {
    render(<Label className="mt-2">x</Label>);
    expect(container.querySelector("label")!.className).toBe(`${LABEL_CLASS} mt-2`);
  });
});

describe("table classes", () => {
  it("align numbers right and keep the header muted", () => {
    expect(TH).toContain("text-left");
    expect(TH_NUM).toContain("text-right");
    expect(TD_NUM).toContain("font-mono");
    expect(TABLE_HEAD_ROW).toContain("--color-text-muted");
  });
});
