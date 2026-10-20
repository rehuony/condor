import { Download, Square, X } from "lucide-react";
import { useCallback, useMemo, useState, type FormEvent } from "react";

import {
  ExecutorTable,
  type SortDir,
  type SortKey,
} from "@/components/perf/ExecutorTable";
import { exportExecutorsCsv, type ExecutorDelete, type ExecutorStop } from "@/components/perf/executorActions";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import {
  DIALOG_BACKDROP,
  DIALOG_CANCEL,
  DIALOG_DANGER,
  DIALOG_FOOTER,
  DIALOG_PANEL,
} from "@/components/ui/dialog";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { type ExecutorInfo } from "@/lib/api";
import { stopKeepCopy } from "@/lib/executorStopCopy";
import { isExecutorActive } from "@/lib/formatters";

/**
 * The choice that makes stopping an executor two different actions.
 *
 * Keeping the position leaves the exposure on the exchange with nothing
 * managing it; closing it does not. Neither is recoverable by looking at the
 * result afterwards, so the dialog asks rather than defaulting quietly.
 *
 * What the choice *means* depends on what is selected — an LP executor closes
 * its pool position either way — so the wording comes from `stopKeepCopy`
 * rather than being stated once and hoped over. `executors` is the list the
 * ids are resolved against; without it the dialog falls back to the non-LP
 * wording it has always used.
 */
