import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";

import { BubbleGroup, type BubbleOption } from "@/components/perf/FilterBubbles";
import { GroupByPicker } from "@/components/perf/GroupByPicker";
import { PopulationToggle } from "@/components/perf/PopulationToggle";
import { IconButton } from "@/components/ui/IconButton";
import type { GroupAxis } from "@/lib/perf-grouping";
import type { Grain, Population } from "@/lib/perf-tree";

import { GRAINS, PERIODS, type PeriodKey } from "./options";

/** The narrowing the bubbles and the pair box apply to the tree. */
export interface ScopeFilterState {
  pair: string;
  ctrlTypes: string[];
  execTypes: string[];
  agents: string[];
}

/** What each bubble row can choose between. */
export interface ScopeFilterOptions {
  ctrlTypes: BubbleOption[];
  execTypes: BubbleOption[];
  agents: BubbleOption[];
}

interface ScopeFiltersProps {
  isCompact: boolean;
  setIsCompact: (compact: boolean) => void;
  population: Population;
  setPopulation: (next: Population) => void;
  urlGrouping: readonly GroupAxis[];
  setGrouping: (axes: readonly GroupAxis[]) => void;
  grain: Grain;
  setGrain: (grain: Grain) => void;
  period: PeriodKey;
  setPeriod: (period: PeriodKey) => void;
  filters: ScopeFilterState;
  setFilters: Dispatch<SetStateAction<ScopeFilterState>>;
  filterOptions: ScopeFilterOptions;
  filtersActive: boolean;
}

/**
 * The head of the scope panel: what is in scope, and what has been narrowed
 * out of it.
 *
 * Everything here describes the *tree* rather than the report — the panes to
 * the right are the same panes whichever way these are set, which is the point
 * (FEAT-086). The bubbles are where the `By bot / By type` toggle used to be,
 * and they do the job it did without spending a level of the tree on it.
 */
export function ScopeFilters({
  isCompact,
  setIsCompact,
  population,
  setPopulation,
  urlGrouping,
  setGrouping,
  grain,
  setGrain,
  period,
  setPeriod,
  filters,
  setFilters,
  filterOptions,
  filtersActive,
}: ScopeFiltersProps) {
  return (
    <div className="shrink-0 border-b border-[var(--color-border)]">
      <div className="flex items-center justify-between px-3 py-2.5 narrow-pane:hidden">
        {!isCompact && (
          <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
            Scope
          </span>
        )}
        <IconButton
          label={isCompact ? "Expand sidebar" : "Collapse sidebar"}
          size="sm"
          onClick={() => setIsCompact(!isCompact)}
        >
          {isCompact ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-3.5 w-3.5" />}
        </IconButton>
      </div>
      {/* Capped and scrollable: three expanded bubble groups over a
          hundred-run history would otherwise push the tree off a short
          window, and the tree is the thing the sidebar is for. */}
      {!isCompact && (
        <div className="flex max-h-[45vh] flex-col gap-1.5 overflow-y-auto scrollbar-thin px-2 pb-2 narrow-pane:max-h-none narrow-pane:overflow-visible narrow-pane:pt-2">
          <PopulationToggle population={population} onChange={setPopulation} />

          {/* Which question the tree is answering: owner, bot, pair or
              class. Beside the population toggle rather than among the
              bubbles, because it says what is being *compared* — the
              bubbles say which of it to keep (FEAT-107). */}
          <GroupByPicker grouping={urlGrouping} onChange={setGrouping} />

          {/* Which granularity of record the tree draws (ARCH-317). One of
              the panel's questions rather than one of its filters — the
              population says *whose* records, the bubbles say *what class*,
              and this says which *kind of thing* is being compared. A strip
              and not a bubble row because the three are one answer. */}
          <div className="flex gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-0.5">
            {GRAINS.map(({ key, label, hint }) => (
              <button
                key={key}
                type="button"
                onClick={() => setGrain(key)}
                aria-pressed={grain === key}
                title={hint}
                className={`flex-1 rounded px-1 py-1 text-[10px] font-medium transition-colors ${
                  grain === key
                    ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* The window a terminated fold reaches back over. Offered here
              and not for the live fleet, whose window is its own deploy —
              cutting a live fold at a week would report a PnL that its
              Realized tile disagrees with. */}
          {population === "terminated" && (
            <div className="flex gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-0.5">
              {(Object.keys(PERIODS) as PeriodKey[]).map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setPeriod(key)}
                  aria-pressed={period === key}
                  className={`flex-1 rounded px-1 py-1 text-[10px] font-medium transition-colors ${
                    period === key
                      ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  }`}
                >
                  {key}
                </button>
              ))}
            </div>
          )}

          {/* The executors page's filters, now narrowing the tree itself
              rather than one table under a total that ignored them. */}
          <input
            type="text"
            value={filters.pair}
            onChange={(e) => setFilters((f) => ({ ...f, pair: e.target.value }))}
            placeholder="Filter pair…"
            className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-[11px] transition-colors hover:border-[var(--color-primary)]/50 focus:border-[var(--color-primary)] focus:outline-none"
          />

          {/* One bubble row per question a reader asks of a fleet. Each is
              drawn only when it has something to choose between: a
              terminated side whose controllers all report the same
              (unknown) class is not a filter. *Which bot* is missing on
              purpose — the tree below draws a row per bot, and that row can
              be acted on (ARCH-316). */}
          {filterOptions.ctrlTypes.length > 1 && (
            <BubbleGroup
              // "Controller type" rather than "Type": the row under it is
              // "Executor type", and two rows of bubbles that look alike
              // need their names to say which is which.
              title="Controller type"
              hint="The class each controller is — pmm_simple, grid_strike, and so on. Executors are narrowed by the class of the controller that ran them; to pick them by what they are, use Executor type."
              options={filterOptions.ctrlTypes}
              selected={filters.ctrlTypes}
              onChange={(v) => setFilters((f) => ({ ...f, ctrlTypes: v }))}
            />
          )}
          {/* Not drawn in `Controllers` mode: there are no executors in the
              tree to pick between. `Controller type` stays in all three —
              it classes an executor by the controller that ran it, so it
              narrows whatever is on screen. */}
          {grain !== "controllers" && filterOptions.execTypes.length > 1 && (
            <BubbleGroup
              title="Executor type"
              hint="Picking one reports the executors themselves: a controller record covers every type it ever ran, so it steps aside and each row folds the matching executors instead."
              options={filterOptions.execTypes}
              selected={filters.execTypes}
              onChange={(v) => setFilters((f) => ({ ...f, execTypes: v }))}
            />
          )}
          {/* Whose this trading is. Third rather than first: the two above
              it are about *what* a record is, which is what a reader
              narrows by most days, and this one answers a question the
              tree already answers wherever it has grown an agent level. */}
          {filterOptions.agents.length > 1 && (
            <BubbleGroup
              title="Agent"
              hint="Which agent's loop owns each record — by the bot's namespace, or by the session an executor was tagged with. Unattributed is everything the fleet map credits to nobody, which on most servers is nearly all of it."
              options={filterOptions.agents}
              selected={filters.agents}
              onChange={(v) => setFilters((f) => ({ ...f, agents: v }))}
            />
          )}
          {filtersActive && (
            <button
              type="button"
              onClick={() => setFilters({ pair: "", ctrlTypes: [], execTypes: [], agents: [] })}
              className="self-start text-[10px] text-[var(--color-text-muted)] underline-offset-2 hover:text-[var(--color-text)] hover:underline"
            >
              Clear all filters
            </button>
          )}
        </div>
      )}
    </div>
  );
}
