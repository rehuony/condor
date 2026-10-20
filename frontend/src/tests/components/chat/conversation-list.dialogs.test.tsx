/**
 * Rename and delete ask in a dialog rather than turning the row into a form,
 * so the rail's rows keep their height while the user decides.
 *
 * @vitest-environment jsdom
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const listConversations = vi.fn();
const renameConversation = vi.fn();
const deleteConversation = vi.fn();

vi.mock("@/lib/api/conversations", () => ({
  conversationsApi: {
    listConversations: (...a: unknown[]) => listConversations(...a),
    renameConversation: (...a: unknown[]) => renameConversation(...a),
    deleteConversation: (...a: unknown[]) => deleteConversation(...a),
  },
}));

const { ConversationList } = await import("@/components/chat/ConversationList");

const now = new Date().toISOString();
const meta = {
  id: "c1", user_id: 1, surface: "web", title: "Memory cleanup", agent_key: "", agent_slug: "",
  server_name: null, created_at: now, updated_at: now, turn_count: 3, last_snippet: "",
};

let container: HTMLDivElement;
let root: Root;

beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  listConversations.mockResolvedValue([meta]);
  renameConversation.mockResolvedValue({});
  deleteConversation.mockResolvedValue({});
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ConversationList liveIds={new Set()} activeId={null} onNew={() => {}} onOpen={() => {}} />
      </QueryClientProvider>,
    );
  });
  await settle();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function settle() {
  for (let i = 0; i < 3; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const rowButton = (title: string) => container.querySelector<HTMLButtonElement>(`button[title="${title}"]`)!;
const dialog = () => document.querySelector<HTMLElement>('[role="dialog"]');
const dialogButton = (text: string) =>
  [...dialog()!.querySelectorAll("button")].find((b) => b.textContent === text)!;

it("renames in a dialog and leaves the row untouched until saved", async () => {
  await act(async () => rowButton("Rename").click());
  // The only text field is the dialog's: the row itself never becomes a form.
  expect([...container.querySelectorAll("input")].every((field) => dialog()!.contains(field))).toBe(true);
  const input = dialog()!.querySelector("input")!;
  expect(input.value).toBe("Memory cleanup");

  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "  Skills review  ");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => dialogButton("Save").click());
  await settle();

  expect(renameConversation).toHaveBeenCalledWith("c1", "Skills review");
  expect(dialog()).toBeNull();
});

it("will not save an empty title", async () => {
  await act(async () => rowButton("Rename").click());
  const input = dialog()!.querySelector("input")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "   ");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(dialogButton("Save").disabled).toBe(true);
});

it("deletes only after the dialog is confirmed", async () => {
  await act(async () => rowButton("Delete").click());
  expect(dialog()!.textContent).toContain("Memory cleanup");
  expect(deleteConversation).not.toHaveBeenCalled();

  await act(async () => dialogButton("Delete").click());
  await settle();

  expect(deleteConversation).toHaveBeenCalledWith("c1");
  expect(dialog()).toBeNull();
});
