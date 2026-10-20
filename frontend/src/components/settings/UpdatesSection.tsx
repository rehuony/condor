import type { ReactNode } from "react";

/** The secondary button a section header carries (Refresh, Check). */
export const SECTION_ACTION_CLASS =
  "flex shrink-0 items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-50";

/**
 * One block of Settings → Updates: a small caps title and a one-line description on
 * the left, the section's action on the right, its cards below. Condor and the
 * Hummingbot API share it so the two read as the same kind of thing.
 */
export function UpdatesSection({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
            {title}
          </h2>
          <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-muted)]">{description}</p>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
