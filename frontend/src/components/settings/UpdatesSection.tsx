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

/**
 * A checkout's revision as both update cards print it: `branch @ commit`, three
 * monospace pieces a fixed gap apart, so the two cards line up character for
 * character whatever spacing their source strings came with. The branch and the
 * `@` are left off when the branch is not known.
 */
export function RepoRevision({
  branch,
  commit,
  title,
  className = "text-[var(--color-text)]",
}: {
  branch?: string | null;
  commit: string;
  /** The full revision, on hover. */
  title?: string;
  className?: string;
}) {
  return (
    <span className={`inline-flex items-baseline gap-1 font-mono ${className}`} title={title}>
      {branch && (
        <>
          <span>{branch}</span>
          <span className="text-[var(--color-text-muted)]">@</span>
        </>
      )}
      <span>{commit}</span>
    </span>
  );
}
