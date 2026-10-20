import { useCallback, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Bot, CheckCheck, Terminal, Trash2, Workflow, Zap } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { NOTIFICATIONS_KEY } from "@/hooks/useChatSocket";
import { api, type AppNotification, type NotificationsResponse } from "@/lib/api";
import { setAppBadge } from "@/lib/appBadge";
import { useAuth } from "@/lib/auth";
import { formatRelativeTime } from "@/lib/formatters";
import { onPushNavigate } from "@/lib/push";

const KIND_ICON: Record<string, typeof Bell> = {
  delegation: Workflow,
  routine: Zap,
  agent: Bot,
  system: Terminal,
};

/** A notification is one line in a dropdown; anything longer is a wall of text. */
const MAX_LINES = 3;

function preview(text: string): string {
  const lines = text.split("\n").filter((l) => l.trim());
  const head = lines.slice(0, MAX_LINES).join("\n");
  return lines.length > MAX_LINES ? `${head}…` : head;
}

/**
 * The dashboard's notification surface (FEAT-048).
 *
 * Everything Condor says when a background task finishes — a delegation
 * completing, a routine run ending, an agent's `send_notification`, the boot
 * notice — used to go to Telegram and nowhere else. This is where it lands for
 * a user who only has the browser open.
 *
 * Two sources, one cache. The history comes from `GET /notifications` on mount
 * and on a slow poll; live arrivals are written into the same react-query key
 * by the chat socket's `notification` handler — the socket `ChatProvider` opens
 * on every route, not just the chat workspace — which is why a notice that
 * arrived while the tab was open and one that arrived an hour ago are
 * indistinguishable here. That is the whole point: the store, not the socket,
 * is what guarantees it is seen, and the poll is what guarantees the store
 * catches up even when the socket is gone.
 */
export function NotificationBell() {
  const { token } = useAuth();
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const close = useCallback(() => setOpen(false), []);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data } = useQuery<NotificationsResponse>({
    queryKey: NOTIFICATIONS_KEY,
    queryFn: () => api.getNotifications(50),
    // A push keeps this fresh while a tab is open; the poll is the floor for
    // when it cannot — a dropped socket, a proxy that killed the upgrade — so
    // the badge is right within a minute regardless. Slow on purpose, and not
    // at all in a background tab: react-query's own refetch-on-focus covers
    // the user who comes back.
    staleTime: 60_000,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    enabled: !!token,
  });

  const items = data?.items ?? [];
  const unread = data?.unread ?? 0;

  // The dock icon carries the same number as the bell (FEAT-082). Not a second
  // source of truth: it is a second render of the count this component already
  // holds, so it cannot drift from what the dropdown shows, and it follows the
  // bell through the poll, a live socket write and marking everything read.
  useEffect(() => setAppBadge(unread), [unread]);

  // A click on an OS notification lands here too (FEAT-083). `sw.js` focuses
  // the window it found and posts the link rather than calling `openWindow`,
  // because a second window is a second live chat WebSocket for the same user
  // — the runtime twin of the manifest's `navigate-existing`. This component is
  // where it belongs: it is mounted on every route and it already owns what
  // happens when someone opens a notification.
  useEffect(() => onPushNavigate((link) => navigate(link)), [navigate]);

  const markRead = useMutation({
    mutationFn: (ids?: string[]) => api.markNotificationsRead(ids),
    onSuccess: (res) => {
      queryClient.setQueryData<NotificationsResponse>(NOTIFICATIONS_KEY, (prev) =>
        prev
          ? { items: prev.items.map((n) => ({ ...n, read: true })), unread: res.unread }
          : prev,
      );
    },
  });

  // Clearing deletes the history for good, so it asks first, outside the menu.
  const [confirmClear, setConfirmClear] = useState(false);
  const clearAll = useMutation({
    mutationFn: () => api.clearNotifications(),
    onSuccess: () => {
      queryClient.setQueryData<NotificationsResponse>(NOTIFICATIONS_KEY, { items: [], unread: 0 });
      setConfirmClear(false);
    },
  });

  const toggle = () => {
    const next = !open;
    setOpen(next);
    // Opening the list *is* reading it. The badge is there to pull the user in,
    // so leaving it lit after they looked would train them to ignore it.
    if (next && unread > 0) markRead.mutate(undefined);
  };

  const openItem = (n: AppNotification) => {
    if (!n.link) return;
    setOpen(false);
    navigate(n.link);
  };

  return (
    <>
      <button
        ref={setAnchor}
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="menu"
        className="relative rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-accent)]"
        title={unread > 0 ? `${unread} unread notification(s)` : "Notifications"}
      >
        <Bell className="h-4 w-4" />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-[var(--color-red)] px-1 text-[9px] font-bold leading-none text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={close}
        align="right"
        role="menu"
        className="w-[360px] py-1"
      >
        {/* A narrow menu keeps the commands as icons; the words stay for screen readers. */}
        <div className="@container/notif-header flex items-center justify-between gap-2 px-3 py-1.5">
          <span className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Notifications
          </span>
          {items.length > 0 && (
            <span className="flex items-center gap-3">
              <button
                onClick={() => {
                  setOpen(false);
                  clearAll.reset();
                  setConfirmClear(true);
                }}
                className="flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] hover:text-[var(--color-red)]"
                title="Remove all notifications"
              >
                <Trash2 className="h-3 w-3" />
                <span className="@max-[280px]/notif-header:sr-only">Clear all</span>
              </button>
              <button
                onClick={() => markRead.mutate(undefined)}
                className="flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] hover:text-[var(--color-accent)]"
                title="Mark all as read"
              >
                <CheckCheck className="h-3 w-3" />
                <span className="@max-[280px]/notif-header:sr-only">Mark all read</span>
              </button>
            </span>
          )}
        </div>

        {items.length === 0 && (
          <div className="px-3 py-6 text-center text-xs text-[var(--color-text-muted)]">
            Nothing yet. Finished tasks show up here.
          </div>
        )}

        {items.map((n) => {
          const Icon = KIND_ICON[n.kind] ?? Bell;
          return (
            <button
              key={n.id}
              onClick={() => openItem(n)}
              className={`flex w-full items-start gap-2.5 px-3 py-2 text-left transition-colors hover:bg-[var(--color-surface-hover)] ${
                n.link ? "cursor-pointer" : "cursor-default"
              } ${n.read ? "" : "bg-[var(--color-primary)]/5"}`}
            >
              <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
              <span className="min-w-0 flex-1">
                {n.title && (
                  <span className="block truncate text-xs font-medium">{n.title}</span>
                )}
                <span className="block whitespace-pre-wrap break-words text-xs text-[var(--color-text)]">
                  {preview(n.text)}
                </span>
                <span className="mt-0.5 block text-[10px] text-[var(--color-text-muted)]">
                  {formatRelativeTime(n.ts)}
                </span>
              </span>
              {!n.read && (
                <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-primary)]" />
              )}
            </button>
          );
        })}
      </AnchoredMenu>

      {confirmClear && (
        <ConfirmDialog
          title="Clear all notifications?"
          confirmLabel="Clear all"
          pendingLabel="Clearing…"
          pending={clearAll.isPending}
          error={clearAll.error instanceof Error ? clearAll.error.message : null}
          onConfirm={() => clearAll.mutate()}
          onCancel={() => setConfirmClear(false)}
        >
          <p>This can't be undone.</p>
        </ConfirmDialog>
      )}
    </>
  );
}
