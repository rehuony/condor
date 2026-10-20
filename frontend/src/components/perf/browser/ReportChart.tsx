import { useEffect, useRef, useState } from "react";
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
 * Everything in the chart *card* that is not one of the two panes: the header
 * strip, the two pane captions and the range strip below them. The panes are
 * sized from the space that is left, so the card fills its column exactly
 * instead of being a fixed 200px island in a screen-tall pane.
 *
 * It covers the card and nothing outside it. The strip above and the positions
 * band below are siblings of the measured element, not part of it — the chart
 * sits in a `flex-1` box between them, so growing the strip (the close-type
 * chips, FEAT-085) or opening the band shrinks that box, the ResizeObserver
 * below reports the new height, and the panes follow. Adding their heights here
 * as well would subtract them twice.
 */
const CHART_CHROME_PX = 152;
const MIN_CHART_PX = 220;

/**
 * The same subtraction for `ExecutorChart`, whose chrome is one 30px header bar
 * and a border — it draws no title, no legend and no range buttons of its own,
 * so charging it the PnL chart's allowance would waste a fifth of the box.
 */
const EXEC_CHART_CHROME_PX = 34;

function useMeasuredHeight<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [height, setHeight] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const next = Math.round(entries[0].contentRect.height);
      setHeight((prev) => (Math.abs(prev - next) > 2 ? next : prev));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, height] as const;
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
 * The chart takes whatever vertical room is left, down to the room
 * it actually occupies and no further: the panes are drawn at
 * `MIN_CHART_PX` however little is left, so a box shorter than that
 * plus its chrome does not shrink the chart — it makes the chart
 * overflow downward and paint across the band's header, which is
 * what swallowed the "Positions held" label when a twelve-row fleet
 * opened the band. The floor is the same two constants the panes are
 * sized from, so the two can't drift apart. `overflow-hidden` is the
 * belt to that braces: if a pane ever is squeezed below its floor,
 * it clips itself instead of covering its neighbour.
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
  archiveOnlyController,
  searchParams,
  setSearchParams,
}: ReportChartProps) {
  const [chartRef, chartBoxH] = useMeasuredHeight<HTMLDivElement>();
  const chartHeight = Math.max(MIN_CHART_PX, (chartBoxH || 420) - CHART_CHROME_PX);
  const execChartHeight = Math.max(MIN_CHART_PX, (chartBoxH || 420) - EXEC_CHART_CHROME_PX);
  return (
    <div
      ref={chartRef}
      className="relative flex-1 overflow-hidden narrow-pane:shrink-0"
      style={{ minHeight: MIN_CHART_PX + CHART_CHROME_PX }}
    >
      <div className="absolute inset-0">
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
            height={execChartHeight}
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
            height={chartHeight}
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
            height={chartHeight}
            params={searchParams}
            setParams={setSearchParams}
          />
        ) : chartData.length >= 2 ? (
          <PnlEvolutionChart
            data={chartData}
            title={chartTitle}
            pnlHeight={Math.round(chartHeight * 0.65)}
            volumeHeight={chartHeight - Math.round(chartHeight * 0.65)}
            currencySymbol={currencySymbol}
            notice={notice}
          />
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
        )}
      </div>
    </div>
  );
}
