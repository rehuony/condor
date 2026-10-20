import {
  ChevronDown,
  ChevronUp,
  Database,
  Rocket,
  ScrollText,
  SlidersHorizontal,
  Square,
  TerminalSquare,
  Trash2,
  X,
} from "lucide-react";
import type { Dispatch, SetStateAction } from "react";

import { runChipLabel } from "@/components/perf/agentFilter";
import { ControllerToggle } from "@/components/perf/ControllerToggle";
import type { ExecutorDelete, ExecutorStop } from "@/components/perf/executorActions";
import { Spinner } from "@/components/ui/Spinner";
import type { BotRunInfo, BotSummary, ControllerInfo } from "@/lib/api/bots";
import type { ExecutorInfo } from "@/lib/api/executors";
import { isExecutorActive } from "@/lib/formatters";
import type { PerfNode } from "@/lib/perf-tree";

/**
 * A destructive action that asks once before it runs: Stop for a bot, Delete
 * for a finished run. `arm` names the subject the confirm step is for — a bare
 * boolean stayed armed while the sidebar moved on, so the Confirm the user was
 * looking at belonged to one bot and would have acted on another.
 */
export interface ArmedAction<T> {
  /** The confirm step is showing for the scope on screen. */
  armed: boolean;
  /** The request is in flight for the scope on screen. */
  pending: boolean;
  /** The last request failed, and it was for the scope on screen. */
  failed: boolean;
  error: unknown;
  arm: (subject: string | null) => void;
  confirm: (subject: T) => void;
}

/** The run this scope is narrowed to, and the way back out of it. */
export interface RunFilter {
  num: number | null;
  /** The scope is an agent's, so the run actually narrows something. */
  owned: boolean;
  /** Why the run's ledger could not be read, or empty. */
  unavailable: string;
  clear: () => void;
}

/** Keyboard-order navigation over the picker's visible rows. */
export interface ScopeNav {
  index: number;
  total: number;
  up: () => void;
  down: () => void;
}

export type DrawerKey = "config" | "logs" | null;

interface ReportActionsProps {
  scope: PerfNode;
  atRoot: boolean;
  server: string;
  activeBot: BotSummary | undefined;
  activeCtrl: ControllerInfo | undefined;
  activeExec: ExecutorInfo | undefined;
  activeRun: BotRunInfo | undefined;
  /** The controller is on the live fleet, so it can be toggled and configured. */
  liveCtrl: ControllerInfo | undefined;
  isKilled: boolean;
  isStopping: boolean;
  /** The bot itself reports it is stopping. */
  botStopping: boolean;
  botStop: ArmedAction<string>;
  runDelete: ArmedAction<number>;
  runFilter: RunFilter;
  nav: ScopeNav;
  openDrawer: DrawerKey;
  setDrawer: Dispatch<SetStateAction<DrawerKey>>;
  execChart: "price" | "pnl";
  setExecChart: (mode: "price" | "pnl") => void;
  execHasPrice: boolean;
  stop: ExecutorStop;
  deletion: ExecutorDelete;
  /** The finished history under this scope, for Delete History. */
  scopeDelete: { ids: string[]; label: string };
  onDeploy: () => void;
  onOpenArchive: (run: BotRunInfo) => void;
  onOpenEditor: () => void;
}

/**
 * The right half of the report's top bar: every action the scope owns, drawn
 * only there — the fleet deploys bots, a bot is stopped and read, a controller
 * is paused and configured (FEAT-084).
 */
