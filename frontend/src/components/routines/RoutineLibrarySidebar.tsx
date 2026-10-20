import { Brain, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Clock, PanelLeftClose, PanelLeftOpen, Search, Square, X } from "lucide-react";
import { useMemo, useState } from "react";

import { IconButton } from "@/components/ui/IconButton";
import { SelectField } from "@/components/ui/select-field";
import type { RoutineInfo, RoutineInstance } from "@/lib/api/routines";
import { formatRelativeTime, toMs } from "@/lib/formatters";
import { formatInterval, formatRoutineName, formatSlug, routineAgent, routineAgents } from "@/lib/routineUtils";

/** What the latest report of a routine says about it, for the card's footer. */
export interface RoutineReportInfo {
  title: string;
  created_at: string;
  count: number;
  tags: string[];
}

/**
 * The library's left column: whose routines, which one, and what has been run.
 *
 * This is the `/routines` launcher page, folded into the browser it used to
 * open (FEAT-091). The page was a grid of cards over a strip of runs, and
 * clicking a card threw both away for a full-screen browser whose own sidebar
 * was a flat list — two renderings of one list, one of them reachable only by
 * leaving the other. The cards, the scope filters, the search box and the runs
 * strip live here now, beside the report they pick, so the page *is* the
 * browser and there is nothing to open.
 *
 * Collapsed it is the 48px rail the workspace pane opens on, which is the one
 * thing that fits beside a conversation.
 */
