import { Brain, ChevronDown, ChevronUp, Search, Zap } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";

import { SelectField } from "@/components/ui/select-field";
import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import type { RoutineInfo, RoutineInstance } from "@/lib/api";
import {
  formatRoutineName,
  formatScope,
  inScope,
  resolveRoutine,
  routineAgent,
  routineAgents,
  type RoutineScope,
} from "@/lib/routineUtils";

/**
 * Which routine the library is reading, and whose routines it lists.
 *
 * Two controls, one question: the scope narrows the list — the general Condor
 * library, or one agent's own — and the second picks a routine out of it. They
 * live together because a filter kept away from the list it filters is a filter
 * nobody uses, which is what the old arrangement proved twice: a scope select
 * stranded in the report's header, over a list that was a 48px rail.
 *
 * Both live in the library sheet's own nav bar, over the report they name.
 * The scope used to be asked in the dock instead, one column away, which is a
 * filter that goes off screen whenever that column or its Routines section is
 * folded — and `parts` is what is left of that split, for a surface that wants
 * only one of the two. The report browser's own header is that surface: it
 * names the routine itself and asks only the scope here, rather than keeping a
 * second select with a second option list to drift from this one.
 *
 * ↑/↓ step through the list, from the closed trigger's keyboard or from the
 * `arrows` buttons beside it; the routine changes under you, as it did in the
 * sidebar this replaced.
 */
