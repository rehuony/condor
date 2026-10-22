import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import type { SetURLSearchParams } from "react-router-dom";

import { ControllerPnlChart } from "@/components/bots/ControllerPnlChart";
import { PnlEvolutionChart } from "@/components/bots/PnlEvolutionChart";
import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { OwnerPnlChart } from "@/components/perf/OwnerPnlChart";
import { ownerNoun, type ScopeOwner } from "@/components/perf/scopeOwners";
import { Spinner } from "@/components/ui/Spinner";
import type { ControllerInfo } from "@/lib/api/bots";
import type { ExecutorInfo } from "@/lib/api/executors";
import { controllerKey } from "@/lib/controller-identity";
import type { FloorChartRow } from "@/lib/owner-series";
import type { PerfNotice } from "@/lib/perf-notices";
import type { PerfNode, PerfTotals, Population } from "@/lib/perf-tree";
import type { PnlChartPoint } from "@/lib/pnl-chart";
import type { ConvertFn } from "@/lib/rates";

// ── Chart sizing ──

/**
 * The chart card's fixed height. A height that followed the window made the
 * chart a sliver on a laptop and a wall on a large monitor, and changed it
 * whenever a band opened; one height reads the same everywhere. The empty and
 * loading placeholders take the same box, so data arriving never resizes it.
 */
const CHART_BOX_PX = 480;
const MIN_CHART_PX = 220;

/**
 * Everything in a chart card that is not one of its panes — the header strip,
 * pane captions and range strip — before it has been measured. The panes get
 * the box less this; `useCardPanes` replaces it with the card's real chrome.
 */
const CHART_CHROME_PX = 152;

/**
 * The same estimate for `ExecutorChart`, whose chrome is one 30px header bar
 * and a border — it draws no title, no legend and no range buttons of its own.
 */
const EXEC_CHART_CHROME_PX = 34;

/**
 * The last chrome measured per card kind, kept across mounts: a chart opened
 * again starts at its real size instead of the estimate and a correction.
 */
const measuredChrome = new Map<string, number>();

/**
 * The panes' height for the card drawn in `ref`: the fixed box less the card's
 * real chrome, its rendered height minus the panes. The chrome is stable once
 * measured, since panes and card grow together, and is kept per `kind`,
 * because the executor chart and the PnL cards carry different chrome.
 *
 * `settled` is false until this kind has been measured once in the session.
 * The card stays invisible until then, so the estimate's size is never shown
 * and corrected a frame later.
 */
