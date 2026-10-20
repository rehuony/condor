import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useDismissOnOutsideClick } from "@/hooks/useDismissOnOutsideClick";
import { useEscapeKey } from "@/hooks/useEscapeKey";

interface Props {
  /** The button the panel hangs off. */
  anchor: HTMLElement | null;
  open: boolean;
  onClose: () => void;
  /** Which edge of the panel lines up with the same edge of the anchor. */
  align?: "left" | "right";
  /**
   * Tie the panel's width to its trigger, which `w-full` cannot do once the
   * panel is portalled — 100% would be of `document.body`. `"exact"` for a
   * select, whose menu must line up with its field; `"min"` for a menu that
   * may outgrow its trigger but never be narrower.
   */
  matchAnchorWidth?: "exact" | "min";
  /**
   * A px ceiling applied on top of the measured room, for a menu that is meant
   * to stay compact even when the viewport could fit more.
   */
  maxHeight?: number;
  /** Readable width floor, clamped to the visual viewport. */
  minWidth?: number;
  /** Keep a wider menu inside its pane without clipping it vertically. */
  horizontalBoundary?: HTMLElement | null;
  /**
   * Distance between the trigger and the panel, in px. Defaults to a floating
   * 6; a menu drawn as a continuation of its trigger passes ~1.
   */
  gap?: number;
  /** Width utility for the panel, e.g. `w-44`. */
  className?: string;
  role?: React.AriaRole;
  children: React.ReactNode;
}

/** Default distance between trigger and panel, and between panel and viewport edge. */
const DEFAULT_GAP = 6;
const EDGE = 8;
/**
 * Below this much room the panel would open as a useless sliver, so it is worth
 * flipping above the trigger instead — the case that matters is the last field
 * in a config panel, which sits just above the launch footer.
 */
const MIN_ROOM = 160;

/** A responsive ancestor can hide the trigger while its portal stays mounted. */
function isVisibleAnchor(anchor: HTMLElement): boolean {
  if (!anchor.isConnected) return false;
  for (let node: HTMLElement | null = anchor; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") return false;
  }
  return true;
}

/**
 * A dropdown panel that no ancestor can clip.
 *
 * An `absolute` menu is still a child of its trigger, so any `overflow-hidden`
 * on the way up cuts it off. That is exactly what happened to the DEX chain and
 * venue pickers: the browser wraps its header and pool table in one clipping
 * card, and on a chain with no pools that card is barely taller than the header
 * itself — so most of the chain list was invisible and unclickable, on the very
 * screen where switching chains is the only thing left to do.
 *
 * A scrolling ancestor fails differently and just as badly. The executor and LP
 * config forms live in a `flex-1 overflow-y-auto` panel: an absolute menu there
 * does extend the scroller's scrollHeight, so the options are reachable — but
 * nothing scrolls for you, so a user already at the bottom of the panel opens
 * the select and sees a few pixels of list and no cue that more scroll room
 * just appeared.
 *
 * Rendering into `document.body` at fixed coordinates escapes every clipping
 * and scrolling ancestor. The trade is that the panel no longer moves with the
 * page, so it is re-anchored on scroll and resize instead — and that tab order
 * no longer follows visual order, which is why the panel also takes focus on
 * open and hands it back on close (see the focus effect below).
 */