export function StopConfirmDialog({
  ids,
  executors = [],
  onConfirm,
  onCancel,
}: {
  ids: string[];
  executors?: ExecutorInfo[];
  onConfirm: (ids: string[], keepPosition: boolean) => void;
  onCancel: () => void;
}) {
  useEscapeKey(true, onCancel);
  const [keepPosition, setKeepPosition] = useState(false);
  const count = ids.length;
  const copy = useMemo(
    () => stopKeepCopy(executors.filter((ex) => ids.includes(ex.id))),
    [executors, ids],
  );

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    onConfirm(ids, keepPosition);
  };

  return (
    <div className={`${DIALOG_BACKDROP} z-50`} onClick={onCancel}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="stop-executor-title"
        className={`${DIALOG_PANEL} max-h-[85dvh] max-w-sm bg-[var(--color-surface)] p-5`}
        onClick={(e) => e.stopPropagation()}
      >
        <form onSubmit={handleSubmit}>
          <h3 id="stop-executor-title" className="text-base font-semibold">
            {count === 1 ? "Stop executor?" : `Stop ${count} executors?`}
          </h3>
          <label className="mt-3 flex cursor-pointer select-none items-center gap-2">
            <input
              type="checkbox"
              checked={keepPosition}
              onChange={(e) => setKeepPosition(e.target.checked)}
              className="h-4 w-4 rounded border-[var(--color-border)] accent-[var(--color-primary)]"
            />
            <span className="text-sm">{copy.label}</span>
          </label>
          <p className="mt-1 ml-6 text-xs text-[var(--color-text-muted)]">
            {keepPosition ? copy.checked : copy.unchecked}
          </p>

          <div className={`${DIALOG_FOOTER} mt-5`}>
            <button type="button" onClick={onCancel} autoFocus className={DIALOG_CANCEL}>
              Cancel
            </button>
            <button type="submit" className={DIALOG_DANGER}>
              Stop
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function DeleteConfirmDialog({ ids, onConfirm, onCancel, label, pending = false, error }: {
  label?: string | null;
  pending?: boolean;
  error?: string | null;
  ids: string[];
  onConfirm: (ids: string[]) => void;
  onCancel: () => void;
}) {
  return (
    <ConfirmDialog
      titleId="delete-executor-title"
      title={`Delete ${ids.length} terminated ${ids.length === 1 ? "record" : "records"}${label ? ` from ${label}` : ""}?`}
      confirmLabel={error ? "Retry failed" : "Delete"}
      pending={pending}
      error={error}
      onConfirm={() => onConfirm(ids)}
      onCancel={onCancel}
    >
      {/* The one thing a reader could get wrong: what this does not touch. */}
      <p>Orders and open positions are not affected.</p>
    </ConfirmDialog>
  );
}

/** What a selection lets you do to it, drawn only while there is one. */
function BulkActionBar({
  count,
  onStop,
  onExport,
  onClear,
  stopping,
  activeCount,
}: {
  count: number;
  onStop: () => void;
  onExport: () => void;
  onClear: () => void;
  stopping: boolean;
  activeCount: number;
}) {
  if (count === 0) return null;
  return (
    <div className="flex items-center gap-3 border-b border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 px-3 py-1.5">
      <span className="text-xs font-medium">{count} selected</span>
      <div className="flex-1" />
      <button
        onClick={onExport}
        className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 text-[11px] font-medium hover:bg-[var(--color-surface-hover)] transition-colors"
      >
        <Download className="h-3 w-3" />
        Export CSV
      </button>
      {activeCount > 0 && <button
        onClick={onStop}
        disabled={stopping}
        className="flex items-center gap-1.5 rounded-md bg-[var(--color-red)] px-2.5 py-1 text-[11px] font-medium text-white hover:opacity-90 transition-colors disabled:opacity-50"
      >
        <Square className="h-3 w-3" />
        {stopping ? "Stopping…" : "Stop Selected"}
      </button>}
      <button
        onClick={onClear}
        className="rounded p-1 hover:bg-[var(--color-surface-hover)] transition-colors"
        title="Clear selection"
        aria-label="Clear selection"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

/**
 * The executors under the current scope, as rows.
 *
 * This is the browser's bottom band for any scope whose subtree holds
 * executors — the same table the executors page drew, with the same sorting,
 * the same selection, the same bulk stop and the same CSV export, now reporting
 * whichever slice of the fleet the sidebar has selected rather than the whole
 * history at once.
 *
 * Only what is *active* can be stopped, so the bulk action filters the
 * selection down to those rather than issuing calls that would fail: a
 * selection dragged across a closed executor should not turn into an error
 * about it.
 */
export function ExecutorRows({
  executors,
  stop,
  deletion,
  selectedId,
  onSelect,
  rateFormatPnl,
  rateFormatValue,
  rateFormatDetailed,
}: {
  executors: ExecutorInfo[];
  stop: ExecutorStop;
  deletion?: ExecutorDelete;
  selectedId: string | null;
  onSelect: (ex: ExecutorInfo) => void;
  rateFormatPnl?: (val: number, quote: string) => string;
  rateFormatValue?: (val: number, quote: string) => string;
  rateFormatDetailed?: (val: number, quote: string) => string;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("timestamp");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const handleSort = useCallback((key: SortKey) => {
    setSortKey((prevKey) => {
      if (prevKey === key) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
        return prevKey;
      }
      setSortDir("desc");
      return key;
    });
  }, []);

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // The rows on screen, not every row ever selected: walking the sidebar
  // changes which executors are listed, and "select all" means the ones the
  // reader can see.
  const allSelected = executors.length > 0 && executors.every((ex) => selectedIds.has(ex.id));
  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) => {
      const everyOne = executors.length > 0 && executors.every((ex) => prev.has(ex.id));
      const next = new Set(prev);
      for (const ex of executors) {
        if (everyOne) next.delete(ex.id);
        else next.add(ex.id);
      }
      return next;
    });
  }, [executors]);

  const selected = useMemo(
    () => executors.filter((ex) => selectedIds.has(ex.id)),
    [executors, selectedIds],
  );

  const handleBulkStop = useCallback(
    () => stop.request(selected.filter((ex) => isExecutorActive(ex.status)).map((ex) => ex.id)),
    [selected, stop],
  );

  const handleStopOne = useCallback((id: string) => stop.request([id]), [stop]);

  return (
    <div className="flex min-h-0 flex-col">
      <BulkActionBar
        count={selected.length}
        onStop={handleBulkStop}
        onExport={() => exportExecutorsCsv(selected.length > 0 ? selected : executors)}
        onClear={() => setSelectedIds(new Set())}
        stopping={stop.pending}
        activeCount={selected.filter((ex) => isExecutorActive(ex.status)).length}
      />
      {stop.error && (
        <p className="px-3 py-1.5 text-[11px] text-[var(--color-red)] bg-[var(--color-red)]/5">
          {stop.error}
        </p>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
        <ExecutorTable
          executors={executors}
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={handleSort}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          onSelectAll={toggleSelectAll}
          allSelected={allSelected}
          onRowClick={onSelect}
          selectedExecutorId={selectedId}
          onStop={handleStopOne}
          stoppingIds={stop.stoppingIds}
          onDelete={deletion ? (id) => deletion.request([id]) : undefined}
          deletingIds={deletion?.deletingIds}
          rateFormatPnl={rateFormatPnl}
          rateFormatValue={rateFormatValue}
          rateFormatDetailed={rateFormatDetailed}
        />
      </div>
    </div>
  );
}
