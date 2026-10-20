/**
 * The shared fetch tools every API module in `lib/api/` builds on: an
 * authenticated JSON fetch that carries the HTTP status on its error, the
 * status reader, and the optional-parameter query-string builder.
 *
 * Endpoints and their types live in `lib/api/<domain>.ts` (bots, executors,
 * market, dex, agents, settings, ...); import the domain module you need.
 */
import { authHeaders } from "./auth-token";

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...authHeaders(),
    ...(init?.headers as Record<string, string>),
  };
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    // The status rides along on the Error so a caller can tell apart failures that
    // read the same as a message but call for different things from the operator —
    // a 501 from the API-settings routes means "this server is older than this
    // panel, upgrade it", not "something is broken". `message` is unchanged, so
    // every existing caller reads exactly what it read before.
    throw Object.assign(new Error(err.detail || `Request failed: ${res.status}`), {
      status: res.status,
      retryAfter: res.status === 429 ? retryAfterSeconds(res.headers.get("Retry-After")) : undefined,
    });
  }
  return res.json();
}

/** Retry-After accepts either a number of seconds or an HTTP date. */
function retryAfterSeconds(value: string | null): number | undefined {
  if (value === null) return undefined;
  const numeric = Number(value);
  if (value.trim() && Number.isFinite(numeric)) return Math.max(0, numeric);
  const date = Date.parse(value);
  return Number.isFinite(date) ? Math.max(0, (date - Date.now()) / 1000) : undefined;
}

/** The HTTP status of a failed `apiFetch`, when the thrown value carries one. */
export function errorStatus(error: unknown): number | undefined {
  const status = (error as { status?: unknown } | null)?.status;
  return typeof status === "number" ? status : undefined;
}

/**
 * The query string for a set of optional parameters, `?` included — or `""`
 * when none of them is set.
 *
 * Every optional query parameter in this file is skipped and stringified here,
 * so a field added to an endpoint's parameter type is carried without a second
 * edit. Spelling each name a second time in a `qs.set` line is what let a
 * filter compile cleanly and never reach the server (READ-333).
 *
 * `undefined`, `null` and `""` are omitted; everything else is `String()`d, so
 * a deliberate `0` is sent rather than dropped.
 */
export function query(params: object): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") qs.set(key, String(value));
  }
  const q = qs.toString();
  return q ? `?${q}` : "";
}