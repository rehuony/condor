/**
 * @vitest-environment jsdom
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const getNotifications = vi.fn();
const clearNotifications = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getNotifications: (...a: unknown[]) => getNotifications(...a),
    clearNotifications: (...a: unknown[]) => clearNotifications(...a),
    markNotificationsRead: () => Promise.resolve({ unread: 0 }),
  },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ token: "t" }) }));
vi.mock("@/lib/push", () => ({ onPushNavigate: () => () => {} }));
vi.mock("@/lib/appBadge", () => ({ setAppBadge: () => {} }));

const { NotificationBell } = await import("./NotificationBell");

const note = (id: string) => ({ id, user_id: 1, ts: 1, kind: "system", text: `note ${id}`, read: false });

let container: HTMLDivElement;
let root: Root;

beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  getNotifications.mockResolvedValue({ items: [note("a"), note("b")], unread: 2 });
  clearNotifications.mockResolvedValue({ removed: 2, unread: 0 });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter><NotificationBell /></MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const byTitle = (title: string) => document.querySelector<HTMLButtonElement>(`button[title="${title}"]`)!;

it("clears every notification only after the dialog is confirmed", async () => {
  await act(async () => container.querySelector<HTMLButtonElement>('button[aria-haspopup="menu"]')!.click());
  await act(async () => byTitle("Remove all notifications").click());

  const dialog = document.querySelector('[role="dialog"]')!;
  expect(dialog.textContent).toContain("This can't be undone.");
  expect(clearNotifications).not.toHaveBeenCalled();

  await act(async () => {
    [...dialog.querySelectorAll("button")].find((b) => b.textContent === "Clear all")!.click();
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

  expect(clearNotifications).toHaveBeenCalledOnce();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  const bell = container.querySelector<HTMLButtonElement>('button[aria-haspopup="menu"]')!;
  expect(bell.title).toBe("Notifications");
  await act(async () => bell.click());
  expect(document.body.textContent).toContain("Nothing yet.");
});