export function RoutineLibrarySidebar({
  routines,
  listed,
  instances,
  activeSource,
  onSelect,
  scope,
  onScopeChange,
  search,
  onSearchChange,
  compact,
  onToggleCompact,
  onStopInstance,
  stopping = false,
  listRef,
  reportInfo,
  page = false,
  headerAction,
}: {
  /** Every routine, for the scope counts — not just the ones in scope. */
  routines: RoutineInfo[];
  /** What the list renders: the picked scope, narrowed by the search box. */
  listed: RoutineInfo[];
  instances: RoutineInstance[];
  activeSource: string;
  onSelect: (name: string) => void;
  scope: string;
  onScopeChange: (next: string) => void;
  search: string;
  onSearchChange: (next: string) => void;
  compact: boolean;
  onToggleCompact: () => void;
  onStopInstance: (instanceId: string) => void;
  stopping?: boolean;
  /** The browser scrolls the active card into view through this. */
  listRef: React.RefObject<HTMLDivElement | null>;
  reportInfo: Map<string, RoutineReportInfo>;
  /** The page has room for a wider library and its own picker below md. */
  page?: boolean;
  /** A command over the listed routines, drawn in the header (Run All). */
  headerAction?: React.ReactNode;
}) {
  const agents = useMemo(() => routineAgents(routines), [routines]);
  const condorCount = routines.filter((r) => !routineAgent(r)).length;
  const agentCount = routines.length - condorCount;
  const category = scope === "all" ? "all" : scope === "condor" || scope === "routine" ? "condor" : "agent";

  const live = (status: string) => status === "running" || status === "scheduled";

  // Every run this process still holds, newest first — not just the ones still
  // going. A one-shot that takes 30s was only ever visible for those 30s, and a
  // run that rendered no report (a failed one, most of all) would then exist
  // nowhere in the UI at all.
  const runs = useMemo(
    () =>
      [...instances].sort(
        (a, b) => toMs(b.last_run_at ?? b.created_at) - toMs(a.last_run_at ?? a.created_at),
      ),
    [instances],
  );
  const [showAllRuns, setShowAllRuns] = useState(false);
  // Anything still live is exempt from the cap: those are the only rows with a
  // Stop button, so hiding one would hide the action.
  const shownRuns = useMemo(() => {
    if (showAllRuns || runs.length <= RUNS_COLLAPSED_LIMIT) return runs;
    const keep = new Set(runs.filter((i) => live(i.status)).map((i) => i.instance_id));
    for (const i of runs) {
      if (keep.size >= RUNS_COLLAPSED_LIMIT) break;
      keep.add(i.instance_id);
    }
    return runs.filter((i) => keep.has(i.instance_id));
  }, [runs, showAllRuns]);
  const hiddenRuns = runs.length - shownRuns.length;

  const categoryButton = (
    value: string,
    label: string,
    count: number,
  ) => {
    const on = category === value;
    return (
      <button
        key={value}
        type="button"
        onClick={() => onScopeChange(value)}
        aria-pressed={on}
        data-scope={value}
        title={`Show ${label.toLowerCase()}`}
        className={`flex min-w-0 items-center justify-center gap-1.5 rounded-md px-1.5 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)]/40 ${
          on
            ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
            : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
        }`}
      >
        {label}
        <span className="text-[11px] opacity-70">{count}</span>
      </button>
    );
  };

  return (
    <div
      role="region"
      aria-label="Routine library"
      className={`${page ? "hidden md:flex @max-[639px]/routine-browser:hidden" : "flex"} min-h-0 shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] ${
        compact
          ? "w-full max-h-32 md:w-sidebar-rail md:max-h-none"
          : page
            ? "w-full md:w-sidebar"
            : "w-full max-h-[35%] md:w-64 md:max-h-none"
      }`}
    >
      {/* Header */}
      <div className={`flex shrink-0 items-center border-b border-[var(--color-border)] py-3 ${compact ? "flex-col gap-2 px-1" : "justify-between gap-2 px-4"}`}>
        {!compact && (
          <span className="mr-auto text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
            Routines
          </span>
        )}
        {/* The rail leads with the way back out; the full header ends with it. */}
        {!compact && headerAction}
        <IconButton
          label={compact ? "Expand the library" : "Collapse to the rail"}
          size="sm"
          className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)]/40"
          onClick={onToggleCompact}
        >
          {compact ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-3.5 w-3.5" />}
        </IconButton>
        {compact && headerAction}
      </div>

      {!compact && (
        <>
          {/* Search */}
          <div className="relative shrink-0 px-3 py-3">
            <Search className="absolute left-5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-muted)]" />
            <input
              type="text"
              value={search}
              onChange={(e) => onSearchChange(e.target.value)}
              aria-label="Search routines"
              placeholder="Search routines…"
              className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] py-2 pl-8 pr-8 text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/70 focus:border-[var(--color-primary)] focus:outline-none"
            />
            {search && (
              <button
                type="button"
                onClick={() => onSearchChange("")}
                className="absolute right-5 top-1/2 -translate-y-1/2 rounded p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                title="Clear search"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>

          {/* The agent choice refines Agents without adding a second scope. */}
          <div className="shrink-0 space-y-2 border-b border-[var(--color-border)] px-3 pb-3">
            <div className="grid grid-cols-3 gap-1">
              {categoryButton("all", "All", routines.length)}
              {categoryButton("condor", "Condor", condorCount)}
              {categoryButton("agent", "Agents", agentCount)}
            </div>
            {category === "agent" && (
              <SelectField
                size="compact"
                label="Routine agent"
                value={scope}
                onChange={onScopeChange}
                // "condor" is the Condor scope above, not an agent to pick here.
                options={[
                  { value: "agent", label: "All agents" },
                  ...agents.filter((slug) => slug !== "condor").map((slug) => ({ value: slug, label: formatSlug(slug) })),
                ]}
              />
            )}
          </div>
        </>
      )}

      {/* The routines themselves */}
      <div
        ref={listRef}
        className={`min-h-0 flex-1 overflow-y-auto scrollbar-thin ${compact ? "" : "space-y-1 p-2"}`}
      >
        {listed.length === 0 && !compact && (
          <p className="px-2 py-6 text-center text-xs text-[var(--color-text-muted)]">
            {routines.length === 0 ? "No routines available" : "No matches"}
          </p>
        )}
        {listed.map((r) => {
          const isActive = r.name === activeSource;
          const hasActiveInstance = instances.some(
            (i) => i.routine_name === r.name && live(i.status),
          );
          const owner = routineAgent(r);
          const displayName = formatRoutineName(r.name);
          const accessibleName = owner ? `${displayName} · ${formatSlug(owner)}` : displayName;
          const info = reportInfo.get(r.name);
          const reportCount = info?.count ?? r.report_count;

          if (compact) {
            return (
              <button
                key={r.name}
                type="button"
                onClick={() => onSelect(r.name)}
                aria-label={accessibleName}
                aria-current={isActive ? "true" : undefined}
                {...(isActive ? { "data-active-source": true } : {})}
                className={`flex w-full items-center justify-center py-3 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-primary)]/40 ${
                  isActive
                    ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                    : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                }`}
                title={accessibleName}
              >
                {owner ? (
                  <Brain className="h-4 w-4 text-purple-400" />
                ) : hasActiveInstance ? (
                  <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 shadow-[0_0_6px_theme(colors.emerald.400)]" />
                ) : (
                  <span className="text-[10px] font-bold uppercase leading-none">
                    {displayName.slice(0, 2)}
                  </span>
                )}
              </button>
            );
          }

          return (
            <button
              key={r.name}
              type="button"
              onClick={() => onSelect(r.name)}
              aria-label={accessibleName}
              aria-current={isActive ? "true" : undefined}
              {...(isActive ? { "data-active-source": true } : {})}
              className={`block w-full rounded-lg border px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)]/40 ${
                isActive
                  ? "border-[var(--color-primary)]/40 bg-[var(--color-primary)]/5"
                  : "border-transparent hover:border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 items-center gap-1.5">
                  {hasActiveInstance && (
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_4px_theme(colors.emerald.400)]" />
                  )}
                  <span
                    className={`truncate text-[13px] font-semibold ${
                      isActive ? "text-[var(--color-text)]" : "text-[var(--color-text)]/90"
                    }`}
                  >
                    {displayName}
                  </span>
                </div>
                {reportCount > 0 && (
                  <span title={`${reportCount} reports`} className="shrink-0 rounded-full bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--color-text-muted)]">
                    {reportCount}
                  </span>
                )}
              </div>
              {owner && (
                <span className="mt-1 flex items-center gap-1 text-[11px] text-purple-400">
                  <Brain className="h-3 w-3 shrink-0" />
                  <span className="truncate">{formatSlug(owner)}</span>
                </span>
              )}
              <p className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--color-text-muted)]">
                {info ? info.title : r.description}
              </p>
              {info && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] text-[var(--color-text-muted)]/70">
                    {formatRelativeTime(info.created_at)}
                  </span>
                  {info.tags.slice(0, 2).map((tag) => (
                    <span
                      key={tag}
                      className="max-w-full truncate rounded bg-[var(--color-surface-hover)] px-1 py-0.5 text-[10px] text-[var(--color-text-muted)]/70"
                    >
                      #{tag}
                    </span>
                  ))}
                </div>
              )}
            </button>
          );
        })}
      </div>

      {/* What has been run, live or finished — the launcher page's strip, which
          was the only place a run that wrote no report could be found. */}
      {!compact && runs.length > 0 && (
        <div className="max-h-52 shrink-0 overflow-y-auto border-t border-[var(--color-border)] scrollbar-thin">
          <h3 className="sticky top-0 bg-[var(--color-surface)] px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
            Runs <span className="font-medium opacity-60">{runs.length}</span>
          </h3>
          <div className="px-2 pb-2">
            {shownRuns.map((inst) => (
              <div key={inst.instance_id} className="group flex items-center gap-1.5 rounded px-1 py-1 hover:bg-[var(--color-surface-hover)]">
                <button
                  onClick={() => onSelect(inst.routine_name)}
                  className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  title={`Open ${inst.routine_name.replace(/_/g, " ")}`}
                >
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                      inst.error || inst.status === "failed"
                        ? "bg-[var(--color-red)]"
                        : inst.status === "running"
                          ? "animate-pulse bg-emerald-400"
                          : inst.status === "scheduled"
                            ? "bg-amber-400"
                            : "bg-sky-400"
                    }`}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-[var(--color-text)]">
                      {inst.routine_name.replace(/_/g, " ")}
                    </span>
                    <span className="flex items-center gap-1.5 text-[11px] text-[var(--color-text-muted)]">
                      <span className="capitalize">{inst.status}</span>
                      {!live(inst.status) && inst.last_run_at && (
                        <span>{formatRelativeTime(inst.last_run_at, "")}</span>
                      )}
                      {inst.schedule?.type === "interval" && (
                        <span className="flex items-center gap-0.5">
                          <Clock className="h-2 w-2" />
                          {formatInterval(inst.schedule.interval_sec as number)}
                        </span>
                      )}
                    </span>
                  </span>
                </button>
                {/* Only something still going can be stopped. A finished run
                    keeps its row — that is the point — but not the button. */}
                {live(inst.status) && (
                  <IconButton
                    label="Stop"
                    size="sm"
                    tone="danger"
                    className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                    onClick={() => onStopInstance(inst.instance_id)}
                    disabled={stopping}
                  >
                    <Square className="h-2.5 w-2.5" />
                  </IconButton>
                )}
              </div>
            ))}
            {hiddenRuns > 0 && (
              <button
                onClick={() => setShowAllRuns(true)}
                className="mt-1 w-full rounded border border-dashed border-[var(--color-border)] py-1 text-[11px] font-medium text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                +{hiddenRuns} more
              </button>
            )}
            {showAllRuns && runs.length > RUNS_COLLAPSED_LIMIT && (
              <button
                onClick={() => setShowAllRuns(false)}
                className="mt-1 w-full rounded border border-dashed border-[var(--color-border)] py-1 text-[11px] font-medium text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                Show less
              </button>
            )}
          </div>
        </div>
      )}

      {/* Navigation hint */}
      {!compact && (
        <div className="shrink-0 border-t border-[var(--color-border)] px-3 py-2 text-[10px] text-[var(--color-text-muted)]/60">
          <span className="flex items-center gap-1.5">
            <span className="flex items-center gap-0.5">
              <Kbd><ChevronUp className="h-2.5 w-2.5" /></Kbd>
              <Kbd><ChevronDown className="h-2.5 w-2.5" /></Kbd>
              <span className="ml-0.5">routine</span>
            </span>
            <span className="flex items-center gap-0.5">
              <Kbd><ChevronLeft className="h-2.5 w-2.5" /></Kbd>
              <Kbd><ChevronRight className="h-2.5 w-2.5" /></Kbd>
              <span className="ml-0.5">report</span>
            </span>
            <Kbd>esc</Kbd>
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * How many run rows the strip shows before it folds — the launcher page's cap,
 * kept because a busy day is dozens of runs pushing the routines off screen.
 */
const RUNS_COLLAPSED_LIMIT = 6;

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex h-4 min-w-[16px] items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-0.5 text-[8px] font-medium">
      {children}
    </kbd>
  );
}