export function ReportActions({
  scope,
  atRoot,
  server,
  activeBot,
  activeCtrl,
  activeExec,
  activeRun,
  liveCtrl,
  isKilled,
  isStopping,
  botStopping,
  botStop,
  runDelete,
  runFilter,
  nav,
  openDrawer,
  setDrawer,
  execChart,
  setExecChart,
  execHasPrice,
  stop,
  deletion,
  scopeDelete,
  onDeploy,
  onOpenArchive,
  onOpenEditor,
}: ReportActionsProps) {
  return (
    <div className="flex items-center gap-1.5">
      {/* The run this scope is narrowed to, and the way back out of it.
          A filter that cannot be seen is a filter that cannot be undone,
          which is the rule the bubbles beside the tree were built on —
          and this one arrived in a link rather than being ticked. */}
      {runFilter.num !== null && (
        <span
          className="flex shrink-0 items-center gap-1 rounded-full border border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 px-2 py-0.5 text-[10px] font-medium text-[var(--color-primary)]"
          title={
            runFilter.owned
              ? `Only the bots, controllers and executors run ${runFilter.num} deployed`
              : `A run narrows an agent scope; this scope is not one, so nothing is filtered`
          }
        >
          {runChipLabel(runFilter.num)}
          <button
            type="button"
            onClick={runFilter.clear}
            aria-label="Clear the run filter"
            className="transition-opacity hover:opacity-70"
          >
            <X className="h-2.5 w-2.5" />
          </button>
        </span>
      )}
      {runFilter.unavailable && (
        <span data-run-unavailable className="shrink-0 text-[10px] text-amber-500/90">
          {runFilter.unavailable}
        </span>
      )}
      <div className="flex items-center border border-[var(--color-border)] rounded overflow-hidden mr-1">
        <button
          onClick={nav.up}
          disabled={nav.index <= 0}
          className="p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-30"
          title="Previous (↑)"
        >
          <ChevronUp className="h-3.5 w-3.5" />
        </button>
        <span className="text-[10px] tabular-nums text-[var(--color-text-muted)] px-1 border-x border-[var(--color-border)]">
          {nav.index + 1}/{nav.total}
        </span>
        <button
          onClick={nav.down}
          disabled={nav.index < 0 || nav.index >= nav.total - 1}
          className="p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-30"
          title="Next (↓)"
        >
          <ChevronDown className="h-3.5 w-3.5" />
        </button>
      </div>
      {/* Every action belongs to a scope, and is drawn only there: the
          fleet deploys bots, a bot is stopped and read, a controller is
          paused and configured. The accordion and the table that used to
          carry the first two are gone (FEAT-084). */}
      {scope.kind === "fleet" && (
        <button
          onClick={onDeploy}
          className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-primary)] transition-colors hover:bg-[var(--color-primary)]/10"
          title="Deploy a new bot"
        >
          <Rocket className="h-3.5 w-3.5" />
          Deploy bot
        </button>
      )}

      {activeBot && (
        <>
          {botStopping ? (
            <span className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-[var(--color-yellow)]">
              <Spinner size="sm" />
              Stopping
            </span>
          ) : botStop.armed ? (
            <span className="flex items-center gap-1.5">
              <button
                onClick={() => botStop.confirm(activeBot.bot_name)}
                disabled={botStop.pending}
                className="rounded bg-[var(--color-red)] px-2 py-1 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {botStop.pending ? "Stopping..." : "Confirm"}
              </button>
              <button
                onClick={() => botStop.arm(null)}
                className="rounded px-2 py-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
              >
                Cancel
              </button>
            </span>
          ) : (
            <button
              onClick={() => botStop.arm(activeBot.bot_name)}
              className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-red)] transition-colors hover:bg-[var(--color-red)]/10"
              title="Stop bot"
            >
              <Square className="h-3.5 w-3.5" />
              Stop bot
            </button>
          )}
          {botStop.failed && (
            <span
              className="text-[10px] whitespace-nowrap text-[var(--color-red)]"
              title={
                botStop.error instanceof Error
                  ? botStop.error.message
                  : "Unknown error"
              }
            >
              Failed to stop
            </span>
          )}
          <button
            onClick={() => setDrawer((d) => (d === "logs" ? null : "logs"))}
            className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors ${
              openDrawer === "logs"
                ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
            }`}
            title={openDrawer === "logs" ? "Hide logs" : "Show logs"}
          >
            <ScrollText className="h-3.5 w-3.5" />
            Logs
            {activeBot.error_count > 0 && (
              <span className="text-[10px] text-[var(--color-yellow)]">
                {activeBot.error_count}
              </span>
            )}
          </button>
        </>
      )}

      {activeRun?.archive_db_path && (
        <button
          onClick={() => onOpenArchive(activeRun)}
          className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-primary)] transition-colors hover:bg-[var(--color-primary)]/10"
          title="Open this run's archived database"
        >
          <Database className="h-3.5 w-3.5" />
          Open archive
        </button>
      )}

      {activeRun?.bot_run_id && activeRun.deployment_status === "ARCHIVED" && (
        runDelete.armed ? (
          <span className="flex items-center gap-1.5">
            <button
              onClick={() => runDelete.confirm(activeRun.bot_run_id!)}
              disabled={runDelete.pending}
              className="rounded bg-[var(--color-red)] px-2 py-1 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {runDelete.pending ? "Deleting…" : "Confirm delete"}
            </button>
            <button
              onClick={() => runDelete.arm(null)}
              className="rounded px-2 py-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
            >
              Cancel
            </button>
          </span>
        ) : (
          <button
            onClick={() => runDelete.arm(activeRun.bot_name)}
            className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-red)] transition-colors hover:bg-[var(--color-red)]/10"
            title="Delete this bot run permanently"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Delete run
          </button>
        )
      )}
      {runDelete.failed && activeRun && (
        <span
          className="text-[10px] whitespace-nowrap text-[var(--color-red)]"
          title={
            runDelete.error instanceof Error
              ? runDelete.error.message
              : "Unknown error"
          }
        >
          Failed to delete
        </span>
      )}

      {/* Which picture this executor is telling its story with. Drawn
          only where there is a choice: no candles, no toggle. */}
      {activeExec && execHasPrice && (
        <div className="flex shrink-0 items-center gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-0.5">
          {(["price", "pnl"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setExecChart(mode)}
              aria-pressed={execChart === mode}
              title={
                mode === "price"
                  ? "Candles, with this executor's entry, exit and levels drawn over them"
                  : "This executor's own PnL over time"
              }
              className={`rounded px-2 py-1 text-[10px] font-medium transition-colors ${
                execChart === mode
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              {mode === "price" ? "Price" : "PnL"}
            </button>
          ))}
        </div>
      )}

      {activeExec && isExecutorActive(activeExec.status) && (
        <button
          onClick={() => stop.request([activeExec.id])}
          disabled={stop.stoppingIds.has(activeExec.id)}
          className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-red)] transition-colors hover:bg-[var(--color-red)]/10 disabled:opacity-50"
          title="Stop this executor"
        >
          <Square className="h-3.5 w-3.5" />
          {stop.stoppingIds.has(activeExec.id) ? "Stopping…" : "Stop"}
        </button>
      )}

      {liveCtrl && activeCtrl && (
        <ControllerToggle
          server={server}
          bot={activeCtrl.bot_name}
          controllerId={activeCtrl.controller_id}
          stopped={isKilled}
          stopping={isStopping}
          label={activeCtrl.controller_id}
          variant="labelled"
        />
      )}
      {/* The config editor: a drawer you open, not a column that is always
          there. It only describes a single controller, so an aggregate
          scope has nothing for it to show. */}
      {liveCtrl && (
        <button
          onClick={() => setDrawer((d) => (d === "config" ? null : "config"))}
          className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors ${
            openDrawer === "config"
              ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
              : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          }`}
          title={openDrawer === "config" ? "Hide config" : "Show config"}
        >
          <SlidersHorizontal className="h-3.5 w-3.5" />
          Config
        </button>
      )}

      {/* Controller sources and their configs, over the whole fleet — so
          it belongs to every scope rather than to one. */}
      <button
        onClick={onOpenEditor}
        className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
        title="Open the controller & config editor"
      >
        <TerminalSquare className="h-3.5 w-3.5" />
        Editor
      </button>

      {/* The selected scope's finished history, from one executor up to a
          whole agent. The fleet root never offers it. */}
      {!atRoot && scopeDelete.ids.length > 0 && (
        <button
          type="button"
          onClick={() => deletion.request(scopeDelete.ids, scopeDelete.label)}
          disabled={deletion.pending}
          aria-label={`Delete history under ${scopeDelete.label}`}
          title={`Delete ${scopeDelete.ids.length} terminated ${scopeDelete.ids.length === 1 ? "record" : "records"} under ${scopeDelete.label}`}
          className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-red)] transition-colors hover:bg-[var(--color-red)]/10 disabled:opacity-50"
        >
          <Trash2 className="h-3.5 w-3.5" />
          {scopeDelete.ids.some((id) => deletion.deletingIds.has(id)) ? "Deleting…" : "Delete History"}
        </button>
      )}
    </div>
  );
}
