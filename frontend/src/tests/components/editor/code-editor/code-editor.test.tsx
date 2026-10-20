/** @vitest-environment jsdom */

import { language } from "@codemirror/language";
import { EditorView } from "@codemirror/view";
import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CodeEditor } from "@/components/editor/code-editor";

const loading = vi.hoisted(() => {
  let release!: () => void;
  const ready = new Promise<void>((resolve) => { release = resolve; });
  return { ready, release, theme: "light" };
});

vi.mock("@/hooks/useTheme", () => ({
  useTheme: () => ({ theme: loading.theme }),
}));

vi.mock("@/components/editor/code-editor/code-editor-implementation", async (importOriginal) => {
  await loading.ready;
  return importOriginal();
});

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  loading.theme = "light";
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  loading.release();
  act(() => root.unmount());
  container.remove();
});

function editor() {
  const element = container.querySelector<HTMLElement>(".cm-editor");
  expect(element).not.toBeNull();
  return EditorView.findFromDOM(element!)!;
}

async function render(props: ComponentProps<typeof CodeEditor>, hidden = false) {
  await act(async () => {
    root.render(<div hidden={hidden}><CodeEditor {...props} /></div>);
    await import("@/components/editor/code-editor/code-editor-implementation");
  });
}

describe("CodeEditor's lazy boundary", () => {
  it("reserves the editor's space while loading and mounts the latest requested document", async () => {
    act(() => {
      root.render(<CodeEditor value="old: true" language="yaml" height="300px" />);
    });
    expect(container.querySelector('[role="status"]')?.getAttribute("aria-label")).toBe("Loading editor");
    expect(container.firstElementChild?.getAttribute("style")).toContain("height: 300px");
    expect(container.querySelector(".cm-editor")).toBeNull();

    // A document can change while the chunk is in flight. Its old props must
    // never become a stale draft when the implementation finally mounts.
    act(() => {
      root.render(<CodeEditor value="print('latest')" language="python" height="480px" readOnly />);
    });
    await act(async () => {
      loading.release();
      await import("@/components/editor/code-editor/code-editor-implementation");
    });

    const view = editor();
    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(view.state.doc.toString()).toBe("print('latest')");
    expect(view.state.facet(language)?.name).toBe("python");
    expect(view.contentDOM.getAttribute("contenteditable")).toBe("false");
    expect(getComputedStyle(view.dom).height).toBe("480px");
  });

  it("keeps edited drafts across visibility changes and follows controlled value, callback, and theme updates", async () => {
    loading.release();
    const firstChange = vi.fn();
    const nextChange = vi.fn();
    let value = "balance: 1";
    await render({ value, language: "yaml", onChange: firstChange });
    const view = editor();

    act(() => view.dispatch({ changes: { from: 9, to: 10, insert: "2" } }));
    expect(firstChange).toHaveBeenLastCalledWith("balance: 2");
    value = "balance: 2";

    await render({ value, language: "yaml", onChange: nextChange }, true);
    await render({ value, language: "yaml", onChange: nextChange });
    expect(editor()).toBe(view);
    expect(view.state.doc.toString()).toBe(value);

    act(() => view.dispatch({ changes: { from: 9, to: 10, insert: "3" } }));
    expect(nextChange).toHaveBeenLastCalledWith("balance: 3");
    expect(firstChange).toHaveBeenCalledTimes(1);

    value = "balance: 10";
    await render({ value, language: "yaml", onChange: nextChange });
    expect(editor()).toBe(view);
    expect(view.state.doc.toString()).toBe(value);
    expect(view.state.facet(EditorView.darkTheme)).toBe(false);

    loading.theme = "dark";
    await render({ value, language: "yaml", onChange: nextChange });
    expect(editor().state.doc.toString()).toBe(value);
    expect(editor().state.facet(EditorView.darkTheme)).toBe(true);
  });
});
