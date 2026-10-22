import { Layers, Server } from "lucide-react";

import { AgentScopeHeader } from "@/components/perf/AgentScopeHeader";
import { StatusDot } from "@/components/perf/ScopeTree";
import type { FleetOwner } from "@/lib/agent-attribution";
import type { BotRunInfo, ControllerInfo } from "@/lib/api/bots";
import type { ExecutorInfo } from "@/lib/api/executors";
import { shortBotName } from "@/lib/formatters";
import { parseSide } from "@/lib/perf-positions";
import { runStatus, type PerfLeaf, type PerfNode, type Population } from "@/lib/perf-tree";

import { plural } from "./text";

/**
 * Says that some of what is on screen was never converted.
 *
 * `foldLeaves` converts a leaf through its `pair`, so a leaf with no pair is
 * added up **as though it were dollars** — for a BRL fleet, overstating it by
 * the whole BRL/USD rate. The pair is not always recoverable: a
 * controller-performance row carries no top-level pair, only the ones inside
 * its open positions, so a controller that stopped flat has nothing left to
 * say what it traded.
 *
 * The answer is the one `ArchivedBotDetail`'s `ConversionNote` already gives:
 * fold at face value and disclose it. Never guess a quote from a bot's name,
 * and never pass the total off as dollars in silence. Drawn wherever a scope's
 * numbers are, which is why it is a component rather than a line in one header.
 */
function UnpricedNote({ leaves }: { leaves: PerfLeaf[] }) {
  if (leaves.length === 0) return null;
  return (
    <span
      className="block truncate text-[10px] text-amber-500/90"
      title={`These stopped holding no position, so the record no longer says which pair they traded: ${leaves
        .map((leaf) => leaf.label)
        .join(", ")}`}
    >
      {plural(leaves.length, "record")} named no quote currency — counted at face value, not
      converted
    </span>
  );
}

/** The name a scope's records go by in counts: "3 executors", "2 finished controllers". */
export type ScopeNoun = "controller" | "executor" | "finished controller" | "finished record" | "closed executor";

interface ReportTitleProps {
  scope: PerfNode;
  population: Population;
  activeCtrl: ControllerInfo | undefined;
  activeRun: BotRunInfo | undefined;
  activeExec: ExecutorInfo | undefined;
  activeAgent: FleetOwner | undefined;
  /** The controller has been asked to stop / has stopped since the listing. */
  isStopping: boolean;
  isKilled: boolean;
  scopeAgentKey: string | null | undefined;
  scopeBotName: string | null | undefined;
  scopeLegacyBots: string[];
  owners: FleetOwner[];
  scopedLeaves: PerfLeaf[];
  unpricedLeaves: PerfLeaf[];
  scopeNoun: ScopeNoun;
  filtersActive: boolean;
}