function useCardPanes(kind: string | null, estimate: number) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [chrome, setChrome] = useState<{ kind: string; px: number } | null>(null);
  const cached = kind ? measuredChrome.get(kind) : undefined;
  const chromePx = chrome?.kind === kind ? chrome.px : (cached ?? estimate);
  const settled = chrome?.kind === kind || cached !== undefined;
  const paneHeight = Math.max(MIN_CHART_PX, CHART_BOX_PX - chromePx);
  useEffect(() => {
    const el = ref.current;
    if (!el || !kind || typeof ResizeObserver === "undefined") return;
    // The observer reports the first size on its own, before the browser
    // paints, and flushSync applies it in that same frame.
    const ro = new ResizeObserver(() => {
      const px = Math.round(el.getBoundingClientRect().height) - paneHeight;
      if (px < 0) return;
      measuredChrome.set(kind, px);
      if (chrome?.kind === kind && Math.abs(px - chromePx) <= 2) return;
      flushSync(() => setChrome({ kind, px }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [kind, paneHeight, chromePx, chrome]);
  return [ref, paneHeight, settled] as const;
}

interface ReportChartProps {
  scope: PerfNode;
  population: Population;
  server: string;
  activeCtrl: ControllerInfo | undefined;
  activeExec: ExecutorInfo | undefined;
  /** The controller id the server knows the active controller by. */
  configId: string;
  convert: ConvertFn;
  currencySymbol: string;
  chartTitle: string;
  /** Which picture a single executor tells its story with. */
  execChart: "price" | "pnl";
  execHasPrice: boolean;
  /** The executor and its siblings on the same pair, for the candle overlay. */
  execChartGroup: ExecutorInfo[];
  /** A Total plus one line per child, when this scope splits (FEAT-116). */
  ownerChart: { rows: FloorChartRow[]; keys: string[] } | null;
  ownerLines: ScopeOwner[];
  totals: PerfTotals;
  chartData: PnlChartPoint[];
  notice: PerfNotice | undefined;
  /** The fleet data behind the chart is still on its first load. */
  historyLoading: boolean;
  runHistoryLoading: boolean;
  /** The run predates stored history; only its archive has a curve. */
  archiveOnlyController: boolean;
  searchParams: URLSearchParams;
  setSearchParams: SetURLSearchParams;
}

/**
 * The chart: one executor's candles or PnL, one live controller's own series,
 * a split by child, or the aggregate fold — whichever the scope supports.
 *
 * The chart sits in a fixed `CHART_BOX_PX` box, whatever the window size,
 * whether a band is open and whether there is data yet. Its panes get the box
 * less the card's chrome, never below `MIN_CHART_PX`; `overflow-hidden` clips
 * a card that would still not fit instead of letting it cover the band below.
 */
export function ReportChart({
  scope,
  population,
  server,
  activeCtrl,
  activeExec,
  configId,
  convert,
  currencySymbol,
  chartTitle,
  execChart,
  execHasPrice,
  execChartGroup,
  ownerChart,
  ownerLines,
  totals,
  chartData,
  notice,
  runHistoryLoading,
  historyLoading,
  archiveOnlyController,
  searchParams,
  setSearchParams,
}: ReportChartProps) {
  // Which card is drawn: the placeholders below fill the box by design, so
  // only a real chart has chrome worth measuring.
  const kind = activeExec && execHasPrice && execChart === "price"
    ? "exec"
    : (activeCtrl && population === "running") || ownerChart || chartData.length >= 2
      ? "report"
      : null;
  const [cardRef, paneHeight, settled] = useCardPanes(
    kind,
    kind === "exec" ? EXEC_CHART_CHROME_PX : CHART_CHROME_PX,
  );
  return (
    <div className="relative shrink-0 overflow-hidden" style={{ height: CHART_BOX_PX }}>
      <div className="absolute inset-0">
        {kind && (
          <div ref={cardRef} className={settled ? undefined : "invisible"}>
            {activeExec && execHasPrice && execChart === "price" ? (
              /* One executor selected, drawn the way the executors page
                 drew it: the pair's candles with this executor's entry,
                 exit, barriers and — for a grid — its levels laid over
                 them. The aggregate panes below are right for a fold of
                 many records and say almost nothing about a single one,
                 whose whole story is where it opened and where it closed.

                 Keyed by executor id so switching between two of them
                 remounts rather than re-pointing a chart that keeps the
                 previous one's visible range. */
              <ExecutorChart
                key={activeExec.id}
                server={server}
                executors={execChartGroup}
                connector={activeExec.connector}
                tradingPair={activeExec.trading_pair}
                height={paneHeight}
              />
            ) : activeCtrl && population === "running" ? (
              <ControllerPnlChart
                // Keyed by bot + config id: two bots can be running this very
                // config, and a key that did not tell them apart would keep the
                // sibling's mounted state when the user switches (CORR-241).
                key={controllerKey(activeCtrl)}
                server={server}
                controllerId={configId}
                botName={activeCtrl.bot_name}
                deployedAt={activeCtrl.deployed_at}
                height={paneHeight}
                convert={convert}
                currencySymbol={currencySymbol}
                controller={activeCtrl}
              />
            ) : ownerChart ? (
              /* This scope splits, so the report is the split: a Total plus
                 one line per child, in the tree's own colours and order.
                 Before the aggregate series below rather than instead of
                 it — a scope with one child, a controller or an executor
                 draws the single curve it always drew (FEAT-116). */
              <OwnerPnlChart
                owners={ownerLines}
                rows={ownerChart.rows}
                keys={ownerChart.keys}
                symbol={currencySymbol}
                net={totals.net}
                capital={totals.capital}
                // The header two lines up already names the scope, so
                // repeating a 40-character bot name here only crowded the
                // legend out of its row: this title says what the *split*
                // is. The fleet has no such header, so it keeps its name.
                title={
                  scope.kind === "fleet"
                    ? `${chartTitle} by ${ownerNoun(scope.children[0].kind)}`
                    : `PnL by ${ownerNoun(scope.children[0].kind)}`
                }
                height={paneHeight}
                params={searchParams}
                setParams={setSearchParams}
              />
            ) : chartData.length >= 2 ? (
              <PnlEvolutionChart
                data={chartData}
                title={chartTitle}
                pnlHeight={Math.round(paneHeight * 0.65)}
                volumeHeight={paneHeight - Math.round(paneHeight * 0.65)}
                currencySymbol={currencySymbol}
                notice={notice}
              />
            ) : null}
          </div>
        )}
        {!kind && (historyLoading ? (
          // The first load of the data behind the chart: an empty card in the
          // chart's box, so nothing flashes "No performance history" first.
          <div className="h-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]" />
        ) : runHistoryLoading ? (
          // The first open of a cold run pays one walk. Behind the same
          // spinner a cold archive already shows, and never blocking the
          // tree: the sidebar and the strip are drawn from the
          // controllers listing whether or not this has landed.
          <div className="flex h-full items-center justify-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
            <Spinner tone="muted" />
            <p className="text-xs text-[var(--color-text-muted)]">Reading this run's history…</p>
          </div>
        ) : (
          <div className="flex h-full items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-6">
            <p className="max-w-sm text-center text-xs text-[var(--color-text-muted)]">
              {archiveOnlyController
                ? "This run predates the server's stored performance history. Its archived database records the run as a whole rather than per controller — select the bot above to see that curve."
                : "No performance history available"}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
