/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatMessageView } from "@/components/chat/ChatMessage";
import type { ChatMessage } from "@/lib/chat-transcript";

const authFetch = vi.fn<(url: string) => Promise<Response>>();
vi.mock("@/lib/auth-token", () => ({
  authFetch: (url: string) => authFetch(url),
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const imageUrl = "/api/v1/conversations/c1/attachments/plot.png";
let container: HTMLDivElement;
let root: Root;

function imageMessage(role: ChatMessage["role"], text = ""): ChatMessage {
  return {
    id: "image", role, text, toolCalls: [],
    kind: role === "system" ? "notification" : undefined,
    attachments: [{ url: imageUrl, mime: "image/png" }],
  };
}

function png(): Response {
  return {
    ok: true,
    blob: async () => new Blob([new Uint8Array(8)], { type: "image/png" }),
  } as Response;
}

async function render(message: ChatMessage, live = false) {
  await act(async () => root.render(<ChatMessageView message={message} live={live} />));
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  authFetch.mockReset();
  authFetch.mockResolvedValue(png());
  URL.createObjectURL = vi.fn(() => "blob:authenticated-image");
  URL.revokeObjectURL = vi.fn();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("images delivered by an agent", () => {
  it.each(["system", "assistant"] as const)("renders authenticated %s attachments with their text", async (role) => {
    await render(imageMessage(role, "SOL entry and exit levels"));

    expect(authFetch).toHaveBeenCalledWith(imageUrl);
    expect(container.querySelector("img")?.src).toBe("blob:authenticated-image");
    expect(container.textContent).toContain("SOL entry and exit levels");
    if (role === "system") expect(container.textContent).toContain("Notified");
  });

  it.each(["system", "assistant"] as const)("keeps an image-only %s turn visible while loading and after it arrives", async (role) => {
    let resolve!: (response: Response) => void;
    authFetch.mockReturnValueOnce(new Promise((res) => { resolve = res; }));
    await render(imageMessage(role), true);

    expect(container.querySelector('[role="status"]')?.getAttribute("aria-label")).toBe("Loading image");
    expect(container.textContent).not.toContain("Working...");
    await act(async () => resolve(png()));
    expect(container.querySelector("img")?.alt).toBe("Attached image");
    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(container.textContent).not.toContain("...");
  });

  it("explains a failed authenticated fetch instead of erasing the notification", async () => {
    authFetch.mockResolvedValueOnce({ ok: false, status: 404 } as Response);
    await render(imageMessage("system"));

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector('[role="status"]')?.textContent).toContain("Image unavailable");
    expect(container.textContent).toContain("Reload this page to try again");
  });

  it("shows the same recovery message when fetched bytes cannot be decoded as an image", async () => {
    await render(imageMessage("assistant"));
    act(() => container.querySelector("img")!.dispatchEvent(new Event("error")));

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector('[role="status"]')?.textContent).toContain("Image unavailable");
  });

  it("keeps a user's local preview in its bubble without fetching it", async () => {
    const message = imageMessage("user", "Read this chart");
    message.attachments = [{ url: "blob:local-preview", mime: "image/png", local: true }];
    await render(message);

    expect(authFetch).not.toHaveBeenCalled();
    expect(container.querySelector("img")?.src).toBe("blob:local-preview");
    expect(container.querySelector("p")?.textContent).toBe("Read this chart");
  });
});
