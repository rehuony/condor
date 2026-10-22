import { ChevronRight } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";

import type { FloorBucket } from "@/components/agent/floor/floor";
import type { ExecutorDelete, ExecutorStop } from "@/components/perf/executorActions";
import { ExecutorRows } from "@/components/perf/ExecutorRows";
import { ExecutorDetails } from "@/components/perf/ExecutorTable";
import { PositionsTable } from "@/components/perf/PositionsTable";
import { ScopeBreakdowns } from "@/components/perf/ScopeBreakdowns";
import type { ControllerInfo } from "@/lib/api/bots";
import type { ExecutorInfo } from "@/lib/api/executors";
import type { PositionRow } from "@/lib/perf-positions";
import type { PerfLeaf } from "@/lib/perf-tree";

/** Which of the bottom band's occupants is showing, if any. */
export type BandKey = "positions" | "executors" | "breakdown" | null;

/** One of the bottom band's headers: a disclosure that is also a switch. */
function BandTab({
  label,
  open,
  onClick,
  ...rest
}: {
  label: string;
  open: boolean;
  onClick: () => void;
} & Record<`data-${string}`, unknown>) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={open}
      className={`flex h-10 items-center gap-1.5 px-3 text-[11px] font-medium uppercase tracking-wider transition-colors hover:bg-[var(--color-surface-hover)] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--color-primary)] ${
        open ? "text-[var(--color-text)]" : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      }`}
      {...rest}
    >
      <ChevronRight className={`h-3 w-3 transition-transform ${open ? "rotate-90" : ""}`} />
      {label}
    </button>
  );
}

interface BottomBandProps {
  /** The tab row, which ends the chart's section, or the open occupant below it. */
  part: "tabs" | "content";
  band: BandKey;
  toggleBand: (next: Exclude<BandKey, null>) => void;
  /** The tab row's action area, filled by the open occupant (a selection's toolbar). */
  actionSlot: HTMLDivElement | null;
  setActionSlot: (el: HTMLDivElement | null) => void;
  /**
   * The scope's one executor, when the scope is a single executor. Its row
   * would only repeat the page above, so the tab shows its details instead of
   * a one-row table and nothing opens the side panel.
   */
  scopeExecutor?: ExecutorInfo;
  positionRows: PositionRow[];
  scopedExecutors: ExecutorInfo[];
  scopedLeaves: PerfLeaf[];
  byPair: FloorBucket[];
  byVenue: FloorBucket[];
  currencySymbol: string;
  activeCtrl: ControllerInfo | undefined;
  stop: ExecutorStop;
  deletion: ExecutorDelete;
  /** The executor whose detail panel is open, if any. */
  detail: ExecutorInfo | null;
  setDetail: Dispatch<SetStateAction<ExecutorInfo | null>>;
  rateFormatPnl?: (val: number, quote: string) => string;
  rateFormatValue?: (val: number, quote: string) => string;
  rateFormatDetailed?: (val: number, quote: string) => string;
}

/**
 * The bottom band (FEAT-085, FEAT-086).
 *
 * A one-line header pinned under the chart, opening over it when
 * the reader wants a breakdown. Three things can be underneath a
 * scope — the positions it is holding, the executors that make it
 * up, and the same records cut by instrument and by venue
 * (FEAT-116) — and they share one band rather than stacking, so
 * the chart's height depends on the strip alone and not on what
 * the selected node happens to have.
 *
 * Both are tables rather than grids of cards: at fleet scope these
 * are dozens or thousands of one-line facts, and a row each is
 * what makes them comparable at a glance.
 *
 * Drawn in two parts: the tab row (`part="tabs"`) under the chart, and the
 * open occupant (`part="content"`) below it at its full height. Opening or
 * closing a tab only adds or removes content under the row, so it never
 * resizes the chart or moves the page.
 */
export function BottomBand({
  part,
  band,
  toggleBand,
  actionSlot,
  setActionSlot,
  scopeExecutor,
  positionRows,
  scopedExecutors,
  scopedLeaves,
  byPair,
  byVenue,
  currencySymbol,
  activeCtrl,
  stop,
  deletion,
  detail,
  setDetail,
  rateFormatPnl,
  rateFormatValue,
  rateFormatDetailed,
}: BottomBandProps) {
  if (positionRows.length === 0 && scopedExecutors.length === 0 && scopedLeaves.length === 0) {
    return null;
  }
  if (part === "tabs") {
    return (
      <div
        className={`flex h-10 shrink-0 items-center overflow-hidden border border-[var(--color-border)] bg-[var(--color-surface)] ${
          // A transparent bottom edge rather than none: the row keeps its
          // height, so opening a tab cannot nudge the chart above.
          band ? "rounded-t-lg border-b-transparent" : "rounded-lg"
        }`}
      >
        {positionRows.length > 0 && (
          <BandTab
            label={`Positions held (${positionRows.length})`}
            open={band === "positions"}
            onClick={() => toggleBand("positions")}
            data-positions-toggle
          />
        )}
        {scopedExecutors.length > 0 && (
          <BandTab
            label={scopeExecutor ? "Details" : `Executors (${scopedExecutors.length})`}
            open={band === "executors"}
            onClick={() => toggleBand("executors")}
            data-executors-toggle
          />
        )}
        {/* Offered wherever there is a spine to cut, which is every
            scope with a record in it — including a single controller,
            whose one instrument and one venue is a true answer. */}
        {scopedLeaves.length > 0 && (
          <BandTab
            label="Breakdown"
            open={band === "breakdown"}
            onClick={() => toggleBand("breakdown")}
            data-breakdown-toggle
          />
        )}
        <div ref={setActionSlot} className="ml-auto flex min-w-0 items-center pr-2" />
      </div>
    );
  }
  if (!band) return null;
  // The tab row above carries the top border; each occupant draws its own
  // divider line under it.
  return (
    <div className="overflow-hidden rounded-b-lg border border-t-0 border-[var(--color-border)] bg-[var(--color-surface)]">
      {band === "executors" && scopeExecutor && (
        <div className="border-t border-[var(--color-border)]/60 p-3">
          <ExecutorDetails
            executor={scopeExecutor}
            layout="columns"
            rateFormatPnl={rateFormatPnl}
            rateFormatValue={rateFormatValue}
            rateFormatDetailed={rateFormatDetailed}
          />
        </div>
      )}

      {band === "executors" && !scopeExecutor && scopedExecutors.length > 0 && (
        <div className="border-t border-[var(--color-border)]/60">
          <ExecutorRows
            actionSlot={actionSlot}
            executors={scopedExecutors}
            stop={stop}
            deletion={deletion}
            selectedId={detail?.id ?? null}
            onSelect={setDetail}
            rateFormatPnl={rateFormatPnl}
            rateFormatValue={rateFormatValue}
            rateFormatDetailed={rateFormatDetailed}
          />
        </div>
      )}

      {band === "breakdown" && scopedLeaves.length > 0 && (
        <ScopeBreakdowns
          byPair={byPair}
          byVenue={byVenue}
          symbol={currencySymbol}
        />
      )}

      {band === "positions" && positionRows.length > 0 && (
        <PositionsTable
          rows={positionRows}
          currencySymbol={currencySymbol}
          // Every row would repeat the controller's name when the
          // scope already is one controller.
          showController={!activeCtrl}
        />
      )}
    </div>
  );
}
