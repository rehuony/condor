/**
 * @vitest-environment jsdom
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { TextInputDialog } from "@/components/ui/TextInputDialog";

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

function render(pending = false) {
  const handlers = { onConfirm: vi.fn(), onCancel: vi.fn() };
  act(() => root.render(
    <ConfirmDialog title="Delete this report?" confirmLabel="Delete report" pending={pending} {...handlers}>
      <p>Removed permanently.</p>
    </ConfirmDialog>,
  ));
  return handlers;
}

// The dialog is portalled to <body>, so it is looked up there.
const button = (label: string) =>
  [...document.querySelectorAll("button")].find((b) => b.textContent === label) as HTMLButtonElement;

it("is a labelled modal that focuses Cancel and confirms on request", () => {
  const { onConfirm, onCancel } = render();
  const dialog = document.querySelector('[role="dialog"]')!;
  expect(dialog.getAttribute("aria-modal")).toBe("true");
  expect(document.getElementById(dialog.getAttribute("aria-labelledby")!)?.textContent).toBe("Delete this report?");
  expect(document.activeElement).toBe(button("Cancel"));
  act(() => button("Delete report").click());
  expect(onConfirm).toHaveBeenCalledOnce();
  act(() => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); });
  expect(onCancel).toHaveBeenCalledOnce();
});

it("cannot be dismissed while the action runs", () => {
  const { onCancel } = render(true);
  expect(button("Deleting…").disabled).toBe(true);
  act(() => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); });
  act(() => (document.querySelector('[role="dialog"]')!.parentElement as HTMLElement).click());
  expect(onCancel).not.toHaveBeenCalled();
});

it.each(["confirm", "input"] as const)("isolates %s dialog keys from its React parent and containing sheet", (kind) => {
  const onCancel = vi.fn();
  const parent = vi.fn();
  const sheet = vi.fn();
  window.addEventListener("keydown", sheet);
  try {
    act(() => root.render(
      <div onKeyDown={parent}>
        {kind === "confirm"
          ? <ConfirmDialog title="Delete?" confirmLabel="Delete" onConfirm={() => {}} onCancel={onCancel} />
          : <TextInputDialog title="Rename" label="Title" initialValue="Report" onSubmit={() => {}} onCancel={onCancel} />}
      </div>,
    ));
    for (const key of ["ArrowRight", "ArrowDown", "f", "Escape"]) {
      act(() => document.activeElement!.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })));
    }
    expect(onCancel).toHaveBeenCalledOnce();
    expect(parent).not.toHaveBeenCalled();
    expect(sheet).not.toHaveBeenCalled();
  } finally {
    window.removeEventListener("keydown", sheet);
  }
});

it.each(["confirm", "input"] as const)("keeps Tab inside the %s dialog, including while pending", (kind) => {
  const onCancel = vi.fn();
  const show = (pending: boolean) => act(() => root.render(kind === "confirm"
    ? <ConfirmDialog title="Delete?" confirmLabel="Delete" pending={pending} onConfirm={() => {}} onCancel={onCancel} />
    : <TextInputDialog title="Rename" label="Title" initialValue="Report" pending={pending} onSubmit={() => {}} onCancel={onCancel} />));
  const press = (key: string, shiftKey = false) => act(() => document.activeElement!.dispatchEvent(
    new KeyboardEvent("keydown", { key, shiftKey, bubbles: true, cancelable: true }),
  ));
  show(false);
  const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
  const first = document.activeElement;
  const last = dialog.querySelector<HTMLButtonElement>('button:last-child')!;
  press("Tab", true);
  expect(document.activeElement).toBe(last);
  press("Tab");
  expect(document.activeElement).toBe(first);

  show(true);
  expect(document.activeElement).toBe(dialog);
  press("Tab");
  press("Escape");
  expect(document.activeElement).toBe(dialog);
  expect(onCancel).not.toHaveBeenCalled();
});
