/**
 * Routine toolbar buttons: one height, order and look at every pane width.
 * `aria-expanded` marks the panel or menu a button currently has open.
 */
const BASE =
  "inline-flex h-8 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border px-2.5 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary)] disabled:cursor-not-allowed disabled:opacity-50 [&>svg]:h-3.5 [&>svg]:w-3.5 [&>svg]:shrink-0";

export const ROUTINE_ACTION = `${BASE} border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] hover:enabled:bg-[var(--color-surface-hover)] aria-expanded:border-[var(--color-primary)]/40 aria-expanded:bg-[var(--color-primary)]/10 aria-expanded:text-[var(--color-primary)]`;

/** The run command: the one filled button in the toolbar. */
export const ROUTINE_PRIMARY_ACTION = `${BASE} border-transparent bg-[var(--color-primary)] text-[var(--on-primary)] hover:enabled:bg-[var(--color-primary)]/80`;
