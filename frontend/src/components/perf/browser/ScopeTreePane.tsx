import { ChevronDown, ChevronUp, Layers } from "lucide-react";
import type { JSX, RefObject } from "react";

import { ScopeTree } from "@/components/perf/ScopeTree";
import type { FleetOwner } from "@/lib/agent-attribution";
import type { ConvertQuote, PerfNode } from "@/lib/perf-tree";

import { plural } from "./text";
import type { ExecutorPaging } from "@/hooks/useFleetData";

interface ScopeTreePaneProps {
  sidebarRef: RefObject<HTMLDivElement | null>;
  isCompact: boolean;
  /** The id `Select all` selects — the fleet, or the host's root (FEAT-108). */
  rootScope: string;
  atRoot: boolean;
  scopeCounts: { bots: number; controllers: number; executors: number };
  setScope: (next: string) => void;
  rootNode: PerfNode;
  effectiveScopeId: string;
  openRows: Set<string>;
  toggleOpen: (id: string) => void;
  soloBot: string | undefined;
  groupByBot: boolean;
  owners: FleetOwner[];
  cv: ConvertQuote;
  currencySymbol: string;
  now: number;
  renderBotAction: (node: PerfNode) => JSX.Element | null;
  paging: ExecutorPaging | undefined;
}

/**
 * The scope list: controller → executor, and nothing else.
 *
 * The fleet is the button above it rather than the first row of it. An "All
 * controllers" card at the top of the list was a total dressed as one of the
 * things it totalled: always first, always the biggest number on the column,
 * and always in the way of the rows the reader opened the sidebar to compare.
 * As a button it costs a line instead of a card, says how much it is about to
 * select, and lights up when it is what the panes are reporting.
 */
export function ScopeTreePane({
  sidebarRef,
  isCompact,
  rootScope,
  atRoot,
  scopeCounts,
  setScope,
  rootNode,
  effectiveScopeId,
  openRows,
  toggleOpen,
  soloBot,
  groupByBot,
  owners,
  cv,
  currencySymbol,
  now,
  renderBotAction,
  paging,
}: ScopeTreePaneProps) {
  return (
    <>
      <div ref={sidebarRef} data-perf-scope-tree className="flex-1 overflow-y-auto scrollbar-thin narrow-pane:overflow-visible">
        <div className="sticky top-0 z-10 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
          {isCompact ? (
            <button
              onClick={() => setScope(rootScope)}
              {...(atRoot ? { "data-active-scope": true } : {})}
              aria-pressed={atRoot}
              title={`Select all — ${plural(scopeCounts.controllers, "controller")}`}
              className={`flex w-full items-center justify-center py-3 transition-colors ${
                atRoot
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              <Layers className="h-3.5 w-3.5" />
            </button>
          ) : (
            <div className="flex items-center gap-1.5 px-2 py-1.5">
              <span className="truncate text-[10px] tabular-nums text-[var(--color-text-muted)]">
                {scopeCounts.bots > 0 && `${plural(scopeCounts.bots, "bot")} · `}
                {plural(scopeCounts.controllers, "controller")}
                {scopeCounts.executors > 0 && ` · ${plural(scopeCounts.executors, "executor")}`}
              </span>
              <button
                type="button"
                onClick={() => setScope(rootScope)}
                aria-pressed={atRoot}
                {...(atRoot ? { "data-active-scope": true } : {})}
                title="Report on everything in scope, combined"
                className={`ml-auto flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] transition-colors ${
                  atRoot
                    ? "border-[var(--color-primary)] bg-[var(--color-primary)]/15 font-semibold text-[var(--color-primary)]"
                    : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-primary)]/50 hover:text-[var(--color-text)]"
                }`}
              >
                <Layers className="h-3 w-3 shrink-0" />
                Select all
              </button>
            </div>
          )}
        </div>
        <ScopeTree
          root={rootNode}
          activeId={effectiveScopeId}
          open={openRows}
          showBot={!soloBot && !groupByBot}
          owners={owners}
          onSelect={setScope}
          onToggleOpen={toggleOpen}
          cv={cv}
          currencySymbol={currencySymbol}
          now={now}
          compact={isCompact}
          renderAction={isCompact ? undefined : renderBotAction}
        />
      </div>

      {/* How much of the executor history the tree was built from.
          A tree missing branches looks exactly like a fleet that never had
          them, so a capped walk has to say so here — the cap now bounds the
          *sidebar*, not just a table's row count (FEAT-086). */}
      {!isCompact && paging && paging.loaded > 0 && (
        <div className="shrink-0 border-t border-[var(--color-border)] px-3 py-1.5 text-[10px] text-[var(--color-text-muted)]">
          <div className="flex items-center gap-1.5">
            <span className="tabular-nums">{paging.loaded.toLocaleString()} executors loaded</span>
            {paging.loading && <span>· loading…</span>}
            {paging.done && <span>· all</span>}
            {paging.capped && (
              <button
                onClick={paging.loadMore}
                className="ml-auto rounded border border-[var(--color-border)] px-1.5 py-0.5 font-medium hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                Load more
              </button>
            )}
          </div>
          {paging.capped && (
            <p className="mt-0.5 text-[var(--color-yellow)]">
              Cap reached — this tree shows part of the history, not all of it.
            </p>
          )}
        </div>
      )}

      {/* Nav hints */}
      {!isCompact && (
        <div className="narrow-pane:hidden border-t border-[var(--color-border)] px-3 py-2 text-[10px] text-[var(--color-text-muted)]/60">
          <span className="flex items-center gap-1.5">
            <span className="flex items-center gap-0.5">
              <kbd className="inline-flex h-4 min-w-[16px] items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-0.5 text-[8px] font-medium">
                <ChevronUp className="h-2.5 w-2.5" />
              </kbd>
              <kbd className="inline-flex h-4 min-w-[16px] items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-0.5 text-[8px] font-medium">
                <ChevronDown className="h-2.5 w-2.5" />
              </kbd>
              <span className="ml-0.5">navigate</span>
            </span>
          </span>
        </div>
      )}
    </>
  );
}
