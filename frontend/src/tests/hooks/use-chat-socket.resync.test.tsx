/**
 * What a reconnect re-reads.
 *
 * The chat socket is the only delivery channel for an out-of-band note — a
 * delegation's outcome, a routine's result — and it is a fire-and-forget
 * broadcast: no queue, no replay, no ack. A tab whose socket was down for the
 * second one was pushed lost it, and nothing ever asked for it again, because
 * the transcript was read exactly once per slot per page load. These pin the
 * recovery path: a reconnect re-reads, and re-reading neither duplicates what
 * is on screen nor drops what arrived while the read was in flight.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";

const getConversation = vi.fn();
const listConversations = vi.fn();
const uploadAttachment = vi.fn();

vi.mock("@/lib/api/chat", () => ({
  chatApi: {
    getSessionOptions: () => Promise.resolve({ default_agent: "claude-code" }),
  },
}));
vi.mock("@/lib/api/conversations", () => ({
  conversationsApi: {
    listConversations: (...args: unknown[]) => listConversations(...args),
    getConversation: (...args: unknown[]) => getConversation(...args),
    uploadAttachment: (...args: unknown[]) => uploadAttachment(...args),
    attachmentUrl: (conversationId: string, id: string) => `/api/v1/conversations/${conversationId}/attachments/${id}`,
  },
}));

vi.mock("@/lib/viewFacts", () => ({
  collectViewFacts: () => ({}),
  renderViewBlock: () => "",
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ token: "jwt", user: { id: 7 } }),
}));

const { useChatSocket } = await import("@/hooks/useChatSocket");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

class FakeSocket {
  static last: FakeSocket | null = null;

  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readyState = FakeSocket.OPEN;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  sent: Record<string, unknown>[] = [];

  constructor() {
    FakeSocket.last = this;
  }
  send(raw: string) { this.sent.push(JSON.parse(raw)); }
  close() {
    this.readyState = FakeSocket.CLOSED;
  }
  deliver(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

const sock = () => FakeSocket.last!;
const localClientId = () => sock().sent.find((frame) => frame.action === "send_message")?.client_id;

const holder: { current: ReturnType<typeof useChatSocket> | null } = {
  current: null,
};
const chat = () => holder.current!;

function Harness() {
  const state = useChatSocket();
  useEffect(() => {
    holder.current = state;
  });
  return null;
}

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** The one session the server knows about in these tests. */
const ROSTER = [{ slot_id: "s1", conversation_id: "c1", agent_key: "k" }];

const roster = () => ({ event: "sessions_list", sessions: ROSTER });

/** The transcript of the slot under test, as rendered. */
const messages = () =>
  chat().slots.find((s) => s.info.slot_id === "s1")?.messages ?? [];

/** Open the page and let the first roster hydrate the one live session. */
async function arrive() {
  act(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <ServerContext value={{ server: "moneymaker", setServer: () => {} }}>
          <Harness />
        </ServerContext>
      </QueryClientProvider>,
    );
  });
  act(() => {
    chat().connect();
    sock().onopen?.();
  });
  act(() => {
    sock().deliver(roster());
  });
  await settle();
}