/** The left half of the report's top bar: what the scope is, and what it folds. */
export function ReportTitle({
  scope,
  population,
  activeCtrl,
  activeRun,
  activeExec,
  activeAgent,
  isStopping,
  isKilled,
  scopeAgentKey,
  scopeBotName,
  scopeLegacyBots,
  owners,
  scopedLeaves,
  unpricedLeaves,
  scopeNoun,
  filtersActive,
}: ReportTitleProps) {
  return (
    // The badges wrap under the title rather than overlapping what follows;
    // on a narrow pane they share one line and the title gives way instead.
    <div className="flex min-w-0 max-w-full flex-[1_1_18rem] flex-wrap items-center gap-x-3 gap-y-1 narrow-pane:flex-nowrap">
      {activeCtrl ? (
        <>
          <div className="truncate">
            <h2 className="text-sm font-semibold truncate">
              {activeCtrl.controller_name || activeCtrl.controller_id}
            </h2>
            {activeCtrl.controller_name && activeCtrl.controller_id && activeCtrl.controller_id !== activeCtrl.controller_name && (
              <span className="text-[10px] text-[var(--color-text-muted)] font-mono block truncate">
                {activeCtrl.controller_id}
              </span>
            )}
            <UnpricedNote leaves={unpricedLeaves} />
          </div>
          {activeCtrl.connector && (
            // Dropped on a narrow pane so the pair and status share the
            // title's line; the venue is still listed further down.
            <span className="shrink-0 rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs text-[var(--color-text-muted)] border border-[var(--color-border)]/50 narrow-pane:hidden">
              {activeCtrl.connector}
            </span>
          )}
          {activeCtrl.trading_pair && (
            <span className="shrink-0 rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs font-medium border border-[var(--color-border)]/50">
              {activeCtrl.trading_pair}
            </span>
          )}
          <div className="flex items-center gap-1.5 shrink-0">
            <StatusDot status={isStopping ? "stopping" : isKilled ? "stopped" : activeCtrl.status} />
            <span className="text-xs capitalize">{isStopping ? "stopping" : isKilled ? "stopped" : activeCtrl.status}</span>
          </div>
        </>
      ) : activeRun ? (
        // A finished run has no node of its own, so the header a run used
        // to get under the `runs` branch is the header the fleet row gets
        // when the bubbles have narrowed it to that one run: the same
        // name, the same provenance line, the same status. The controller
        // count comes from the tree rather than from the run record,
        // whose `num_controllers` is aggregated off the *live* fleet and
        // is therefore zero for everything in here.
        <>
          <div className="truncate">
            <h2 className="text-sm font-semibold truncate" title={activeRun.bot_name}>
              {shortBotName(activeRun.bot_name)}
            </h2>
            <span className="text-[10px] text-[var(--color-text-muted)] block truncate">
              {[
                activeRun.account_name,
                activeRun.strategy_name,
                plural(scope.children.length, "controller"),
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
            <UnpricedNote leaves={unpricedLeaves} />
          </div>
          <span className="shrink-0 rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs text-[var(--color-text-muted)] border border-[var(--color-border)]/50">
            {activeRun.deployment_status}
          </span>
          <div className="flex items-center gap-1.5 shrink-0">
            <StatusDot status={runStatus(activeRun)} />
            <span className="text-xs capitalize">{runStatus(activeRun) || "—"}</span>
          </div>
        </>
      ) : activeExec ? (
        <>
          <div className="truncate">
            <h2 className="text-sm font-semibold truncate font-mono" title={activeExec.id}>
              {activeExec.id.slice(0, 14)}…
            </h2>
            <span className="text-[10px] text-[var(--color-text-muted)] block truncate">
              {activeExec.type}
              {activeExec.controller_id ? ` · ${activeExec.controller_id}` : ""}
            </span>
          </div>
          {activeExec.connector && (
            // Dropped on a narrow pane so the pair and status share the
            // title's line; the venue is still listed further down.
            <span className="shrink-0 rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs text-[var(--color-text-muted)] border border-[var(--color-border)]/50 narrow-pane:hidden">
              {activeExec.connector}
            </span>
          )}
          {activeExec.trading_pair && (
            <span className="shrink-0 rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs font-medium border border-[var(--color-border)]/50">
              {activeExec.trading_pair}
            </span>
          )}
          <div className="flex items-center gap-1.5 shrink-0">
            <StatusDot status={activeExec.status} />
            <span className="text-xs capitalize">
              {activeExec.close_type ? parseSide(activeExec.close_type) : activeExec.status}
            </span>
          </div>
        </>
      ) : scopeAgentKey ? (
        // An agent scope: the same report every other scope gets, plus
        // the one thing only this scope can say — whether the loop that
        // produced these numbers is still alive, and what it last said.
        <AgentScopeHeader
          runKey={scopeAgentKey}
          owners={owners}
          owner={activeAgent}
          legacyBots={scopeLegacyBots}
          botName={scopeBotName ?? undefined}
        >
          <span className="block truncate text-[10px] text-[var(--color-text-muted)]">
            {plural(scopedLeaves.length, scopeNoun)} aggregated
            {filtersActive && " · filtered"}
          </span>
          <UnpricedNote leaves={unpricedLeaves} />
        </AgentScopeHeader>
      ) : (
        // Every other scope — the whole fleet, one live bot's share of
        // it, or a controller reconstructed out of closed executors. One
        // header for all of them: what it is, and what it folds.
        <div className="truncate">
          <h2 className="text-sm font-semibold truncate flex items-center gap-2">
            {scopeBotName ? (
              <Server className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
            ) : (
              <Layers className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
            )}
            <span className="truncate" title={scopeBotName ?? scope.label}>
              {scopeBotName
                ? shortBotName(scopeBotName)
                : scope.kind !== "fleet"
                  ? scope.label
                  : population === "running"
                    ? "All controllers combined"
                    : "Everything that has finished"}
            </span>
          </h2>
          <span className="text-[10px] text-[var(--color-text-muted)] block truncate">
            {plural(scopedLeaves.length, scopeNoun)} aggregated
            {filtersActive && " · filtered"}
          </span>
          <UnpricedNote leaves={unpricedLeaves} />
        </div>
      )}
    </div>
  );
}
