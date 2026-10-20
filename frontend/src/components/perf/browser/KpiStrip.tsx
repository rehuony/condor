import type { ControllerInfo } from "@/lib/api/bots";
import { formatCurrencyPnl, formatCurrencyVolume, formatRuntimeHours, pnlColor } from "@/lib/formatters";
import { parseSide } from "@/lib/perf-positions";
import type { PerfTotals } from "@/lib/perf-tree";

import type { ScopeNoun } from "./ReportTitle";
import { plural } from "./text";

/**
 * One headline number and the line that qualifies it.
 *
 * The sub-line is what makes the row comparable across scopes: a fleet that has
 * been up eight hours and one that has been up eight days both report a total,
 * and only the rate says which is actually earning. Modelled on the KPI strip
 * of the `bot_report` routine.
 *
 * The sub-line's height is reserved even when there is nothing to say, so the
 * tiles' baselines line up whether or not a given one has a qualifier.
 */
function Kpi({
  label,
  value,
  sub,
  color,
  title,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
  /** What the number is a *reading of*, when that is not what it looks like. */
  title?: string;
}) {
  return (
    <div data-kpi={label} title={title} className="min-w-0">
      <div className="mb-0.5 truncate text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">{label}</div>
      <div className="truncate text-sm font-semibold leading-tight tabular-nums md:text-lg" style={color ? { color } : undefined}>
        {value}
      </div>
      <div className="mt-0.5 h-3.5 text-[10px] tabular-nums text-[var(--color-text-muted)] truncate" title={sub}>
        {sub ?? ""}
      </div>
    </div>
  );
}

interface KpiStripProps {
  totals: PerfTotals;
  currencySymbol: string;
  /** The run's ledger could not be read: every tile reads "—". */
  runWithheld: boolean;
  /** A total as a per-hour pace over the scope's runtime, or nothing under an hour. */
  perHour: (total: number, fmt: (v: number, symbol?: string) => string) => string | undefined;
  activeCtrl: ControllerInfo | undefined;
  scopeNoun: ScopeNoun;
}

