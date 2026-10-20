/** Notification channels and the delivery log. */

import { apiFetch } from "@/lib/api";

/** A finished background task, addressed to the user rather than to a chat. */
export interface AppNotification {
  id: string;
  user_id: number;
  ts: number;
  /** "delegation" | "routine" | "agent" | "system" */
  kind: string;
  text: string;
  title?: string | null;
  /** A dashboard route to open, e.g. "/routines?tab=reports". */
  link?: string | null;
  read: boolean;
}

/** One browser on one device, as Settings lists it (FEAT-083).
 *
 * No key material: the store holds the browser's public key and auth secret so
 * it can encrypt *to* it, and there is no reader for them out here. */
export interface PushSubscriptionRow {
  /** The push service URL. Also the identity of the row. */
  endpoint: string;
  /** "Chrome on macOS" — best-effort, so a user can tell devices apart. */
  label: string;
  created: number;
}

export interface NotificationsResponse {
  items: AppNotification[];
  unread: number;
}

export const notificationsApi = {
  // ── Notifications (FEAT-048) ──

  /** The bell's history. Scoped to the JWT server-side — there is no user param. */
  getNotifications: (limit = 50) =>
    apiFetch<NotificationsResponse>(`/api/v1/notifications?limit=${limit}`),

  /** Mark some notifications read, or all of them when `ids` is omitted. */
  markNotificationsRead: (ids?: string[]) =>
    apiFetch<{ unread: number }>("/api/v1/notifications/read", {
      method: "POST",
      body: JSON.stringify({ ids: ids ?? null }),
    }),

  /** Delete all of the caller's notifications. */
  clearNotifications: () =>
    apiFetch<{ removed: number; unread: number }>("/api/v1/notifications", { method: "DELETE" }),

  // ── Web Push (FEAT-083) ──
  //
  // The bell with the window closed. Every one of these is scoped to the JWT
  // server-side: an endpoint in a body is a device to store, never an
  // authorization to touch someone else's row.

  /** The `applicationServerKey` this browser needs before it can subscribe. */
  getVapidKey: () => apiFetch<{ public_key: string }>("/api/v1/push/vapid"),

  /** The caller's own subscribed devices. */
  getPushSubscriptions: () =>
    apiFetch<{ items: PushSubscriptionRow[] }>("/api/v1/push/subscriptions"),

  /** Register (or re-register) one browser. Upserts on `endpoint`. */
  pushSubscribe: (body: {
    endpoint: string;
    p256dh: string;
    auth: string;
    label: string;
  }) =>
    apiFetch<{ subscribed: boolean; items: PushSubscriptionRow[] }>(
      "/api/v1/push/subscribe",
      { method: "POST", body: JSON.stringify(body) },
    ),

  /** Forget one of the caller's own devices. */
  pushUnsubscribe: (endpoint: string) =>
    apiFetch<{ removed: boolean; items: PushSubscriptionRow[] }>(
      "/api/v1/push/unsubscribe",
      { method: "POST", body: JSON.stringify({ endpoint }) },
    ),
};
