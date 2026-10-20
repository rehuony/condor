/** Shared transcript contracts for live chat state, replay, and rendering. */

export interface ToolCall {
  tool_call_id: string;
  title: string;
  status: string;
}

/**
 * One step of a turn's run, in the order it happened (ARCH-330).
 *
 * A tool step carries the call's *id*, not the call: the call itself lives in
 * `ChatMessage.toolCalls` and keeps being patched there by `tool_call_update`,
 * so a copy here would be a second, stale answer to "what is that tool doing".
 * The renderer joins the two.
 */
export type RunStep =
  | { type: "thought"; text: string }
  | { type: "tool"; id: string };

export interface ChatMessage {
  id: string;
  /** A `system` message is not a bubble — it is a divider in the scrollback. */
  role: "user" | "assistant" | "system";
  text: string;
  toolCalls: ToolCall[];
  thought?: string;
  /**
   * How the reasoning and the tool calls interleaved (ARCH-330).
   *
   * `thought` and `toolCalls` say *what* the run held; this says in what
   * order, which neither of them can — a turn that thinks, calls, thinks again
   * and calls a second time is one merged string beside a flat list there.
   * Both are still carried, so nothing that only knows them changes.
   *
   * Absent means the order is not known: a turn hydrated from a transcript
   * written before the recorder kept it. The renderer falls back to what it
   * always drew — the reasoning, then the calls.
   */
  events?: RunStep[];
  /** System: "switch" | "error" | "delegation" | "resume" | "notification" |
   * "routine" | "secret_notice". */
  kind?: string;
  /**
   * The user redirected the agent while this answer was being written. The
   * partial stays on screen — its context survives into the next turn, so
   * nothing is actually lost — but it is marked so it does not read as a
   * complete answer that simply stopped making sense.
   */
  interrupted?: boolean;
  /**
   * This bubble is the one the current turn is still being written into.
   *
   * The flag lives in the transcript rather than in a ref beside it because
   * *every* decision about where a fragment goes is then made from the state
   * the fragment is folded into, inside the same updater. A pointer held
   * outside React had to be written when the updater ran and read when the
   * caller ran — two different moments — and any gap between them (a commit
   * that had not happened yet, an updater React re-ran) split one answer
   * across several bubbles.
   */
  open?: boolean;
  /**
   * When the turn was said, in epoch seconds — the same unit and clock the
   * stored transcript uses (`TurnEntry.ts`), so a hydrated message and a live
   * one are formatted by the same code. Absent only for a transcript recorded
   * before this was carried.
   */
  ts?: number;
  /**
   * Who produced this turn, as the backend stamps it — three states:
   * `undefined` is unattributed (a user line, a divider, or an assistant turn
   * recorded before attribution existed), `""` is the default agent, Condor,
   * and a slug is a bound Agent.
   *
   * Carried per turn rather than read off the conversation's binding because
   * the binding is last-write-wins: after a handover it names the agent that
   * took *over*, so every earlier answer was being credited to it.
   */
  agentSlug?: string;
  /**
   * What was handed over with this turn (FEAT-098).
   *
   * Two provenances, one shape: a bubble the composer just appended carries
   * local object URLs (`local: true`), because the browser already has the bytes
   * it read from the clipboard; a bubble hydrated from the transcript carries
   * the bearer-guarded route, which `useAuthedImage` fetches. The distinction
   * is on the item rather than on the message so the two can never be confused
   * for one another by a renderer that only sees a URL.
   */
  attachments?: ChatAttachment[];
}

/** One picture on a turn, as the transcript renders it. */
export interface ChatAttachment {
  url: string;
  mime: string;
  /** The URL is already an object URL for bytes in this tab; do not fetch it. */
  local?: boolean;
}
