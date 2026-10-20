import type { ReactNode } from "react";

/**
 * "Nothing here" for a list, table or detail pane: one muted, centred line
 * with room above and below so it reads as a state and not as a stray row.
 *
 * Unavailable data is shown as unavailable, never as zero or as an empty
 * table, so almost every data view has one of these.
 */
export function EmptyState({ children, className }: { children: ReactNode; className?: string }) {
  const base = "py-8 text-center text-sm text-[var(--color-text-muted)]";
  return <p className={className ? `${base} ${className}` : base}>{children}</p>;
}
