import { useEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from "react";

/**
 * The scope panel's narrow-pane life: a bottom sheet opened from a toggle bar.
 *
 * On a wide pane the panel is a rail and this state is inert. Under
 * `narrow-pane:` it is a modal sheet — focus moves into it, Tab cycles inside
 * it, Escape and a resize back to a wide pane close it, and focus returns to
 * the toggle that opened it.
 */
export interface ScopeSheetState {
  open: boolean;
  setOpen: Dispatch<SetStateAction<boolean>>;
  panelRef: RefObject<HTMLDivElement | null>;
  toggleRef: RefObject<HTMLButtonElement | null>;
}

export function useScopeSheet(): ScopeSheetState {
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const media = window.matchMedia("(max-width: 767px), (min-width: 768px) and (max-width: 1023px) and (max-height: 500px)");
    if (!media.matches) return;
    const panel = panelRef.current;
    const toggle = toggleRef.current;
    panel?.querySelector<HTMLButtonElement>("[data-perf-scope-close]")?.focus();
    const onKey = (event: KeyboardEvent) => {
      // A confirmation above this panel owns Tab and Escape until it closes.
      const anotherModal = Array.from(document.querySelectorAll('[aria-modal="true"]'))
        .some((modal) => modal !== panel && !modal.contains(panel));
      if (document.querySelector('[role="listbox"]') || anotherModal) return;
      if (event.key === "Escape") { event.preventDefault(); setOpen(false); }
      if (event.key !== "Tab" || !panel) return;
      const elements = Array.from(panel.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]')).filter((element) => element.getClientRects().length > 0);
      const first = elements[0], last = elements.at(-1);
      if (event.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
    };
    const onResize = () => { if (!media.matches) setOpen(false); };
    document.addEventListener("keydown", onKey);
    media.addEventListener("change", onResize);
    return () => {
      document.removeEventListener("keydown", onKey);
      media.removeEventListener("change", onResize);
      toggle?.focus({ preventScroll: true });
    };
  }, [open]);
  return { open, setOpen, panelRef, toggleRef };
}