export function RoutinePicker({
  routines,
  instances = [],
  scope,
  onScopeChange,
  source,
  onSelect,
  variant = "dock",
  parts = "both",
  arrows = false,
  scopeInMenu = false,
}: {
  routines: RoutineInfo[];
  /** Live runs, for the dot that says one of these is running right now. */
  instances?: RoutineInstance[];
  scope: RoutineScope;
  onScopeChange: (next: RoutineScope) => void;
  /** The routine on screen, in either spelling (see `resolveRoutine`). */
  source?: string;
  onSelect: (name: string) => void;
  /** `"dock"` stacks the controls in the column; `"inline"` is one row. */
  variant?: "dock" | "inline";
  /** Which controls to render — the two live on different surfaces. */
  parts?: "both" | "scope" | "routine";
  /** Step buttons beside the trigger, for the reader who is not on a keyboard. */
  arrows?: boolean;
  /** Offer the scope inside the menu, for layouts with room for one control. */
  scopeInMenu?: boolean;
}) {
  const [open, setOpen] = useState(false);
  // State, not a ref: the portalled panel only gets coordinates once a render
  // has handed it the resolved trigger element.
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [query, setQuery] = useState("");
  const panelRef = useRef<HTMLDivElement>(null);
  const focusPanel = useCallback((node: HTMLDivElement | null) => {
    panelRef.current = node;
    node?.focus({ preventScroll: true });
  }, []);

  const agents = useMemo(() => routineAgents(routines), [routines]);
  // "condor" is the Condor scope, not an agent, even when an agent claims it.
  const agentScopes = agents.filter((a) => a !== "condor");
  const agentPicked = scope === "agent" || agentScopes.includes(scope);
  const listed = useMemo(() => inScope(routines, scope), [routines, scope]);
  const active = useMemo(
    () => resolveRoutine(routines, source),
    [routines, source],
  );
  const activeIdx = listed.findIndex((r) => r.name === active?.name);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return listed;
    return listed.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        formatRoutineName(r.name).toLowerCase().includes(q) ||
        r.description.toLowerCase().includes(q),
    );
  }, [listed, query]);

  const running = useMemo(
    () =>
      new Set(
        instances
          .filter((i) => i.status === "running" || i.status === "scheduled")
          .map((i) => i.routine_name),
      ),
    [instances],
  );

  /** ↑/↓ on the closed trigger: the next routine, read straight away. */
  const step = (delta: number) => {
    const next = listed[activeIdx + delta];
    if (next) onSelect(next.name);
  };

  const pick = (name: string) => {
    setOpen(false);
    setQuery("");
    onSelect(name);
  };

  const onTriggerKey = (e: React.KeyboardEvent) => {
    if (open) return;
    if (e.key === "ArrowDown") {
      step(1);
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      step(-1);
      e.preventDefault();
    }
  };

  /** Roving focus down the options, so the list is walkable once it is open. */
  const onPanelKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const rows = Array.from(
      panelRef.current?.querySelectorAll<HTMLButtonElement>(
        "[data-routine-row]",
      ) ?? [],
    );
    if (rows.length === 0) return;
    const at = rows.indexOf(document.activeElement as HTMLButtonElement);
    const next = e.key === "ArrowDown" ? at + 1 : at - 1;
    rows[Math.max(0, Math.min(rows.length - 1, next))]?.focus();
    e.preventDefault();
  };

  const owner = active ? routineAgent(active) : null;
  const dock = variant === "dock";

  const trigger = (
    <button
      type="button"
      ref={setAnchor}
      onClick={() => {
        setQuery("");
        setOpen((v) => !v);
      }}
      onKeyDown={onTriggerKey}
      aria-label="Routine"
      aria-haspopup="listbox"
      aria-expanded={open}
      title={
        active ? `${active.name} — ↑/↓ for the next routine` : "Pick a routine"
      }
      className={`flex min-h-7 items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-left text-xs transition-colors hover:border-[var(--color-primary)]/40 hover:bg-[var(--color-surface-hover)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]/40 pointer-coarse:min-h-11 ${
        dock ? "w-full" : "min-w-0 w-full flex-1"
      }`}
    >
      {owner ? (
        <Brain className="h-3.5 w-3.5 shrink-0 text-purple-400" />
      ) : (
        <Zap className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
      )}
      <span className="min-w-0 flex-1 truncate font-medium text-[var(--color-text)]">
        {active ? formatRoutineName(active.name) : "Pick a routine"}
      </span>
      {active && active.report_count > 0 && (
        <span className="shrink-0 text-[11px] text-[var(--color-text-muted)]/70">
          {active.report_count}
        </span>
      )}
      <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
    </button>
  );

  const scopeSelect = (
    <SelectField label="Routine scope" size="compact" value={scope} onChange={onScopeChange}
      options={[
        { value: "all", label: formatScope("all") },
        { value: "condor", label: formatScope("condor") },
        ...(agents.length ? [{ value: "agent", label: formatScope("agent") }] : []),
        ...agents.map((slug) => ({ value: slug, label: formatScope(slug) })),
      ]} />
  );

  /** One step button of the ↑/↓ pair, dead at the end of the list. */
  const arrow = (delta: number) => {
    const target = listed[activeIdx + delta];
    const up = delta < 0;
    return (
      <button
        type="button"
        onClick={() => step(delta)}
        disabled={!target}
        aria-label={up ? "Previous routine" : "Next routine"}
        title={
          target
            ? `${up ? "Previous" : "Next"}: ${formatRoutineName(target.name)}`
            : `No ${up ? "previous" : "next"} routine`
        }
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:cursor-default disabled:opacity-30 disabled:hover:bg-transparent pointer-coarse:h-11 pointer-coarse:w-11"
      >
        {up ? (
          <ChevronUp className="h-3.5 w-3.5" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5" />
        )}
      </button>
    );
  };

  return (
    <div
      data-routine-picker
      className={
        dock
          ? "flex flex-col gap-1 px-3 pb-2 pt-1"
          : "flex w-full min-w-0 items-center gap-1.5 [&>div:first-child]:min-w-0 [&>div:first-child]:flex-1 @max-[639px]/workspace-sheet:grid @max-[639px]/workspace-sheet:grid-cols-[minmax(0,1fr)_auto] @max-[639px]/workspace-sheet:gap-2 @max-[639px]/workspace-sheet:[&>div:first-child]:col-span-full"
      }
    >
      {parts !== "routine" && scopeSelect}
      {parts !== "scope" && trigger}
      {parts !== "scope" && arrows && (
        <span className="flex shrink-0 items-center">
          {arrow(-1)}
          {arrow(1)}
        </span>
      )}
      <AnchoredMenu
        anchor={anchor}
        open={open && parts !== "scope"}
        onClose={() => setOpen(false)}
        matchAnchorWidth="exact"
        minWidth={400}
        horizontalBoundary={anchor?.closest<HTMLElement>("[data-workspace-sheet]")}
        maxHeight={360}
        role="listbox"
      >
        <div ref={focusPanel} tabIndex={-1} onKeyDown={onPanelKey} className="focus:outline-none">
          {/* Search and scope stay pinned while the list scrolls under them. */}
          <div className="sticky top-0 z-10 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
            <div className="flex min-h-8 items-center gap-1.5 px-2 py-1.5 pointer-coarse:min-h-11">
              <Search className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={`Search ${formatScope(scope).toLowerCase()}…`}
                className="min-w-0 flex-1 bg-transparent text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/60 focus:outline-none"
              />
            </div>
            {scopeInMenu && (
              // Three kinds of owner as one segmented control; the agents
              // themselves only appear once "Agents" is picked.
              <div role="group" aria-label="Routine scope" className="space-y-1.5 px-2 pb-2">
                <div className="grid auto-cols-fr grid-flow-col gap-0.5 rounded-md bg-[var(--color-surface-hover)] p-0.5">
                  {(["all", "condor", ...(agentScopes.length ? ["agent"] : [])] as RoutineScope[]).map((value) => {
                    const pressed = value === "agent" ? agentPicked : scope === value;
                    return (
                      <button
                        key={value}
                        type="button"
                        aria-pressed={pressed}
                        onClick={() => onScopeChange(value)}
                        className={`h-6 rounded px-2 text-[11px] font-medium transition-colors focus-visible:outline-2 focus-visible:outline-[var(--color-primary)] ${
                          pressed
                            ? "bg-[var(--color-surface)] text-[var(--color-text)] shadow-sm"
                            : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                        }`}
                      >
                        {value === "agent" ? "Agents" : formatScope(value)}
                      </button>
                    );
                  })}
                </div>
                {agentPicked && (
                  <div className="flex flex-wrap gap-1">
                    {(["agent", ...agentScopes] as RoutineScope[]).map((value) => (
                      <button
                        key={value}
                        type="button"
                        aria-pressed={scope === value}
                        onClick={() => onScopeChange(value)}
                        className={`flex h-6 items-center gap-1 rounded-md border px-2 text-[11px] font-medium transition-colors focus-visible:outline-2 focus-visible:outline-[var(--color-primary)] ${
                          scope === value
                            ? "border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                            : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                        }`}
                      >
                        {value !== "agent" && <Brain className="h-3 w-3 shrink-0 text-purple-400" />}
                        {formatScope(value)}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
          {matches.length === 0 ? (
            <p className="px-3 py-3 text-xs text-[var(--color-text-muted)]">
              No routine matches.
            </p>
          ) : (
            matches.map((r) => {
              const isActive = r.name === active?.name;
              const rowOwner = routineAgent(r);
              return (
                <button
                  key={r.name}
                  type="button"
                  data-routine-row
                  role="option"
                  aria-selected={isActive}
                  onClick={() => pick(r.name)}
                  className={`block min-h-8 w-full border-l-2 px-2.5 py-1.5 text-left transition-colors focus:outline-none pointer-coarse:min-h-11 ${
                    isActive
                      ? "border-l-[var(--color-primary)] bg-[var(--color-primary)]/5"
                      : "border-l-transparent hover:bg-[var(--color-surface-hover)] focus:bg-[var(--color-surface-hover)]"
                  }`}
                >
                  <span className="flex items-center gap-1.5">
                    {rowOwner ? (
                      <Brain className="h-3.5 w-3.5 shrink-0 text-purple-400" />
                    ) : (
                      <Zap className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]/60" />
                    )}
                    <span
                      className={`min-w-0 flex-1 truncate text-xs ${
                        isActive
                          ? "font-semibold text-[var(--color-text)]"
                          : "text-[var(--color-text)]"
                      }`}
                    >
                      {formatRoutineName(r.name)}
                    </span>
                    {running.has(r.name) && (
                      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_4px_theme(colors.emerald.400)]" />
                    )}
                    {r.report_count > 0 && (
                      <span className="shrink-0 text-[11px] text-[var(--color-text-muted)]/60">
                        {r.report_count}
                      </span>
                    )}
                  </span>
                  <span className="block truncate pl-5 text-[11px] text-[var(--color-text-muted)]/70">
                    {/* Only a mixed list has to say whose routine this is. */}
                    {rowOwner && scope === "all" ? `${rowOwner} · ` : ""}
                    {r.description}
                  </span>
                </button>
              );
            })
          )}
        </div>
      </AnchoredMenu>
    </div>
  );
}
