import { Rocket } from "lucide-react";

import type { BotSummary } from "@/lib/api/bots";
import type { PerfNode } from "@/lib/perf-tree";

interface ScopeNoticesProps {
  scope: PerfNode;
  /** Live bots reporting no controllers — the broker diagnostic. */
  silentBots: BotSummary[];
  /** The run's ledger could not be read, so the figures are withheld. */
  runWithheld: boolean;
  runUnavailable: string;
  /** Nothing is deployed and no executor is open. */
  emptyPopulation: boolean;
  onDeploy: () => void;
}

/**
 * What an empty live population *means*, said before any number is
 * drawn (CORR-356). Without it a server that has never run a bot
 * reads as a measurement of zero: a strip of `$0.00` tiles and "No
 * performance history available", with a 10px muted "Nothing in
 * scope." in the sidebar as the only hint — and that line is
 * written for a different case (every filter ticked off, a window
 * with nothing in it) and is not drawn at all once the sidebar is
 * collapsed. So it goes in the report pane, which every reader has.
 *
 * The broker diagnostic wins where it applies, because telling an
 * outage apart from an empty fleet is the higher-value half, and it
 * is drawn *over* the numbers rather than instead of them: a silent
 * bot can sit beside a live unattached executor, and that executor's
 * money is real.
 */
export function ScopeNotices({
  scope,
  silentBots,
  runWithheld,
  runUnavailable,
  emptyPopulation,
  onDeploy,
}: ScopeNoticesProps) {
  return (
    <>
      {silentBots.length > 0 ? (
        <div className="shrink-0 rounded-lg border border-[var(--color-yellow)]/40 bg-[var(--color-yellow)]/10 px-4 py-3">
          <p className="text-sm font-medium text-[var(--color-yellow)]">
            {silentBots.length === 1
              ? `${silentBots[0].bot_name} is running but reporting no controllers`
              : `${silentBots.length} bots are running but reporting no controllers`}
          </p>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            Controller reports reach the API over its MQTT broker. Check that the broker is
            up and that the API is connected to it — on the server,{" "}
            <code className="font-mono">make doctor</code> names it.
          </p>
        </div>
      ) : runWithheld ? (
        // A run whose server could not be read filters to nothing, and a
        // fold of nothing is a strip of `$0.00` (CORR-433). Said here, in
        // the same idiom as an empty fleet, because the 10px line beside
        // the run chip is not where anyone looks for why the numbers are
        // zero — and the tiles below read "—" rather than a measurement.
        <div
          data-run-withheld
          className="shrink-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3"
        >
          <p className="text-sm font-medium">{runUnavailable}</p>
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">
            This run's ledger could not be read, so the figures below are withheld rather than a
            result — the run may well have traded.
          </p>
        </div>
      ) : emptyPopulation ? (
        <div
          className="flex shrink-0 items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2"
          title="Nothing is deployed on this server and no executor is open, so the figures below are an absence rather than a result. What has already run is under Terminated."
        >
          <p className="min-w-0 flex-1 truncate text-xs">
            <span className="font-medium">No bots running</span>
            <span className="text-[var(--color-text-muted)]"> · nothing deployed on this server</span>
          </p>
          {scope.kind === "fleet" && (
            <button
              onClick={onDeploy}
              className="flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-[var(--color-primary)]/40 px-2 text-xs font-medium text-[var(--color-primary)] transition-colors hover:bg-[var(--color-primary)]/10"
              title="Deploy a new bot"
            >
              <Rocket className="h-3.5 w-3.5" />
              Deploy bot
            </button>
          )}
        </div>
      ) : null}
    </>
  );
}
