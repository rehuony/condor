/**
 * @vitest-environment jsdom
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { AdminPerson } from "@/lib/api/admin";

const image = vi.fn();
vi.mock("@/hooks/useAuthedImage", () => ({ useAuthedImage: (url: string) => image(url) }));

const { PersonAvatar } = await import("@/components/settings/admin/PersonAvatar");

const person = {
  user_id: 42, first_name: "Sun", last_name: "Raise", username: "rehuony_tg", known: true,
} as AdminPerson;

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

it("shows the proxied Telegram photo once it has loaded", () => {
  image.mockReturnValue({ src: "blob:photo", status: "ready" });
  act(() => root.render(<PersonAvatar person={person} />));
  expect(image).toHaveBeenCalledWith("/api/v1/admin/people/42/photo");
  expect(container.querySelector("img")?.getAttribute("src")).toBe("blob:photo");
});

it.each(["loading", "error"] as const)("keeps the initials while the photo is %s", (status) => {
  image.mockReturnValue({ src: null, status });
  act(() => root.render(<PersonAvatar person={person} />));
  expect(container.querySelector("img")).toBeNull();
  expect(container.textContent).toBe("SR");
});