/** Drop the socket and bring it back, exactly as the browser would. */
function reconnect() {
  act(() => {
    sock().readyState = FakeSocket.CLOSED;
    sock().onclose?.();
  });
  act(() => {
    chat().connect();
    sock().onopen?.();
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("WebSocket", FakeSocket);
  FakeSocket.last = null;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  listConversations.mockResolvedValue([]);
  getConversation.mockResolvedValue({
    meta: {},
    turns: [{ role: "user", text: "run the audit", ts: "1", tool_calls: [] }],
  });
});

function changed(phase: "accepted" | "completed", extra: Record<string, unknown> = {}) {
  act(() => sock().deliver({
    event: "conversation_changed",
    conversation_id: "c1",
    session_key: "tg:7",
    phase,
    is_busy: phase === "accepted",
    ...extra,
  }));
}

const turn = (role: string, text: string, ts: number) => ({
  role, text, ts, tool_calls: [],
});

describe("changes from Telegram or another browser", () => {
  it("loads an image-only notification during a Telegram turn and preserves it after a page reload", async () => {
    await arrive();
    getConversation.mockResolvedValue({
      meta: {}, is_busy: true,
      turns: [turn("user", "run the audit", 1), {
        ...turn("system", "", 2), kind: "notification",
        attachments: [{ id: "plot.png", mime: "image/png", bytes: 1024 }],
      }],
    });
    changed("completed", { is_busy: true });
    await settle();

    const notification = messages()[1];
    expect(notification).toMatchObject({
      role: "system", kind: "notification", text: "",
      attachments: [{ url: "/api/v1/conversations/c1/attachments/plot.png", mime: "image/png" }],
    });
    expect(chat().isSlotStreaming("s1")).toBe(true);
    expect(sock().sent).toEqual([]);

    act(() => root.unmount());
    root = createRoot(container);
    await arrive();
    expect(messages()[1]).toEqual(notification);
    expect(messages()).toHaveLength(2);
  });

  it("restores a sent image after a local answer finishes without duplicating the active answer", async () => {
    await arrive();
    act(() => chat().sendMessage("s1", "draw the chart"));
    act(() => sock().deliver({ event: "tool_call", slot_id: "s1", tool_call_id: "image", title: "send_notification", status: "in_progress" }));
    getConversation.mockResolvedValue({ meta: {}, is_busy: true, turns: [
      turn("user", "run the audit", 1), turn("user", "draw the chart", 2), {
        ...turn("system", "Entry and exit levels", 3), kind: "notification",
        attachments: [{ id: "plot.png", mime: "image/png", bytes: 1024 }],
      }, turn("assistant", "Image sent", 4),
    ] });
    changed("completed", { is_busy: true });
    await settle();
    expect(getConversation).toHaveBeenCalledTimes(1);
    expect(messages().at(-1)?.open).toBe(true);

    act(() => {
      sock().deliver({ event: "text_chunk", slot_id: "s1", text: "Image sent" });
      sock().deliver({ event: "prompt_done", slot_id: "s1", client_id: localClientId() });
    });
    await settle();
    expect(messages().map((message) => message.text)).toEqual([
      "run the audit", "draw the chart", "Entry and exit levels", "Image sent",
    ]);
    expect(messages()[2].attachments).toEqual([
      { url: "/api/v1/conversations/c1/attachments/plot.png", mime: "image/png" },
    ]);
  });

  it("reads accepted messages and completed answers without starting another turn", async () => {
    await arrive();
    queryClient.setQueryData(["conversations"], []);
    getConversation.mockResolvedValue({
      meta: {}, is_busy: true,
      turns: [turn("user", "run the audit", 1), turn("user", "from my phone", 2)],
    });
    changed("accepted");
    await settle();
    expect(messages().map((message) => message.text)).toEqual(["run the audit", "from my phone"]);
    expect(chat().isSlotStreaming("s1")).toBe(true);
    expect(queryClient.getQueryState(["conversations"])?.isInvalidated).toBe(true);

    getConversation.mockResolvedValue({
      meta: {}, is_busy: false,
      turns: [turn("user", "run the audit", 1), turn("user", "from my phone", 2), turn("assistant", "phone answer", 3)],
    });
    changed("completed");
    await settle();
    expect(messages().map((message) => message.text)).toEqual(["run the audit", "from my phone", "phone answer"]);
    expect(chat().isSlotStreaming("s1")).toBe(false);
    expect(sock().sent).toEqual([]);
  });

  it("invalidates a new Telegram conversation without opening it or stealing focus", async () => {
    await arrive();
    queryClient.setQueryData(["conversations"], []);
    const active = chat().activeSlotId;
    changed("accepted", { conversation_id: "phone-only" });
    await settle();
    expect(queryClient.getQueryState(["conversations"])?.isInvalidated).toBe(true);
    expect(chat().slots).toHaveLength(1);
    expect(chat().activeSlotId).toBe(active);
    expect(chat().isStreaming).toBe(false);
    expect(getConversation).toHaveBeenCalledTimes(1);
    expect(sock().sent).toEqual([]);
  });

  it("also refreshes changes from another browser using the same web session key", async () => {
    await arrive();
    getConversation.mockResolvedValue({ meta: {}, turns: [turn("user", "other browser", 2)] });
    changed("accepted", { session_key: "web:7:s1" });
    await settle();
    expect(messages().map((message) => message.text)).toEqual(["other browser"]);
    expect(sock().sent).toEqual([]);
  });

  it("defers a refresh until the local question and streamed answer finish", async () => {
    await arrive();
    act(() => chat().sendMessage("s1", "local question"));
    act(() => sock().deliver({ event: "tool_call", slot_id: "s1", tool_call_id: "tool", title: "Read", status: "in_progress" }));
    changed("accepted");
    changed("completed");
    await settle();
    expect(getConversation).toHaveBeenCalledTimes(1);
    expect(messages().map((message) => message.text)).toEqual(["run the audit", "local question", ""]);
    expect(messages().at(-1)?.open).toBe(true);
    expect(chat().isSlotStreaming("s1")).toBe(true);

    getConversation.mockResolvedValue({ meta: {}, is_busy: false, turns: [
      turn("user", "run the audit", 1), turn("user", "phone question", 2),
      turn("assistant", "phone answer", 3), turn("user", "local question", 4),
      turn("assistant", "local answer", 5),
    ] });
    act(() => {
      sock().deliver({ event: "text_chunk", slot_id: "s1", text: "local answer" });
      sock().deliver({ event: "prompt_done", slot_id: "s1", client_id: localClientId() });
    });
    await settle();
    expect(messages().map((message) => message.text)).toEqual([
      "run the audit", "phone question", "phone answer", "local question", "local answer",
    ]);
    expect(chat().isSlotStreaming("s1")).toBe(false);
    expect(sock().sent.filter((message) => message.action === "send_message")).toHaveLength(1);
  });

  it("preserves a local message typed while the external refresh is in flight", async () => {
    await arrive();
    let release: (value: unknown) => void = () => {};
    getConversation.mockReturnValueOnce(new Promise((resolve) => { release = resolve; }));
    changed("accepted");
    await settle();
    act(() => chat().sendMessage("s1", "still waiting locally"));
    await act(async () => release({ meta: {}, turns: [turn("user", "phone question", 2)] }));
    await settle();
    expect(messages().map((message) => message.text)).toEqual(["run the audit", "still waiting locally"]);
  });

  it("ignores an older read that finishes after the completion refresh", async () => {
    await arrive();
    let release: (value: unknown) => void = () => {};
    getConversation.mockReturnValueOnce(new Promise((resolve) => { release = resolve; }));
    changed("accepted");
    await settle();
    getConversation.mockResolvedValue({ meta: {}, is_busy: false, turns: [turn("assistant", "finished", 3)] });
    changed("completed");
    await settle();
    await act(async () => release({ meta: {}, is_busy: true, turns: [turn("user", "old snapshot", 2)] }));
    await settle();
    expect(messages().map((message) => message.text)).toEqual(["finished"]);
    expect(chat().isSlotStreaming("s1")).toBe(false);
  });

  it("keeps questions queued behind a pending session spawn", async () => {
    await arrive();
    getConversation.mockResolvedValue({ meta: {}, turns: [] });
    act(() => chat().resumeConversation("pending"));
    await settle();
    act(() => chat().sendMessage("pending", "send after the spawn"));
    changed("accepted", { conversation_id: "pending" });
    await settle();
    const pending = chat().slots.find((slot) => slot.info.slot_id === "pending");
    expect(pending?.messages.map((message) => message.text)).toEqual(["send after the spawn"]);
    expect(getConversation).toHaveBeenCalledTimes(2);
    expect(sock().sent.filter((message) => message.action === "send_message")).toHaveLength(0);

    act(() => sock().deliver({ event: "session_started", slot_id: "pending", conversation_id: "pending", agent_key: "k", restored: true }));
    await settle();
    expect(sock().sent.filter((message) => message.action === "send_message")).toHaveLength(1);
    expect(getConversation).toHaveBeenCalledTimes(2);
  });

  it("waits for both local turns instead of refreshing after the interrupted one", async () => {
    await arrive();
    act(() => {
      chat().sendMessage("s1", "first local question");
      chat().sendMessage("s1", "redirect it");
    });
    changed("accepted");
    act(() => sock().deliver({ event: "prompt_done", slot_id: "s1", client_id: localClientId(), stop_reason: "cancelled" }));
    await settle();
    expect(getConversation).toHaveBeenCalledTimes(1);
    expect(messages().map((message) => message.text)).toEqual([
      "run the audit", "first local question", "redirect it",
    ]);
    act(() => sock().deliver({ event: "prompt_done", slot_id: "s1", client_id: localClientId() }));
    await settle();
    expect(getConversation).toHaveBeenCalledTimes(2);
  });

  it("ignores stale lifecycle events and accepts a reset sequence on reconnect", async () => {
    await arrive();
    changed("completed", { sequence: 2 });
    await settle();
    changed("accepted", { sequence: 1 });
    await settle();
    expect(chat().isSlotStreaming("s1")).toBe(false);
    expect(getConversation).toHaveBeenCalledTimes(2);

    reconnect();
    act(() => sock().deliver(roster()));
    await settle();
    changed("accepted", { sequence: 1 });
    await settle();
    expect(chat().isSlotStreaming("s1")).toBe(true);
    expect(getConversation).toHaveBeenCalledTimes(4);
  });

  it("recovers cross-surface busy state from history and clears it on reconnect", async () => {
    getConversation.mockResolvedValue({ meta: {}, is_busy: true, turns: [] });
    await arrive();
    expect(chat().isSlotStreaming("s1")).toBe(true);
    getConversation.mockResolvedValue({ meta: {}, is_busy: false, turns: [] });
    reconnect();
    act(() => sock().deliver(roster()));
    await settle();
    expect(chat().isSlotStreaming("s1")).toBe(false);
  });

  it("keeps a conversation busy while another accepted turn is still queued", async () => {
    await arrive();
    changed("accepted");
    changed("completed", { is_busy: true });
    await settle();
    expect(chat().isSlotStreaming("s1")).toBe(true);
    changed("completed");
    await settle();
    expect(chat().isSlotStreaming("s1")).toBe(false);
  });

  it("does not erase a question sent as the socket reconnects", async () => {
    await arrive();
    act(() => {
      sock().readyState = FakeSocket.CLOSED;
      sock().onclose?.();
      chat().sendMessage("s1", "sent on reconnect");
    });
    act(() => {
      chat().connect();
      sock().onopen?.();
      sock().deliver(roster());
    });
    await settle();
    expect(messages().map((message) => message.text)).toEqual(["run the audit", "sent on reconnect"]);
    expect(getConversation).toHaveBeenCalledTimes(1);
    expect(sock().sent.filter((message) => message.action === "send_message")).toHaveLength(1);
  });

  it("does not erase a question whose attachment is still uploading on reconnect", async () => {
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL() { return "blob:preview"; }
    });
    let release: (value: unknown) => void = () => {};
    uploadAttachment.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    await arrive();
    act(() => chat().sendMessage("s1", "uploading", [new File(["image"], "image.png", { type: "image/png" })]));
    reconnect();
    act(() => sock().deliver(roster()));
    await settle();
    expect(messages().map((message) => message.text)).toEqual(["run the audit", "uploading"]);
    expect(messages().at(-1)?.attachments?.[0].url).toBe("blob:preview");
    expect(getConversation).toHaveBeenCalledTimes(1);
    await act(async () => release({ id: "image-1" }));
    await settle();
    expect(sock().sent.filter((message) => message.action === "send_message")).toHaveLength(1);
  });

  it("recovers a missed local DONE even when Telegram is now busy", async () => {
    await arrive();
    act(() => chat().sendMessage("s1", "finished while offline"));
    act(() => sock().deliver({ event: "tool_call", slot_id: "s1", tool_call_id: "tool", title: "Read", status: "in_progress" }));
    expect(messages().at(-1)?.open).toBe(true);
    getConversation.mockResolvedValue({ meta: {}, is_busy: true, turns: [
      turn("user", "run the audit", 1), turn("user", "finished while offline", 2),
      turn("assistant", "local result", 3), turn("user", "new phone request", 4),
    ] });
    reconnect();
    act(() => sock().deliver({ event: "sessions_list", sessions: [{
      ...ROSTER[0], is_busy: true, web_pending_turns: 0,
    }] }));
    await settle();
    expect(messages().map((message) => message.text)).toEqual([
      "run the audit", "finished while offline", "local result", "new phone request",
    ]);
    expect(chat().isSlotStreaming("s1")).toBe(true);

    getConversation.mockResolvedValue({ meta: {}, is_busy: false, turns: [turn("assistant", "phone finished", 5)] });
    changed("completed");
    await settle();
    expect(messages().map((message) => message.text)).toEqual(["phone finished"]);
    expect(chat().isSlotStreaming("s1")).toBe(false);
  });

  it("continues protecting a local turn the reconnect roster still lists", async () => {
    await arrive();
    act(() => chat().sendMessage("s1", "still running locally"));
    const clientId = localClientId();
    reconnect();
    act(() => sock().deliver({ event: "sessions_list", sessions: [{
      ...ROSTER[0], is_busy: true, web_pending_turns: 1,
    }] }));
    changed("completed", { is_busy: true });
    await settle();
    expect(getConversation).toHaveBeenCalledTimes(1);
    expect(messages().map((message) => message.text)).toEqual(["run the audit", "still running locally"]);
    act(() => sock().deliver({ event: "prompt_done", slot_id: "s1", client_id: clientId }));
    await settle();
    expect(getConversation).toHaveBeenCalledTimes(2);
  });

  it.each(["uploading", "sent"])("keeps a %s local question when a foreign turn completes", async (stage) => {
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL() { return "blob:preview"; }
      static revokeObjectURL() {}
    });
    let releaseUpload: (value: { id: string }) => void = () => {};
    uploadAttachment.mockReturnValue(new Promise((resolve) => { releaseUpload = resolve; }));
    await arrive();
    act(() => sock().deliver({ event: "text_chunk", slot_id: "s1", text: "inherited answer" }));
    act(() => chat().sendMessage("s1", "local question", stage === "uploading"
      ? [new File(["image"], "image.png", { type: "image/png" })] : undefined));
    getConversation.mockResolvedValue({ meta: {}, is_busy: false, turns: [
      turn("user", "run the audit", 1), turn("assistant", "inherited answer", 2),
    ] });
    changed("completed");
    act(() => sock().deliver({
      event: "prompt_done", slot_id: "s1",
      // Wakes have no client; inherited browser turns retain their origin.
      ...(stage === "sent" ? { client_id: "other-browser" } : {}),
    }));
    await settle();
    expect(getConversation).toHaveBeenCalledTimes(1);
    expect(messages().map((message) => message.text)).toContain("local question");

    if (stage === "uploading") {
      expect(sock().sent.filter((frame) => frame.action === "send_message")).toHaveLength(0);
      expect(messages().at(-1)?.attachments?.[0].url).toBe("blob:preview");
      await act(async () => releaseUpload({ id: "image-1" }));
    }
    expect(localClientId()).toEqual(expect.any(String));
    getConversation.mockResolvedValue({ meta: {}, is_busy: false, turns: [
      turn("user", "run the audit", 1), turn("assistant", "inherited answer", 2),
      turn("user", "local question", 3), turn("assistant", "local answer", 4),
    ] });
    act(() => sock().deliver({ event: "prompt_done", slot_id: "s1", client_id: localClientId() }));
    await settle();
    expect(getConversation).toHaveBeenCalledTimes(2);
    expect(messages().map((message) => message.text)).toEqual([
      "run the audit", "inherited answer", "local question", "local answer",
    ]);
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("re-reading the transcript on a reconnect", () => {
  it("shows a note that landed while the socket was down", async () => {
    await arrive();
    expect(messages().map((m) => m.text)).toEqual(["run the audit"]);

    // While nobody was listening, the delegation finished and wrote its
    // outcome into the transcript. The push went to a socket that was gone.
    getConversation.mockResolvedValue({
      meta: {},
      turns: [
        { role: "user", text: "run the audit", ts: "1", tool_calls: [] },
        {
          role: "system",
          text: "the audit finished",
          kind: "delegation",
          ts: "2",
          tool_calls: [],
        },
      ],
    });

    reconnect();
    act(() => {
      sock().deliver(roster());
    });
    await settle();

    expect(messages().map((m) => m.text)).toEqual([
      "run the audit",
      "the audit finished",
    ]);
    expect(messages()[1].kind).toBe("delegation");
  });

  it("does not duplicate what a live push already put on screen", async () => {
    await arrive();

    // The note arrived over the wire this time, so it is on screen as a live
    // message *and* recorded server-side. A reconnect must not show it twice.
    act(() => {
      sock().deliver({
        event: "system_note",
        slot_id: "s1",
        text: "the audit finished",
        kind: "delegation",
      });
    });
    getConversation.mockResolvedValue({
      meta: {},
      turns: [
        { role: "user", text: "run the audit", ts: "1", tool_calls: [] },
        {
          role: "system",
          text: "the audit finished",
          kind: "delegation",
          ts: "2",
          tool_calls: [],
        },
      ],
    });

    reconnect();
    act(() => {
      sock().deliver(roster());
    });
    await settle();

    expect(messages().map((m) => m.text)).toEqual([
      "run the audit",
      "the audit finished",
    ]);
  });

  it("keeps a note that streamed in while the re-read was in flight", async () => {
    await arrive();

    let release: (v: unknown) => void = () => {};
    getConversation.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );

    reconnect();
    act(() => {
      sock().deliver(roster());
    });
    // The re-read is out but not back, and the wire delivers a fresh note.
    act(() => {
      sock().deliver({
        event: "system_note",
        slot_id: "s1",
        text: "and the deploy is green",
        kind: "routine",
      });
    });
    await act(async () => {
      release({
        meta: {},
        turns: [{ role: "user", text: "run the audit", ts: "1", tool_calls: [] }],
      });
      await Promise.resolve();
      await Promise.resolve();
    });
    await settle();

    expect(messages().map((m) => m.text)).toEqual([
      "run the audit",
      "and the deploy is green",
    ]);
  });

  it("reads the transcript exactly once on the first connect", async () => {
    await arrive();

    expect(getConversation).toHaveBeenCalledTimes(1);
    expect(getConversation).toHaveBeenCalledWith("c1");

    // A roster the client asked for itself is not a reconnect: no re-read.
    act(() => {
      sock().deliver(roster());
    });
    await settle();

    expect(getConversation).toHaveBeenCalledTimes(1);
  });

  it("leaves the transcript alone when the re-read fails", async () => {
    await arrive();

    getConversation.mockRejectedValue(new Error("gateway is down"));

    reconnect();
    act(() => {
      sock().deliver(roster());
    });
    await settle();

    expect(messages().map((m) => m.text)).toEqual(["run the audit"]);

    // And the chat still works: the next reconnect tries again rather than
    // treating the slot as never-hydrated and re-prepending the whole thing.
    getConversation.mockResolvedValue({
      meta: {},
      turns: [
        { role: "user", text: "run the audit", ts: "1", tool_calls: [] },
        { role: "system", text: "back up", kind: "delegation", ts: "2", tool_calls: [] },
      ],
    });
    reconnect();
    act(() => {
      sock().deliver(roster());
    });
    await settle();

    expect(messages().map((m) => m.text)).toEqual(["run the audit", "back up"]);
  });
});