export function AnchoredMenu({
  anchor,
  open,
  onClose,
  align = "left",
  matchAnchorWidth,
  maxHeight,
  minWidth = 0,
  horizontalBoundary,
  gap = DEFAULT_GAP,
  className = "",
  role,
  children,
}: Props) {
  const menuRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  useLayoutEffect(() => { onCloseRef.current = onClose; });
  const [pos, setPos] = useState<{
    top?: number;
    bottom?: number;
    left?: number;
    right?: number;
    maxHeight: number;
    maxWidth: number;
    width?: number;
    minWidth?: number;
  } | null>(null);

  // Stale coordinates from a previous open are harmless: the panel renders
  // nothing while closed, and this runs before the browser paints the reopen.
  useLayoutEffect(() => {
    if (!open || !anchor) return;
    const place = () => {
      if (!isVisibleAnchor(anchor)) {
        setPos(null);
        onCloseRef.current();
        return;
      }
      const r = anchor.getBoundingClientRect();
      const viewport = window.visualViewport;
      const boundary = horizontalBoundary?.getBoundingClientRect();
      const leftEdge = Math.max((viewport?.offsetLeft ?? 0) + EDGE, boundary ? boundary.left + EDGE : -Infinity);
      const topEdge = (viewport?.offsetTop ?? 0) + EDGE;
      const rightEdge = Math.min((viewport?.offsetLeft ?? 0) + (viewport?.width ?? window.innerWidth) - EDGE, boundary ? boundary.right - EDGE : Infinity);
      const bottomEdge = (viewport?.offsetTop ?? 0) + (viewport?.height ?? window.innerHeight) - EDGE;
      const below = bottomEdge - r.bottom - gap;
      const above = r.top - gap - topEdge;
      // Open upward only when downward is cramped *and* upward is roomier —
      // otherwise the familiar direction wins.
      const cap = (room: number) =>
        Math.max(maxHeight ? Math.min(room, maxHeight) : room, 0);
      const vertical =
        below < Math.min(MIN_ROOM, above)
          ? { bottom: window.innerHeight - r.top + gap, maxHeight: cap(above) }
          : { top: r.bottom + gap, maxHeight: cap(below) };
      const availableWidth = Math.max(0, rightEdge - leftEdge);
      const panelWidth = Math.min(availableWidth, Math.max(minWidth, matchAnchorWidth === "exact"
        ? r.width : Math.max(matchAnchorWidth === "min" ? r.width : 0, menuRef.current?.getBoundingClientRect().width ?? r.width)));
      const left = Math.max(leftEdge, Math.min(align === "right" ? r.right - panelWidth : r.left, rightEdge - panelWidth));
      setPos({
        ...vertical,
        left,
        maxWidth: availableWidth,
        width: matchAnchorWidth === "exact" ? panelWidth : undefined,
        minWidth: Math.min(availableWidth, Math.max(minWidth, matchAnchorWidth === "min" ? r.width : 0)) || undefined,
      });
    };
    place();
    const frame = requestAnimationFrame(place);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(place);
    observer?.observe(anchor);
    if (horizontalBoundary) observer?.observe(horizontalBoundary);
    window.visualViewport?.addEventListener("resize", place);
    window.visualViewport?.addEventListener("scroll", place);
    // Capture phase: the page's own scroll containers do not bubble scroll.
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
      window.visualViewport?.removeEventListener("resize", place);
      window.visualViewport?.removeEventListener("scroll", place);
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, anchor, align, matchAnchorWidth, maxHeight, minWidth, horizontalBoundary, gap]);

  // Tab order follows the DOM, and the DOM now says the panel is a sibling of
  // the whole app rather than a child of its trigger. Without this, Tab from an
  // open menu walks into whatever control comes next *on the page*, leaving the
  // options unreachable from the keyboard. Runs after the layout effect above
  // on purpose: the panel is not in the DOM until `pos` is set.
  const placed = pos !== null;
  useEffect(() => {
    if (!open || !placed) return;
    const panel = menuRef.current;
    if (!panel) return;
    // Preserve an option already focused by a select. Search inputs do not
    // claim focus on open, so touch devices keep their keyboard closed.
    if (!panel.contains(document.activeElement)) {
      // The layout effect just positioned it; scrolling it into view would
      // only fight that.
      panel.focus({ preventScroll: true });
    }
    return () => {
      // Restore only when the panel was still the thing holding focus: removing
      // the focused node drops focus to <body>, whereas a mousedown that landed
      // on some other control has already moved focus there, and pulling it
      // back to the trigger would undo the user's own click.
      const active = document.activeElement;
      if (active && active !== document.body) return;
      if (anchor && isVisibleAnchor(anchor)) anchor.focus({ preventScroll: true });
    };
  }, [open, placed, anchor]);

  useDismissOnOutsideClick(open, onClose, [menuRef, anchor]);
  useEscapeKey(open, onClose);

  if (!open || !pos) return null;

  return createPortal(
    <div
      ref={menuRef}
      role={role}
      onKeyDown={(event) => {
        if (role === "menu" && ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
          const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []);
          const current = items.indexOf(document.activeElement as HTMLButtonElement);
          const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
            : event.key === "ArrowDown" ? (current + 1) % items.length
              : (current <= 0 ? items.length : current) - 1;
          event.preventDefault();
          event.stopPropagation();
          items[next]?.focus({ preventScroll: true });
        }
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }
      }}
      // Focusable on open without joining the tab order itself, so Tab from the
      // panel steps into the options rather than skipping the menu entirely.
      tabIndex={-1}
      style={{
        position: "fixed",
        top: pos.top,
        bottom: pos.bottom,
        left: pos.left,
        right: pos.right,
        // Never taller than the room on the side it opened to, so a long list
        // scrolls inside the panel rather than running off the window.
        maxHeight: pos.maxHeight,
        maxWidth: pos.maxWidth,
        width: pos.width,
        minWidth: pos.minWidth,
      }}
      className={`z-[60] overflow-y-auto rounded-lg border border-[var(--color-border)] focus:outline-none bg-[var(--color-surface)] shadow-xl ${className}`}
    >
      {children}
    </div>,
    document.body,
  );
}
