import { ListTree, X } from "lucide-react";
import type { Dispatch, ReactNode, RefObject, SetStateAction } from "react";

/**
 * The button a narrow pane opens the scope sheet from. It sits at the start of
 * the report header, which already names the scope, so it carries no summary.
 */
export function ScopeSheetToggle({
  open,
  setOpen,
  toggleRef,
  onExpand,
}: {
  open: boolean;
  setOpen: Dispatch<SetStateAction<boolean>>;
  toggleRef: RefObject<HTMLButtonElement | null>;
  /** Opening the sheet must also expand a rail collapsed on a wider screen. */
  onExpand: () => void;
}) {
  return (
    <button ref={toggleRef} type="button" aria-expanded={open} onClick={() => {
      // Opening filters must also expand a rail collapsed on a wider screen.
      if (!open) onExpand();
      setOpen((open) => !open);
    }}
      data-perf-scope-toggle
      aria-label="Scope & filters"
      title="Scope & filters"
      className={`hidden h-8 w-8 shrink-0 items-center justify-center rounded-md border border-[var(--color-border)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] narrow-pane:inline-flex ${
        open ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]" : "text-[var(--color-text-muted)]"
      }`}>
      <ListTree className="h-4 w-4" />
    </button>
  );
}

/**
 * The scope panel itself: a rail beside the report on a wide pane, the sheet
 * described above on a narrow one. `children` is the filters and the tree.
 */
export function ScopeSheet({
  open,
  onClose,
  panelRef,
  compact,
  children,
}: {
  open: boolean;
  onClose: () => void;
  panelRef: RefObject<HTMLDivElement | null>;
  /** The rail is collapsed to its icon column. */
  compact: boolean;
  children: ReactNode;
}) {
  return (
    <>
      {open && (
        <div data-perf-scope-backdrop aria-hidden="true" onClick={onClose}
          className="hidden touch-none narrow-pane:fixed narrow-pane:inset-0 narrow-pane:z-[44] narrow-pane:block narrow-pane:bg-black/50" />
      )}
      <div
        ref={panelRef}
        role={open ? "dialog" : undefined}
        aria-modal={open ? true : undefined}
        aria-label={open ? "Scope and filters" : undefined}
        className={`flex min-h-0 shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] transition-all ${
          compact ? "w-sidebar-rail" : "w-sidebar"
        } ${
          // Narrow panes show the scope as a bottom sheet, only while asked for.
          open
            ? "narrow-pane:fixed narrow-pane:inset-x-0 narrow-pane:bottom-0 narrow-pane:z-[45] narrow-pane:max-h-[85dvh] narrow-pane:w-full narrow-pane:rounded-t-xl narrow-pane:border narrow-pane:border-b-0 narrow-pane:pb-[env(safe-area-inset-bottom)] narrow-pane:shadow-2xl narrow-pane:transition-none"
            : "narrow-pane:hidden"
        }`}
      >
        <div className="hidden shrink-0 items-center justify-between border-b border-[var(--color-border)] py-1 pl-4 pr-1 narrow-pane:flex">
          <strong className="text-sm">Scope &amp; filters</strong>
          <button type="button" data-perf-scope-close aria-label="Close filters" onClick={onClose}
            className="flex h-9 w-9 items-center justify-center rounded-md text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"><X className="h-4 w-4" /></button>
        </div>
        <div className="contents narrow-pane:block narrow-pane:min-h-0 narrow-pane:flex-1 narrow-pane:overflow-y-auto narrow-pane:overscroll-contain narrow-pane:[&_.truncate]:whitespace-normal">
          {children}
        </div>
        <div className="hidden shrink-0 border-t border-[var(--color-border)] p-3 narrow-pane:block">
          <button type="button" onClick={onClose}
            className="h-9 w-full rounded-md bg-[var(--color-primary)] px-4 text-sm font-medium text-[var(--on-primary)] transition-colors hover:bg-[var(--color-primary)]/80">View results</button>
        </div>
      </div>
    </>
  );
}
