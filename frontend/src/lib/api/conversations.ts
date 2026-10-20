/** Stored conversation listing, lookup and housekeeping. */

import { authFetch } from "../auth-token";
import { apiFetch } from "@/lib/api";

import type { DeploymentRow } from "./agents";

// ── Conversations ──
//
// A session is the live subprocess; a conversation is what was said. The
// second outlives the first, so this — not localStorage — is the source of
// truth for a transcript.

export interface ConversationMeta {
  id: string;
  user_id: number;
  /** Where it was born: "tg" | "web" | "mcp". */
  surface: string;
  title: string;
  agent_key: string;
  agent_slug: string;
  server_name: string | null;
  created_at: string;
  updated_at: string;
  turn_count: number;
  last_snippet: string;
  /** Running token total (FEAT-120). `{}` or absent on a conversation older
   *  than the measurement, which reads as "not measured", not as zero. */
  usage?: Partial<TokenUsage>;
}

/**
 * What a model read and wrote — the backend's `TokenUsage.to_dict()`.
 *
 * `input_tokens` is inclusive of cache on every backend, so `total_tokens` is
 * everything read and written; the two `cache_*` counters are subsets of it.
 * `cost_usd` is an estimate at API prices, never a spend — a Claude
 * subscription is not billed per token — and `unpriced_turns` counts the runs
 * no price was known for (a local model), which makes the cost a lower bound.
 * The `context_*` pair is the latest reading, not a sum.
 */
export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_usd: number;
  unpriced_turns: number;
  context_used: number | null;
  context_size: number | null;
  total_tokens: number;
}

export interface ConversationTurn {
  /** "user" | "assistant" | "system". */
  role: string;
  text: string;
  thought: string;
  tool_calls: { id?: string; title?: string; status?: string }[];
  /** System entries only: "switch" | "error" | "delegation" | "resume" |
   *  "notification". Same union as the backend's `TurnEntry.kind`. */
  kind: string;
  ts: number;
  /**
   * Who produced this turn — the backend's `TurnEntry` attribution, mirrored
   * here rather than dropped.
   *
   * It lives on the turn because the conversation's meta is last-write-wins: a
   * chat that hands over mid-way would otherwise credit its whole history to
   * whoever answered last, which is exactly what the transcript used to do.
   * `agent_key` is the model, `agent_slug` the bound Agent with `""` meaning
   * the default one, Condor. Both empty is a turn written before attribution
   * existed — unattributed, not "Condor". Optional because a stored line older
   * than the field parses without it.
   */
  agent_key?: string;
  agent_slug?: string;
  /** Assistant turns: how the stream ended; "" or absent = never reported. */
  stop_reason?: string;
  /**
   * Images attached to user, assistant or system notification turns (FEAT-098).
   *
   * The reference and never the payload — an id under this conversation's own
   * `attachments/` directory, its type and its size. The bytes come from the
   * bearer-guarded GET beside it, which is why they are fetched into an object
   * URL rather than pointed at by an `<img src>`. Absent on every turn recorded
   * before this existed, and on every turn that carried nothing.
   */
  attachments?: ConversationAttachment[];
  /**
   * The order the run happened in, as the recorder kept it (ARCH-330).
   *
   * A step is either a stretch of reasoning or a call, and a call step *names*
   * an entry of `tool_calls` rather than repeating it — so the detail has one
   * home and the two can never disagree. Absent or empty on every turn
   * recorded before this existed, which means "the order was not kept", not
   * "the agent did nothing": the flat fields are the fallback then.
   */
  events?: ConversationRunStep[];
}

/**
 * One step of a turn's run: reasoning, or a call named by its id.
 *
 * Deliberately not a discriminated union. This is wire data whose `type` a
 * newer backend is free to extend, and a union would let the compiler promise
 * a narrowing the JSON cannot honour; the reader tests the tag itself and
 * ignores a step it does not recognise.
 */
export interface ConversationRunStep {
  type: string;
  text?: string;
  id?: string;
}

/** One stored image on a turn. No filename: none is ever recorded. */
export interface ConversationAttachment {
  id: string;
  mime: string;
  bytes: number;
}

export interface ConversationDetail {
  /** Any active or queued turn, including Telegram and other browser tabs. */
  is_busy?: boolean;
  meta: ConversationMeta;
  turns: ConversationTurn[];
}

/**
 * What one conversation created, and whether it could have recorded it
 * (FEAT-110).
 *
 * `predates_ledger` separates two answers that look identical on screen and are
 * not: *this conversation deployed nothing*, and *this conversation ran before
 * Condor wrote down what it did*. Every conversation older than FEAT-105 is the
 * second one, and telling a reader the first about it would be a confident lie.
 */
export interface ConversationDeployments {
  deployments: DeploymentRow[];
  predates_ledger: boolean;
}

export const conversationsApi = {
  // ── Conversations (durable transcripts) ──
  //
  // Everything the user has ever said, across Telegram and the dashboard, in
  // one keyspace. A conversation is continuable long after its session died.

  listConversations: (limit = 100) =>
    apiFetch<ConversationMeta[]>(`/api/v1/conversations?limit=${limit}`),

  getConversation: (id: string, limit = 200) =>
    apiFetch<ConversationDetail>(
      `/api/v1/conversations/${encodeURIComponent(id)}?limit=${limit}`,
    ),

  /**
   * What this conversation put into the world (FEAT-110).
   *
   * The conversation-shaped sibling of a run's ledger, in the same
   * `DeploymentRow` shape, so the panel beside the chat and the one in the
   * agent's Lab are the same component reading the same wire.
   */
  getConversationDeployments: (id: string) =>
    apiFetch<ConversationDeployments>(
      `/api/v1/conversations/${encodeURIComponent(id)}/deployments`,
    ),

  renameConversation: (id: string, title: string) =>
    apiFetch<ConversationMeta>(
      `/api/v1/conversations/${encodeURIComponent(id)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ title }),
      },
    ),

  /**
   * Store one image against a conversation; the id goes on the send frame.
   *
   * Called at *send* time, not at paste time (FEAT-098): the composer holds the
   * browser's own `File` objects and previews them with `createObjectURL`, so
   * nothing is written to disk for a message that is never sent and there is no
   * orphan to sweep. `authFetch` because the body is `FormData` — forcing
   * `Content-Type: application/json` on it would be wrong.
   */
  uploadAttachment: async (
    conversationId: string,
    file: File,
  ): Promise<ConversationAttachment> => {
    const body = new FormData();
    body.append("file", file);
    const res = await authFetch(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/attachments`,
      { method: "POST", body },
    );
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `Upload failed: ${res.status}`);
    }
    return res.json();
  },

  /** Where the bytes of a stored attachment live. Bearer-guarded, so this is a
   *  URL for `useAuthedImage` to fetch, never one for an `<img src>`. */
  attachmentUrl: (conversationId: string, attachmentId: string) =>
    `/api/v1/conversations/${encodeURIComponent(conversationId)}/attachments/` +
    encodeURIComponent(attachmentId),

  /** Delete the stored transcript and its live sessions. Ending a session
   *  alone preserves the transcript. */
  deleteConversation: (id: string) =>
    apiFetch<{
      deleted: boolean;
      sessions_destroyed: number;
    }>(`/api/v1/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }),
};
