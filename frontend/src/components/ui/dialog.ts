import type { KeyboardEvent } from "react";

/**
 * The one look every modal dialog shares: a dimmed, lightly blurred backdrop
 * and a raised panel that slides in, centred at every width.
 *
 * Class strings rather than a component, so each dialog keeps its own markup,
 * stacking order and dismissal rules and only takes on the shared surface.
 */
export const DIALOG_BACKDROP =
  "fixed inset-0 flex items-center justify-center bg-black/50 p-4 backdrop-blur-[2px] animate-dialog-backdrop motion-reduce:animate-none";

export const DIALOG_PANEL =
  "w-full overflow-y-auto rounded-2xl border border-[var(--color-border)] shadow-2xl shadow-black/20 animate-dialog-panel motion-reduce:animate-none";

/**
 * A layer that takes the whole viewport as a page of its own (a fullscreen
 * chart, the report browser, the config editor): no backdrop, the page ground
 * color, a column for header and body. `_TOP` stacks over an open layer.
 */
export const FULLSCREEN_LAYER = "fixed inset-0 z-50 flex flex-col bg-[var(--color-bg)]";
export const FULLSCREEN_LAYER_TOP = "fixed inset-0 z-[60] flex flex-col bg-[var(--color-bg)]";

/** Footer buttons: one size everywhere; on phones they split the row evenly. */
const BUTTON =
  "inline-flex h-9 items-center justify-center gap-1.5 rounded-lg px-4 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary)] disabled:cursor-not-allowed disabled:opacity-50 max-sm:w-full";

export const DIALOG_FOOTER = "grid grid-cols-2 gap-2 sm:flex sm:justify-end";
export const DIALOG_CANCEL = `${BUTTON} border border-[var(--color-border)] text-[var(--color-text)] hover:enabled:bg-[var(--color-surface-hover)]`;
export const DIALOG_DANGER = `${BUTTON} bg-[var(--color-red)] text-white hover:enabled:bg-[var(--color-red)]/90`;
export const DIALOG_PRIMARY = `${BUTTON} bg-[var(--color-primary)] text-[var(--on-primary)] hover:enabled:bg-[var(--color-primary)]/90`;

/** Keep modal keys away from page shortcuts and the sheet underneath it. */
export function handleDialogKeyDown(event: KeyboardEvent<HTMLElement>, pending: boolean, onCancel: () => void) {
  event.stopPropagation();
  if (event.key === "Escape") {
    event.preventDefault();
    if (!pending) onCancel();
  }
  if (event.key !== "Tab") return;

  const panel = event.currentTarget;
  const controls = Array.from(panel.querySelectorAll<HTMLElement>(
    'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex]',
  )).filter((control) => control.tabIndex >= 0 && !control.closest("[hidden], [inert]"));
  const first = controls[0];
  const last = controls.at(-1);
  if (!first || !controls.includes(document.activeElement as HTMLElement)
    || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
    event.preventDefault();
    (event.shiftKey ? last ?? panel : first ?? panel).focus({ preventScroll: true });
  }
}