/** Headline numbers first: the chart below is the shape of these. */
export function KpiStrip({ totals, currencySymbol, runWithheld, perHour, activeCtrl, scopeNoun }: KpiStripProps) {
  return (
    <div className="shrink-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2.5 md:px-4 md:py-3">
      {/* A fixed set of tiles, not a set that depends on what the scope
          happens to have. Two reasons. The strip's height decides the
          chart's, so tiles coming and going would resize the chart as
          the reader walks the sidebar. And a tile reading "—" is a
          fact worth having: a terminated scope holds no unrealized PnL,
          and a live one has not won or lost anything yet — which is
          different from those numbers being unavailable. */}
      {/* Reflowed by the *pane's* width, not the window's. `lg:` is a
          viewport breakpoint, so on a wide screen these stayed eight
          across however narrow the pane got — and opening the executor
          detail column takes ~480px off it, at which point eight
          columns of a hundred-odd pixels each printed
          "REALIZEDUNREALIZED" over one another. `auto-fit` asks the
          column the tiles are actually in. */}
      {/* Phones fix four columns: two short rows instead of a tall stack. */}
      <div className="grid grid-cols-4 gap-x-2 gap-y-2.5 md:grid-cols-[repeat(auto-fit,minmax(88px,1fr))] md:gap-3">
        <Kpi
          label="Net PnL"
          value={runWithheld ? "—" : formatCurrencyPnl(totals.net, currencySymbol)}
          color={runWithheld ? undefined : pnlColor(totals.net)}
          // The return % is per-leaf — each is measured against its own
          // notional — so it rides along only where it means something.
          // Summing or averaging them across a fold would report a
          // return nobody earned.
          sub={runWithheld ? undefined : [
            perHour(totals.net, formatCurrencyPnl),
            totals.returnPct !== undefined
              ? `${totals.returnPct >= 0 ? "+" : ""}${totals.returnPct.toFixed(2)}%`
              : undefined,
          ]
            .filter(Boolean)
            .join(" · ") || undefined}
        />
        <Kpi
          label="Realized"
          value={runWithheld ? "—" : formatCurrencyPnl(totals.realized, currencySymbol)}
          color={runWithheld ? undefined : pnlColor(totals.realized)}
          sub={runWithheld ? undefined : perHour(totals.realized, formatCurrencyPnl)}
        />
        <Kpi
          label="Unrealized"
          value={runWithheld ? "—" : formatCurrencyPnl(totals.unrealized, currencySymbol)}
          color={runWithheld ? undefined : pnlColor(totals.unrealized)}
          // What the unrealized figure is *of*: with no open position
          // there is nothing for it to be, and a stale non-zero reading
          // beside "no open positions" is worth seeing.
          sub={
            runWithheld
              ? undefined
              : totals.positions === 0
              ? "no open positions"
              : `${totals.positions} position${totals.positions !== 1 ? "s" : ""}`
          }
        />
        {/* Win rate is only meaningful over what has *closed*, so the
            count beside it is the denominator rather than decoration:
            a fold of live controllers has closed nothing and says so,
            and a mixed fold quotes the closed subset it was measured
            over rather than pretending the open ones lost. */}
        <Kpi
          label="Win rate"
          value={
            runWithheld || totals.winRate === undefined
              ? "—"
              : `${(totals.winRate * 100).toFixed(1)}%`
          }
          sub={
            runWithheld
              ? undefined
              : totals.closed > 0
              ? `${totals.wins.toLocaleString()} of ${totals.closed.toLocaleString()} closed`
              : "nothing closed yet"
          }
        />
        <Kpi
          label="Volume"
          value={runWithheld ? "—" : formatCurrencyVolume(totals.volume, currencySymbol)}
          sub={runWithheld ? undefined : perHour(totals.volume, formatCurrencyVolume)}
        />
        <Kpi
          label="Fees"
          value={
            !runWithheld && totals.fees > 0 ? formatCurrencyVolume(totals.fees, currencySymbol) : "—"
          }
          // A floor and not a total: `leafFromController` hardcodes
          // `fees: 0` because the controllers payload reports no fee
          // total of its own, so what is measurable here is the
          // executors' fees. The caption rides on the tile rather than
          // the number gaining a footnote — a figure that has to be
          // read with an asterisk is a figure with an asterisk in it.
          title="Executor fees only — the controllers payload reports no fee total, so this is a floor, not a total."
          // What the fees ate: the share of gross the venue took, which
          // is the thing an absolute fee total cannot say on its own.
          sub={
            !runWithheld && totals.fees > 0 && totals.net + totals.fees !== 0
              ? `${((totals.fees / Math.abs(totals.net + totals.fees)) * 100).toFixed(1)}% of gross`
              : undefined
          }
        />
        <Kpi
          label="Capital"
          value={
            !runWithheld && totals.capital > 0
              ? formatCurrencyVolume(totals.capital, currencySymbol)
              : "—"
          }
          // Turnover rather than a controller count: how hard the
          // capital is working is the thing the two numbers beside it
          // do not already say, and the count is on the Runtime tile.
          sub={
            !runWithheld && totals.capital > 0
              ? `${(totals.volume / totals.capital).toFixed(1)}x turnover`
              : undefined
          }
        />
        <Kpi
          label="Runtime"
          value={totals.hours > 0 ? formatRuntimeHours(totals.hours) : "—"}
          // Named in hours, not "2d 9h", because it is the divisor of
          // every per-hour figure in this row — the reader can check the
          // pace against the total without converting anything. Under
          // the hour it is minutes: `0.1h` is not a number anyone can
          // check a pace against (see `formatRuntimeHours`).
          sub={
            activeCtrl
              ? activeCtrl.bot_name
              : `${plural(totals.bots, "bot")}, ${plural(totals.count, scopeNoun)}`
          }
        />
      </div>

      {/* ── How the positions ended (FEAT-085) ──

          Inside the same card as the tiles, because it is the same kind
          of fact at a different cardinality: the tiles are what this
          scope adds up to, and these are what it adds up to broken out
          by close type. They cannot *be* tiles — the fixed six-column
          grid above breaks the moment a fleet shows seven distinct
          types — and they were not worth the third of the pane the
          bottom band used to spend on three short chips.

          One row that scrolls sideways rather than wrapping, and that
          is load-bearing: the chart is sized from what this strip
          leaves, so a strip whose height depended on how many close
          types a scope happens to have would resize the chart as the
          reader walked the sidebar. A scope with none drops the row
          entirely — a fleet of LP controllers should not pay for a line
          that says nothing. */}
      {totals.closeTypes.length > 0 && (
        <div
          data-close-types
          className="mt-3 flex items-center gap-1.5 overflow-x-auto scrollbar-thin border-t border-[var(--color-border)]/60 pt-2"
        >
          <span className="shrink-0 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
            Closes{" "}
            <span className="font-semibold tabular-nums text-[var(--color-text)]">
              {totals.closeTotal.toLocaleString()}
            </span>
          </span>
          {totals.closeTypes.map(([type, count]) => (
            <span
              key={type}
              className="inline-flex shrink-0 items-center gap-1 rounded-md bg-[var(--color-bg)] px-2 py-0.5 text-[11px] border border-[var(--color-border)]/50"
            >
              <span className="text-[var(--color-text-muted)]">{parseSide(type)}</span>
              <span className="font-semibold tabular-nums">{count}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
